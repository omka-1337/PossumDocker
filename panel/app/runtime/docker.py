"""Talks to the Docker Engine API directly: for development without the agent.

In a real installation the panel uses app.runtime.agent and has no Docker access at all.
Keep this in step with the agent (agent/internal/runtime); tests check both have the same methods.
"""

import asyncio
import io
import json
import logging
import posixpath
import re
import tarfile
import time
from collections.abc import AsyncIterator

import aiodocker
import aiohttp
from aiodocker.exceptions import DockerError

from app.runtime.spec import ContainerSpec
from app.runtime.state import ContainerState, LogFn, RuntimeUnavailable, parse_docker_time

__all__ = ["ContainerState", "DockerRuntime", "LogFn", "RuntimeUnavailable", "host_limits"]

log = logging.getLogger(__name__)

LABEL_MANAGED = "possum.managed"
LABEL_SERVER = "possum.server_id"
LABEL_ROLE = "possum.role"

RESTART_ATTEMPTS = 3
PIDS_LIMIT = 4096


def host_limits(spec: ContainerSpec) -> dict:
    """Crash restarts and resource limits, shared by the agent (agent/internal/runtime keeps them in step)."""
    limits: dict = {
        # Docker brings a crashed server back (non-zero exit) a few times; a clean stop (exit 0) or
        # a stop from the panel stays stopped.
        "RestartPolicy": {"Name": "on-failure", "MaximumRetryCount": RESTART_ATTEMPTS},
        # A runaway plugin can't fork-bomb the host.
        "PidsLimit": PIDS_LIMIT,
    }
    if spec.memory_mb:
        # Same limit for memory+swap: no swapping past the limit, the game gets killed instead.
        limits["Memory"] = limits["MemorySwap"] = spec.memory_mb * 1024 * 1024
    if spec.cpus:
        limits["NanoCpus"] = int(spec.cpus * 1_000_000_000)
    return limits


def _tar_with(path: str, data: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        folder = tarfile.TarInfo(posixpath.dirname(path))
        folder.type, folder.mode = tarfile.DIRTYPE, 0o755
        tar.addfile(folder)
        info = tarfile.TarInfo(path)
        info.size, info.mode, info.mtime = len(data), 0o755, int(time.time())
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def container_name(server_id: str) -> str:
    return f"possum-{server_id}"


def volume_name(server_id: str) -> str:
    return f"possum-{server_id}-data"


def _labels(server_id: str, role: str) -> dict[str, str]:
    return {LABEL_MANAGED: "true", LABEL_SERVER: server_id, LABEL_ROLE: role}


def _exit_code_from_status(status_text: str) -> int | None:
    # `docker ps` status text: "Exited (137) 2 minutes ago"
    match = re.match(r"Exited \((-?\d+)\)", status_text)
    return int(match.group(1)) if match else None


def _health_from_status(status_text: str) -> str | None:
    # `docker ps` status text: "Up 5 minutes (healthy)", "Up 3 seconds (health: starting)"
    match = re.search(r"\((?:health: )?(starting|healthy|unhealthy)\)", status_text)
    return match.group(1) if match else None


class DockerRuntime:
    def __init__(self, docker: aiodocker.Docker | None = None):
        from app.runtime.docker_files import DockerFiles  # it imports the naming helpers from here

        self._docker = docker or aiodocker.Docker()
        self.files = DockerFiles(self._docker)

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
                text = info.get("Status", "")
                states[server_id] = ContainerState(
                    status=info["State"],
                    health=_health_from_status(text),
                    exit_code=_exit_code_from_status(text),
                )
        return states

    async def published_ports(self) -> set[tuple[int, str]]:
        """Host ports any container publishes. Inside a container the panel can't probe host ports itself."""
        taken = set()
        for container in await self._docker.containers.list(all="true"):
            for port in container._container.get("Ports", []):
                if port.get("PublicPort"):
                    taken.add((port["PublicPort"], port.get("Type", "tcp")))
        return taken

    async def state(self, server_id: str) -> ContainerState | None:
        try:
            info = await self._docker.containers.get(container_name(server_id))
        except DockerError as exc:
            if exc.status == 404:
                return None
            raise
        state = info["State"]
        return ContainerState(
            status=state["Status"],
            health=(state.get("Health") or {}).get("Status"),
            exit_code=state.get("ExitCode") if state["Status"] in ("exited", "dead") else None,
            oom_killed=bool(state.get("OOMKilled")),
            started_at=(
                parse_docker_time(state["StartedAt"])
                if state.get("StartedAt", "").startswith(("1", "2"))
                else None
            ),
        )

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

        container = await self._docker.containers.create(
            {
                **self._base_config(spec, server_id, "install"),
                "WorkingDir": spec.data_path,
                "HostConfig": {"Binds": [f"{volume_name(server_id)}:{spec.data_path}"]},
            },
            name=name,
        )
        try:
            if spec.script:
                # Copied in rather than bind-mounted: a bind needs a path on the Docker host,
                # and the panel may itself run in a container where the script lives elsewhere.
                script = await asyncio.to_thread(spec.script.read_bytes)
                await container.put_archive("/", _tar_with("possum/install.sh", script))
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
        if spec.mounts:
            # Docker refuses to mount a volume subpath that doesn't exist yet.
            await self.files.ensure_dirs(server_id, [m.subpath for m in spec.mounts])

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
                    **self._volume_config(server_id, spec),
                    "PortBindings": {port_key(p): [{"HostPort": str(p.host)}] for p in spec.ports},
                    **host_limits(spec),
                    # A tiny init as PID 1 forwards SIGTERM to the game; a game running as PID 1
                    # itself would ignore it and only die from SIGKILL after the timeout.
                    "Init": True,
                },
            },
            name=name,
        )

    def _volume_config(self, server_id: str, spec: ContainerSpec) -> dict:
        if not spec.mounts:
            return {"Binds": [f"{volume_name(server_id)}:{spec.data_path}"]}
        return {
            "Mounts": [
                {
                    "Type": "volume",
                    "Source": volume_name(server_id),
                    "Target": mount.path,
                    "VolumeOptions": {"Subpath": mount.subpath},
                }
                for mount in spec.mounts
            ]
        }

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
        # A game that exits with an error code on its way down must not be brought back by Docker.
        # The next start creates a new container with the policy back on.
        await self._docker._query_json(
            f"containers/{container_name(server_id)}/update",
            method="POST",
            data={"RestartPolicy": {"Name": "no"}},
        )
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

    async def logs(
        self, server_id: str, tail: int | None = 200, since: int = 0, timestamps: bool = False
    ) -> AsyncIterator[str]:
        """Follow the console output. Ends when the container stops. Nothing if it doesn't exist."""
        try:
            container = await self._docker.containers.get(container_name(server_id))
        except DockerError as exc:
            if exc.status == 404:
                return
            raise
        params = {"tail": "all" if tail is None else str(tail), "since": since, "timestamps": timestamps}
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
        await self.files.close(server_id)  # it keeps the volume in use
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
