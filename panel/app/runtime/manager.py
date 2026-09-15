"""Server lifecycle: install in the background, start/stop, and the status shown in the UI."""

import asyncio
import enum
import logging
import posixpath
from collections import deque
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.games.configs import ConfigDocument
from app.games.schema import ConfigFile, Template
from app.models import Server, ServerState
from app.runtime.docker import ContainerState, LogFn, RuntimeUnavailable
from app.runtime.files import Files
from app.runtime.spec import ContainerSpec, build_spec

log = logging.getLogger(__name__)

INSTALL_LOG_LINES = 1000


class Runtime(Protocol):
    """What the manager needs from Docker. Tests pass a fake; later the Go agent client."""

    async def states(self) -> dict[str, ContainerState]: ...
    async def state(self, server_id: str) -> ContainerState | None: ...
    async def pull(self, image: str, on_log: LogFn) -> None: ...
    async def ensure_volume(self, server_id: str) -> str: ...
    async def run_install(self, server_id: str, spec: ContainerSpec, on_log: LogFn) -> None: ...
    async def create(self, server_id: str, spec: ContainerSpec) -> None: ...
    async def start(self, server_id: str) -> None: ...
    async def stop(self, server_id: str, command: str | None, timeout: int) -> None: ...
    async def send_command(self, server_id: str, line: str) -> None: ...
    async def read_file(self, server_id: str, path: str) -> bytes | None: ...
    async def write_file(self, server_id: str, path: str, data: bytes) -> None: ...
    def logs(self, server_id: str, tail: int | None = 200, since: int = 0) -> AsyncIterator[str]: ...
    async def remove(self, server_id: str) -> None: ...

    files: Files


class ServerStatus(enum.StrEnum):
    """What the UI shows: the DB lifecycle combined with the live container state."""

    PENDING = "pending"
    INSTALLING = "installing"
    INSTALL_FAILED = "install_failed"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    UNKNOWN = "unknown"  # Docker is unreachable


class ServerBusy(Exception):
    pass


class ServerManager:
    def __init__(
        self,
        runtime: Runtime,
        sessionmaker: async_sessionmaker[AsyncSession],
        templates: dict[str, Template],
        templates_dir: Path,
    ):
        self.runtime = runtime
        self._sessionmaker = sessionmaker
        self._templates = templates
        self._templates_dir = templates_dir
        self._tasks: dict[str, asyncio.Task] = {}
        self._stopping: set[str] = set()
        self._install_logs: dict[str, deque[str]] = {}
        self._reaper: asyncio.Task | None = None

    # --- startup / shutdown --------------------------------------------------

    async def recover(self) -> None:
        """An install can't survive a panel restart: mark interrupted ones as failed."""
        async with self._sessionmaker() as session:
            result = await session.scalars(select(Server).where(Server.state == ServerState.INSTALLING))
            for server in result:
                server.state = ServerState.INSTALL_FAILED
                server.state_message = "the panel was restarted during installation, reinstall the server"
            await session.commit()

    async def shutdown(self) -> None:
        tasks = [*self._tasks.values(), *([self._reaper] if self._reaper else [])]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def start_background_jobs(self) -> None:
        reap = getattr(self.runtime.files, "reap_idle", None)
        if reap is None:
            return

        async def reaper():
            while True:
                await asyncio.sleep(60)
                try:
                    await reap()
                except Exception:
                    log.exception("reaping idle file helpers failed")

        self._reaper = asyncio.create_task(reaper())

    # --- status --------------------------------------------------------------

    def status(
        self, server: Server, container: ContainerState | None, docker_ok: bool = True
    ) -> ServerStatus:
        if server.state != ServerState.INSTALLED:
            return ServerStatus(server.state.value)
        if server.id in self._stopping:
            return ServerStatus.STOPPING
        if not docker_ok:
            return ServerStatus.UNKNOWN
        if container is None:
            return ServerStatus.STOPPED  # container is recreated on start
        if container.status == "restarting" or (
            container.status == "running" and container.health == "starting"
        ):
            return ServerStatus.STARTING
        if container.status == "running":
            return ServerStatus.RUNNING
        return ServerStatus.STOPPED

    async def statuses(self, servers: list[Server]) -> dict[str, ServerStatus]:
        try:
            states = await self.runtime.states()
            docker_ok = True
        except RuntimeUnavailable:
            states, docker_ok = {}, False
        return {s.id: self.status(s, states.get(s.id), docker_ok) for s in servers}

    async def status_of(self, server: Server) -> ServerStatus:
        return (await self.statuses([server]))[server.id]

    def install_log(self, server_id: str) -> list[str]:
        return list(self._install_logs.get(server_id, []))

    # --- install -------------------------------------------------------------

    def is_busy(self, server_id: str) -> bool:
        task = self._tasks.get(server_id)
        return task is not None and not task.done()

    def _spawn(self, server_id: str, coro) -> None:
        if self.is_busy(server_id):
            coro.close()
            raise ServerBusy("another operation is still running for this server")
        task = asyncio.create_task(coro)
        self._tasks[server_id] = task
        task.add_done_callback(
            lambda t: self._tasks.pop(server_id, None) if self._tasks.get(server_id) is t else None
        )

    async def install(self, server: Server) -> None:
        """Mark the server as installing and do the actual work in the background."""
        async with self._sessionmaker() as session:
            db_server = await session.get(Server, server.id)
            db_server.state = ServerState.INSTALLING
            db_server.state_message = None
            await session.commit()
        server.state, server.state_message = ServerState.INSTALLING, None
        self._install_logs[server.id] = deque(maxlen=INSTALL_LOG_LINES)
        self._spawn(server.id, self._install(server.id))

    async def _install(self, server_id: str) -> None:
        lines = self._install_logs[server_id]
        on_log = lines.append
        try:
            async with self._sessionmaker() as session:
                server = await session.get(Server, server_id)
                template = self._templates[server.template_id]
                spec = build_spec(template, server, self._templates_dir)

            if spec.install:
                await self.runtime.pull(spec.install.image, on_log)
            if not spec.install or spec.install.image != spec.runtime.image:
                await self.runtime.pull(spec.runtime.image, on_log)
            await self.runtime.ensure_volume(server_id)
            if spec.install:
                on_log("running install step")
                await self.runtime.run_install(server_id, spec.install, on_log)
            await self.runtime.create(server_id, spec.runtime)
            on_log("installed")
            await self._set_state(server_id, ServerState.INSTALLED, None)
        except asyncio.CancelledError:
            await asyncio.shield(
                self._set_state(server_id, ServerState.INSTALL_FAILED, "installation was cancelled")
            )
            raise
        except Exception as exc:
            log.exception("install of %s failed", server_id)
            on_log(f"error: {exc}")
            await self._set_state(server_id, ServerState.INSTALL_FAILED, str(exc)[:1000])

    async def _set_state(self, server_id: str, state: ServerState, message: str | None) -> None:
        async with self._sessionmaker() as session:
            server = await session.get(Server, server_id)
            if server is None:  # deleted meanwhile
                return
            server.state = state
            server.state_message = message
            await session.commit()

    # --- lifecycle -----------------------------------------------------------

    def _require_installed(self, server: Server) -> None:
        if server.state != ServerState.INSTALLED:
            raise ServerBusy("the server is not installed")
        if self.is_busy(server.id):
            raise ServerBusy("another operation is still running for this server")

    async def start(self, server: Server) -> None:
        self._require_installed(server)
        state = await self.runtime.state(server.id)
        if state and state.status in ("running", "restarting", "paused"):
            raise ServerBusy("the server is already running")
        await self._start(server)

    async def _start(self, server: Server) -> None:
        # Always from the current values and template: edited settings apply on the next start.
        # The data lives in the volume, so a fresh container loses nothing but old console output.
        spec = build_spec(self._templates[server.template_id], server, self._templates_dir)
        await self.runtime.create(server.id, spec.runtime)
        await self.runtime.start(server.id)

    async def _ensure_container(self, server: Server) -> None:
        if await self.runtime.state(server.id) is None:
            # Removed outside the panel: rebuild it, the data volume is untouched.
            spec = build_spec(self._templates[server.template_id], server, self._templates_dir)
            await self.runtime.create(server.id, spec.runtime)

    async def stop(self, server: Server) -> None:
        self._require_installed(server)
        self._spawn(server.id, self._stop(server))

    async def restart(self, server: Server) -> None:
        self._require_installed(server)

        async def restart():
            await self._stop(server)
            try:
                await self._start(server)
            except Exception:
                log.exception("restart of %s failed to start it again", server.id)

        self._spawn(server.id, restart())

    async def _stop(self, server: Server) -> None:
        stop = self._templates[server.template_id].runtime.stop
        self._stopping.add(server.id)
        try:
            state = await self.runtime.state(server.id)
            if state and state.status in ("running", "restarting", "paused"):
                await self.runtime.stop(server.id, stop.command, stop.timeout)
        except Exception:
            log.exception("stop of %s failed", server.id)
        finally:
            self._stopping.discard(server.id)

    async def send_command(self, server: Server, line: str) -> None:
        await self.runtime.send_command(server.id, line)

    # --- config files --------------------------------------------------------

    def _config_path(self, server: Server, config: ConfigFile) -> str:
        return posixpath.join(self._templates[server.template_id].runtime.data_path, config.path)

    async def read_config(self, server: Server, config: ConfigFile) -> ConfigDocument | None:
        """None until the game has written the file (usually on its first start)."""
        if server.state != ServerState.INSTALLED:
            raise ServerBusy("the server is not installed")
        await self._ensure_container(server)
        data = await self.runtime.read_file(server.id, self._config_path(server, config))
        return None if data is None else ConfigDocument(config.format, data)

    async def write_config(
        self, server: Server, config: ConfigFile, values: dict[str, str]
    ) -> ConfigDocument:
        document = await self.read_config(server, config) or ConfigDocument(config.format, b"")
        for key, value in values.items():
            document.set(key, value)
        await self.runtime.write_file(server.id, self._config_path(server, config), document.render())
        return document

    async def delete(self, server: Server) -> None:
        task = self._tasks.get(server.id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.runtime.remove(server.id)
        self._install_logs.pop(server.id, None)
