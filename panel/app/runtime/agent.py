"""Runtime and Files that talk to the Go agent (agent/) instead of Docker.

Same interface as app.runtime.docker / docker_files, so the manager and the API don't care which one
they got. The agent is the only component with access to Docker; the panel only has its URL and token.
"""

import asyncio
import base64
import io
import json
import tarfile
import tempfile
import time
from collections.abc import AsyncIterator
from dataclasses import asdict
from pathlib import Path

import httpx

from app.runtime.files import FileEntry, FileError, SearchMatch, Upload, resolve, validate_name
from app.runtime.spec import ContainerSpec
from app.runtime.state import ContainerState, LogFn, RuntimeUnavailable, parse_docker_time


class AgentError(RuntimeUnavailable):
    """The agent is unreachable, or Docker behind it failed."""


def _state(data: dict) -> ContainerState:
    return ContainerState(
        status=data["status"],
        health=data.get("health"),
        exit_code=data.get("exit_code"),
        oom_killed=data.get("oom_killed", False),
        started_at=parse_docker_time(data["started_at"]) if data.get("started_at") else None,
    )


def _spec_json(spec: ContainerSpec, script: bytes | None = None) -> dict:
    data = asdict(spec)
    data.pop("script")
    data["script"] = base64.b64encode(script).decode() if script else None
    return data


class AgentClient:
    def __init__(self, url: str, token: str):
        self.http = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            # Streams (logs, installs, backups) last as long as they last; only connecting times out.
            timeout=httpx.Timeout(None, connect=10),
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            resp = await self.http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise AgentError(f"agent unreachable: {exc}") from exc
        await self.raise_for_status(resp)
        return resp

    @staticmethod
    async def raise_for_status(resp: httpx.Response) -> None:
        if resp.status_code < 400:
            return
        await resp.aread()
        try:
            message = resp.json().get("error") or resp.text
        except ValueError:
            message = resp.text
        if resp.status_code in (400, 404, 409, 413, 415, 422):
            raise FileError(message, resp.status_code)
        if resp.status_code == 401:
            raise AgentError("the agent rejected the panel's token: POSSUM_AGENT_TOKEN differs between them")
        raise AgentError(message)

    async def ndjson(self, path: str, body: dict, on_log: LogFn) -> None:
        """POST that streams {"log": ...} lines and ends with {"done": true} or {"error": ...}."""
        try:
            async with self.http.stream("POST", path, json=body) as resp:
                await self.raise_for_status(resp)
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    event = json.loads(line)
                    if "log" in event:
                        on_log(event["log"])
                    elif "error" in event:
                        raise RuntimeError(event["error"])
                    elif event.get("done"):
                        return
        except httpx.HTTPError as exc:
            raise AgentError(f"agent connection lost: {exc}") from exc
        raise AgentError("the agent ended the stream without a result")


class AgentRuntime:
    def __init__(self, url: str, token: str):
        self.agent = AgentClient(url, token)
        self.files = AgentFiles(self.agent)

    async def _call(self, method: str, path: str, not_found_ok: bool = False, **kwargs) -> httpx.Response:
        """Container calls: a 4xx from the agent is a runtime problem here, not a file one.

        not_found_ok: a 404 ("no such container/file") is an answer the caller handles.
        """
        try:
            return await self.agent.request(method, path, **kwargs)
        except FileError as exc:
            if exc.status == 404 and not_found_ok:
                raise
            raise AgentError(str(exc)) from exc

    async def close(self) -> None:
        await self.agent.close()

    async def states(self) -> dict[str, ContainerState]:
        data = (await self._call("GET", "/v1/servers")).json()
        return {sid: _state(s) for sid, s in data.items()}

    async def state(self, server_id: str) -> ContainerState | None:
        try:
            data = (await self._call("GET", f"/v1/servers/{server_id}", not_found_ok=True)).json()
        except FileError as exc:
            if exc.status == 404:
                return None
            raise
        return _state(data)

    async def published_ports(self) -> set[tuple[int, str]]:
        return {(port, proto) for port, proto in (await self._call("GET", "/v1/ports")).json()}

    async def pull(self, image: str, on_log: LogFn) -> None:
        await self.agent.ndjson("/v1/images/pull", {"image": image}, on_log)

    async def ensure_volume(self, server_id: str) -> str:
        await self._call("POST", f"/v1/servers/{server_id}/volume")
        return f"possum-{server_id}-data"

    async def run_install(self, server_id: str, spec: ContainerSpec, on_log: LogFn) -> None:
        script = await asyncio.to_thread(spec.script.read_bytes) if spec.script else None
        await self.agent.ndjson(f"/v1/servers/{server_id}/install", _spec_json(spec, script), on_log)

    async def create(self, server_id: str, spec: ContainerSpec) -> None:
        await self._call("PUT", f"/v1/servers/{server_id}/container", json=_spec_json(spec))

    async def start(self, server_id: str) -> None:
        await self._call("POST", f"/v1/servers/{server_id}/start")

    async def stop(self, server_id: str, command: str | None, timeout: int) -> None:
        await self._call(
            "POST", f"/v1/servers/{server_id}/stop", json={"command": command or "", "timeout": timeout}
        )

    async def send_command(self, server_id: str, line: str) -> None:
        await self._call("POST", f"/v1/servers/{server_id}/command", json={"line": line})

    async def read_file(self, server_id: str, path: str) -> bytes | None:
        try:
            resp = await self._call(
                "GET", f"/v1/servers/{server_id}/container-file", not_found_ok=True, params={"path": path}
            )
        except FileError as exc:
            if exc.status == 404:
                return None
            raise
        return resp.content

    async def write_file(self, server_id: str, path: str, data: bytes) -> None:
        await self._call(
            "PUT", f"/v1/servers/{server_id}/container-file", params={"path": path}, content=data
        )

    async def logs(
        self, server_id: str, tail: int | None = 200, since: int = 0, timestamps: bool = False
    ) -> AsyncIterator[str]:
        params = {"tail": "all" if tail is None else str(tail), "since": str(since)}
        if timestamps:
            params["timestamps"] = "true"
        try:
            async with self.agent.http.stream("GET", f"/v1/servers/{server_id}/logs", params=params) as resp:
                await self.agent.raise_for_status(resp)
                async for line in resp.aiter_lines():
                    yield line + "\n"
        except httpx.HTTPError as exc:
            raise AgentError(f"log stream lost: {exc}") from exc

    async def remove(self, server_id: str) -> None:
        await self._call("DELETE", f"/v1/servers/{server_id}")


class AgentFiles:
    def __init__(self, agent: AgentClient):
        self.agent = agent

    def _url(self, server_id: str, suffix: str = "") -> str:
        return f"/v1/servers/{server_id}/files{suffix}"

    async def _post(self, server_id: str, suffix: str, body: dict) -> httpx.Response:
        return await self.agent.request("POST", self._url(server_id, suffix), json=body)

    async def stat(self, server_id: str, path: str) -> FileEntry | None:
        try:
            data = (
                await self.agent.request("GET", self._url(server_id, "/stat"), params={"path": path})
            ).json()
        except FileError as exc:
            if exc.status == 404:
                return None
            raise
        return FileEntry(**data)

    async def list(self, server_id: str, path: str) -> list[FileEntry]:
        data = (await self.agent.request("GET", self._url(server_id), params={"path": path})).json()
        return [FileEntry(**entry) for entry in data]

    async def search(self, server_id: str, path: str, query: str) -> tuple[list[SearchMatch], bool]:
        data = (
            await self.agent.request(
                "GET", self._url(server_id, "/search"), params={"path": path, "q": query}
            )
        ).json()
        matches = [
            SearchMatch(**{k: m[k] for k in ("path", "type", "size", "mtime")}) for m in data["matches"]
        ]
        return matches, data["truncated"]

    async def mkdir(self, server_id: str, path: str) -> None:
        await self._post(server_id, "/mkdir", {"path": path})

    async def ensure_dirs(self, server_id: str, paths: list[str]) -> None:
        await self._post(server_id, "/ensure-dirs", {"paths": paths})

    async def rename(self, server_id: str, path: str, new_name: str) -> None:
        await self._post(server_id, "/rename", {"path": path, "name": new_name})

    async def move(self, server_id: str, sources: list[str], destination: str) -> None:
        await self._post(server_id, "/move", {"sources": sources, "destination": destination})

    async def copy(self, server_id: str, sources: list[str], destination: str) -> None:
        await self._post(server_id, "/copy", {"sources": sources, "destination": destination})

    async def delete(self, server_id: str, paths: list[str]) -> None:
        await self._post(server_id, "/delete", {"paths": paths})

    async def usage(self, server_id: str, paths: list[str]) -> int:
        return (await self._post(server_id, "/usage", {"paths": paths})).json()["bytes"]

    async def unpacked_size(self, server_id: str, path: str) -> int:
        response = await self.agent.request(
            "GET", self._url(server_id, "/unpacked-size"), params={"path": path}
        )
        return response.json()["bytes"]

    async def extract(self, server_id: str, path: str) -> str:
        return (await self._post(server_id, "/extract", {"path": path})).json()["path"]

    async def read_text(self, server_id: str, path: str) -> str:
        resp = await self.agent.request("GET", self._url(server_id, "/text"), params={"path": path})
        return resp.json()["content"]

    async def write_text(self, server_id: str, path: str, content: str) -> None:
        await self.agent.request(
            "PUT", self._url(server_id, "/text"), json={"path": path, "content": content}
        )

    async def upload(self, server_id: str, directory: str, uploads: list[Upload]) -> None:
        # A tar of the uploads, spooled to disk past 32 MB; the agent sets owners and checks names again.
        with tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024) as archive:
            await asyncio.to_thread(_build_upload_tar, archive, uploads)
            archive.seek(0)
            await self.agent.request(
                "POST",
                self._url(server_id, "/upload"),
                params={"directory": directory},
                content=_file_chunks(archive),
            )

    async def download(self, server_id: str, path: str) -> AsyncIterator[bytes]:
        async for chunk in self._stream(self._url(server_id, "/download"), {"path": path}):
            yield chunk

    async def download_archive(
        self, server_id: str, directory: str, names: list[str]
    ) -> AsyncIterator[bytes]:
        params = [("path", directory)] + [("name", n) for n in names]
        async for chunk in self._stream(self._url(server_id, "/archive"), params):
            yield chunk

    async def _stream(self, url: str, params) -> AsyncIterator[bytes]:
        try:
            async with self.agent.http.stream("GET", url, params=params) as resp:
                await self.agent.raise_for_status(resp)
                async for chunk in resp.aiter_bytes():
                    yield chunk
        except httpx.HTTPError as exc:
            raise AgentError(f"download interrupted: {exc}") from exc

    async def archive_to(
        self, server_id: str, target: Path, paths: list[str] | None, exclude: list[str]
    ) -> tuple[int, list[str] | None]:
        size = 0
        try:
            async with self.agent.http.stream(
                "POST", self._url(server_id, "/backup"), json={"paths": paths, "exclude": exclude}
            ) as resp:
                await self.agent.raise_for_status(resp)
                present = json.loads(resp.headers.get("x-backup-paths", "null"))
                handle = await asyncio.to_thread(open, target, "wb")
                try:
                    async for chunk in resp.aiter_bytes():
                        await asyncio.to_thread(handle.write, chunk)
                        size += len(chunk)
                finally:
                    await asyncio.to_thread(handle.close)
        except httpx.HTTPError as exc:
            # The agent drops the connection when tar fails half-way: never keep a cut-off archive.
            raise FileError(f"the backup was interrupted: {exc}", 502) from exc
        return size, present

    async def restore_from(
        self, server_id: str, source: Path, paths: list[str] | None, exclude: list[str]
    ) -> None:
        handle = await asyncio.to_thread(open, source, "rb")
        try:
            await self.agent.request(
                "PUT",
                self._url(server_id, "/restore"),
                params={"paths": json.dumps(paths), "exclude": json.dumps(exclude)},
                content=_file_chunks(handle),
            )
        finally:
            await asyncio.to_thread(handle.close)

    async def close(self, server_id: str) -> None:
        await self.agent.request("DELETE", self._url(server_id, "/helper"))


async def _file_chunks(handle, size: int = 256 * 1024) -> AsyncIterator[bytes]:
    while chunk := await asyncio.to_thread(handle.read, size):
        yield chunk


def _build_upload_tar(fileobj, uploads: list[Upload]) -> None:
    folders: set[str] = set()
    now = int(time.time())
    with tarfile.open(fileobj=fileobj, mode="w") as tar:
        for upload in uploads:
            rel = resolve(upload.path)
            parts = rel.split("/")
            if not rel or any(validate_name(p) != p for p in parts):
                raise FileError(f"invalid upload path '{upload.path}'")
            # Explicit folder entries, otherwise Docker creates missing parents owned by root.
            for i in range(1, len(parts)):
                folder = "/".join(parts[:i])
                if folder not in folders:
                    folders.add(folder)
                    info = tarfile.TarInfo(folder)
                    info.type, info.mode, info.mtime = tarfile.DIRTYPE, 0o755, now
                    tar.addfile(info)
            upload.file.seek(0, io.SEEK_END)
            info = tarfile.TarInfo(rel)
            info.size, info.mtime = upload.file.tell(), now
            upload.file.seek(0)
            tar.addfile(info, upload.file)
