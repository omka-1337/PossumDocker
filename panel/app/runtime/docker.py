"""Talks to the Docker Engine API.

Temporary: before the first release this moves into the Go agent, and the panel
talks to the agent instead. Keep everything Docker-specific inside this module.
"""

import asyncio
import io
import json
import logging
import posixpath
import re
import tarfile
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Literal

import aiodocker
import aiohttp
from aiodocker.exceptions import DockerError

from app.runtime.spec import ContainerSpec

log = logging.getLogger(__name__)

LABEL_MANAGED = "dgs.managed"
LABEL_SERVER = "dgs.server_id"
LABEL_ROLE = "dgs.role"

LogFn = Callable[[str], None]


class RuntimeUnavailable(Exception):
    """Docker can't be reached (not running, no access to the socket...)."""


@dataclass(frozen=True)
class ContainerState:
    status: Literal["created", "running", "paused", "restarting", "removing", "exited", "dead"]
    # "starting" | "healthy" | "unhealthy" when the image has a healthcheck, else None.
    health: str | None = None


def container_name(server_id: str) -> str:
    return f"dgs-{server_id}"


def volume_name(server_id: str) -> str:
    return f"dgs-{server_id}-data"


def _labels(server_id: str, role: str) -> dict[str, str]:
    return {LABEL_MANAGED: "true", LABEL_SERVER: server_id, LABEL_ROLE: role}


def _health_from_status(status_text: str) -> str | None:
    # `docker ps` status text: "Up 5 minutes (healthy)", "Up 3 seconds (health: starting)"
    match = re.search(r"\((?:health: )?(starting|healthy|unhealthy)\)", status_text)
    return match.group(1) if match else None


class DockerRuntime:
    def __init__(self, docker: aiodocker.Docker | None = None):
        self._docker = docker or aiodocker.Docker()

    async def close(self) -> None:
        await self._docker.close()

    # --- queries -------------------------------------------------------------

    async def states(self) -> dict[str, ContainerState]:
        """State of every runtime container, keyed by server id, in one Docker call."""
        filters = json.dumps({"label": [f"{LABEL_MANAGED}=true", f"{LABEL_ROLE}=runtime"]})
        try:
            containers = await self._docker.containers.list(all="true", filters=filters)
        except (DockerError, aiohttp.ClientError, OSError) as exc:
            raise RuntimeUnavailable(str(exc)) from exc

        states = {}
        for container in containers:
            info = container._container
            server_id = info.get("Labels", {}).get(LABEL_SERVER)
            if server_id:
                states[server_id] = ContainerState(
                    status=info["State"], health=_health_from_status(info.get("Status", ""))
                )
        return states

    async def state(self, server_id: str) -> ContainerState | None:
        try:
            info = await self._docker.containers.get(container_name(server_id))
        except DockerError as exc:
            if exc.status == 404:
                return None
            raise
        state = info["State"]
        return ContainerState(status=state["Status"], health=(state.get("Health") or {}).get("Status"))

    # --- install -------------------------------------------------------------

    async def pull(self, image: str, on_log: LogFn) -> None:
        on_log(f"pulling {image}")
        layers_done = 0
        async for event in self._docker.images.pull(image, stream=True):
            if "error" in event:
                raise RuntimeError(event["error"])
            # Per-layer progress is too chatty for a log; report finished layers only.
            if event.get("status") in ("Pull complete", "Already exists"):
                layers_done += 1
            elif event.get("status", "").startswith(("Digest:", "Status:")):
                on_log(event["status"])
        on_log(f"{image}: {layers_done} layer(s) ready")

    async def ensure_volume(self, server_id: str) -> str:
        name = volume_name(server_id)
        await self._docker.volumes.create({"Name": name, "Labels": _labels(server_id, "data")})
        return name

    async def run_install(self, server_id: str, spec: ContainerSpec, on_log: LogFn) -> None:
        """Run the install container to completion. Raises if it exits with a non-zero code."""
        name = f"{container_name(server_id)}-install"
        await self._remove_container(name)

        binds = [f"{volume_name(server_id)}:{spec.data_path}"]
        if spec.script:
            binds.append(f"{spec.script}:/dgs/install.sh:ro")
        container = await self._docker.containers.create(
            {
                **self._base_config(spec, server_id, "install"),
                "WorkingDir": spec.data_path,
                "HostConfig": {"Binds": binds},
            },
            name=name,
        )
        try:
            await container.start()
            async for line in container.log(stdout=True, stderr=True, follow=True):
                on_log(line.rstrip("\n"))
            result = await container.wait()
            if result["StatusCode"] != 0:
                raise RuntimeError(f"install step exited with code {result['StatusCode']}")
        finally:
            await self._remove_container(name)

    async def create(self, server_id: str, spec: ContainerSpec) -> None:
        """(Re)create the runtime container. Data lives in the volume, so this is safe."""
        name = container_name(server_id)
        await self._remove_container(name)

        port_key = lambda p: f"{p.container}/{p.protocol}"  # noqa: E731
        await self._docker.containers.create(
            {
                **self._base_config(spec, server_id, "runtime"),
                # stdin stays open so the panel can type commands into the server console.
                "OpenStdin": True,
                "StdinOnce": False,
                "Tty": False,
                "ExposedPorts": {port_key(p): {} for p in spec.ports},
                "HostConfig": {
                    "Binds": [f"{volume_name(server_id)}:{spec.data_path}"],
                    "PortBindings": {port_key(p): [{"HostPort": str(p.host)}] for p in spec.ports},
                    "RestartPolicy": {"Name": "no"},
                },
            },
            name=name,
        )

    def _base_config(self, spec: ContainerSpec, server_id: str, role: str) -> dict:
        config: dict = {
            "Image": spec.image,
            "Env": [f"{k}={v}" for k, v in spec.env.items()],
            "Labels": _labels(server_id, role),
        }
        if spec.entrypoint is not None:
            config["Entrypoint"] = spec.entrypoint
        if spec.command is not None:
            config["Cmd"] = spec.command
        return config

    # --- lifecycle -----------------------------------------------------------

    async def start(self, server_id: str) -> None:
        container = await self._docker.containers.get(container_name(server_id))
        await container.start()

    async def stop(self, server_id: str, command: str | None, timeout: int) -> None:
        """Graceful stop: console command (if the game has one), then SIGTERM, then SIGKILL."""
        container = await self._docker.containers.get(container_name(server_id))
        if command:
            await self.send_command(server_id, command)
            try:
                await asyncio.wait_for(container.wait(), timeout)
                return
            except TimeoutError:
                log.warning("server %s ignored '%s', sending SIGTERM", server_id, command)
        await container.stop(t=timeout)

    async def send_command(self, server_id: str, line: str) -> None:
        container = await self._docker.containers.get(container_name(server_id))
        stream = container.attach(stdin=True)
        try:
            await stream.write_in(line.encode() + b"\n")
        finally:
            # Closing our attach doesn't close the container's stdin (StdinOnce is false).
            await stream.close()

    async def logs(self, server_id: str, tail: int | None = 200, since: int = 0) -> AsyncIterator[str]:
        """Follow the console output. Ends when the container stops. Nothing if it doesn't exist."""
        try:
            container = await self._docker.containers.get(container_name(server_id))
        except DockerError as exc:
            if exc.status == 404:
                return
            raise
        params = {"tail": "all" if tail is None else str(tail), "since": since}
        async for line in container.log(stdout=True, stderr=True, follow=True, **params):
            yield line

    # --- files ---------------------------------------------------------------
    # Through the Docker archive API: works on stopped containers and needs no host paths,
    # so it behaves the same when the panel itself runs in a container.

    async def read_file(self, server_id: str, path: str) -> bytes | None:
        """Contents of an absolute path inside the server container, or None if there's no such file."""
        container = await self._docker.containers.get(container_name(server_id))
        try:
            archive = await container.get_archive(path)
        except DockerError as exc:
            if exc.status == 404:
                return None
            raise
        with archive:
            member = next((m for m in archive.getmembers() if m.isfile()), None)
            if member is None:
                return None
            return archive.extractfile(member).read()

    async def write_file(self, server_id: str, path: str, data: bytes) -> None:
        """Replace a file, keeping its owner and mode (game servers rarely run as root)."""
        container = await self._docker.containers.get(container_name(server_id))
        info = tarfile.TarInfo(posixpath.basename(path))
        info.size, info.mtime, info.mode, info.uid, info.gid = len(data), int(time.time()), 0o644, 0, 0
        try:
            with await container.get_archive(path) as existing:
                if old := next((m for m in existing.getmembers() if m.isfile()), None):
                    info.mode, info.uid, info.gid = old.mode, old.uid, old.gid
        except DockerError as exc:
            if exc.status != 404:
                raise

        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            tar.addfile(info, io.BytesIO(data))
        await container.put_archive(posixpath.dirname(path), buffer.getvalue())

    async def remove(self, server_id: str) -> None:
        """Remove the containers and the data volume. Irreversible."""
        name = container_name(server_id)
        await self._remove_container(f"{name}-install")
        await self._remove_container(name)
        try:
            volume = await self._docker.volumes.get(volume_name(server_id))
            await volume.delete()
        except DockerError as exc:
            if exc.status != 404:
                raise

    async def _remove_container(self, name: str) -> None:
        try:
            container = await self._docker.containers.get(name)
            await container.delete(force=True)
        except DockerError as exc:
            if exc.status != 404:
                raise
