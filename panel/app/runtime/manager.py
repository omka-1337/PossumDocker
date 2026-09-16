"""Server lifecycle: install in the background, start/stop, and the status shown in the UI."""

import asyncio
import enum
import logging
import posixpath
import shutil
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.games.configs import ConfigDocument, validate_config_values
from app.games.schema import ConfigFile, Template
from app.models import Backup, BackupStatus, Server, ServerState
from app.runtime.files import Files
from app.runtime.spec import ContainerSpec, build_spec, storage_limits
from app.runtime.state import ContainerState, LogFn, RuntimeUnavailable

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
    def logs(
        self, server_id: str, tail: int | None = 200, since: int = 0, timestamps: bool = False
    ) -> AsyncIterator[str]: ...
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
    CRASHED = "crashed"  # exited with an error and Docker gave up restarting it
    RESTORING = "restoring"  # a backup is being put back
    UNKNOWN = "unknown"  # Docker is unreachable


@dataclass(frozen=True)
class StatusInfo:
    status: ServerStatus
    # Why: an install error, or how a crashed server ended.
    message: str | None = None


# Exit codes of a process killed by a signal: 128 + the signal.
_SIGNALS = {134: "SIGABRT", 137: "SIGKILL", 139: "SIGSEGV", 143: "SIGTERM"}


def crash_message(container: ContainerState) -> str:
    if container.oom_killed:
        return f"ran out of memory (exit code {container.exit_code})"
    message = f"exited with code {container.exit_code}"
    if signal := _SIGNALS.get(container.exit_code or 0):
        message += f" ({signal}" + (", possibly out of memory" if signal == "SIGKILL" else "") + ")"
    return message


RESUME_ATTEMPTS = 12
RESUME_DELAY = 5
# How often running servers with a disk limit are measured.
DISK_CHECK_SECONDS = 120

MB = 1024 * 1024


def format_size(size: int) -> str:
    return f"{size / 1024 / MB:.1f} GB" if size >= 1024 * MB else f"{max(size, 0) / MB:.0f} MB"


class ServerBusy(Exception):
    pass


class StorageFull(ServerBusy):
    """Over a disk or backup limit. A soft limit: checked before the panel writes, and every few minutes."""


class ServerManager:
    def __init__(
        self,
        runtime: Runtime,
        sessionmaker: async_sessionmaker[AsyncSession],
        templates: dict[str, Template],
        templates_dir: Path,
        backups_dir: Path,
    ):
        self.runtime = runtime
        self.backups_dir = backups_dir
        self._sessionmaker = sessionmaker
        self._templates = templates
        self._templates_dir = templates_dir
        self._tasks: dict[str, asyncio.Task] = {}
        self._stopping: set[str] = set()
        self._restoring: set[str] = set()
        # Last restore failure per server, shown on the backups tab until the next restore.
        self.restore_errors: dict[str, str] = {}
        self._install_logs: dict[str, deque[str]] = {}
        self._reaper: asyncio.Task | None = None
        self._resumer: asyncio.Task | None = None
        self._disk_watcher: asyncio.Task | None = None
        from app.runtime.players import PlayerService  # it imports this module

        self.players = PlayerService(self)
        # Servers stopped for going over their disk limit, and why; shown until the next start.
        self.disk_errors: dict[str, str] = {}

    # --- startup / shutdown --------------------------------------------------

    async def recover(self) -> None:
        """Installs and backups can't survive a panel restart: mark interrupted ones as failed."""
        async with self._sessionmaker() as session:
            result = await session.scalars(select(Server).where(Server.state == ServerState.INSTALLING))
            for server in result:
                server.state = ServerState.INSTALL_FAILED
                server.state_message = "the panel was restarted during installation, reinstall the server"
            backups = await session.scalars(select(Backup).where(Backup.status == BackupStatus.CREATING))
            for backup in backups:
                backup.status = BackupStatus.FAILED
                backup.message = "the panel was restarted while this backup was being made"
                self.backup_file(backup).unlink(missing_ok=True)
            await session.commit()

    async def shutdown(self) -> None:
        await self.players.stop()
        tasks = [*self._tasks.values(), *(t for t in (self._reaper, self._resumer, self._disk_watcher) if t)]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def start_background_jobs(self) -> None:
        self._resumer = asyncio.create_task(self._resume())
        self._disk_watcher = asyncio.create_task(self._watch_disks())
        self.players.start()
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

    async def _resume(self) -> None:
        """Start servers that should be running but aren't: after a reboot, or Docker's own restarts ran out.

        Docker restarts a crashed container itself; this covers what it doesn't (a host reboot).
        """
        for _ in range(RESUME_ATTEMPTS):
            try:
                states = await self.runtime.states()
                break
            except RuntimeUnavailable:
                # The agent may still be starting.
                await asyncio.sleep(RESUME_DELAY)
        else:
            log.warning("docker unreachable, servers that were running are not started again")
            return

        async with self._sessionmaker() as session:
            servers = await session.scalars(
                select(Server).where(Server.should_run, Server.state == ServerState.INSTALLED)
            )
            for server in servers:
                container = states.get(server.id)
                if container and container.status in ("running", "restarting", "paused"):
                    continue
                log.info("starting %s again, it was running before", server.id)
                try:
                    await self.start(server)
                except Exception:
                    log.exception("starting %s again failed", server.id)

    # --- disk space ----------------------------------------------------------

    def limits_of(self, server: Server) -> tuple[int | None, int | None]:
        """Disk and backup limits in bytes; None: no limit."""
        template = self._templates.get(server.template_id)
        if template is None:
            return None, None
        disk, backups = storage_limits(template, server)
        return (disk * MB if disk else None), (backups * MB if backups else None)

    async def disk_usage(self, server: Server) -> int:
        return await self.runtime.files.usage(server.id, [])

    async def require_space(self, server: Server, adding: int) -> None:
        """Refuse a write of `adding` bytes that would take the server over its disk limit."""
        limit, _ = self.limits_of(server)
        if limit is None:
            return
        used = await self.disk_usage(server)
        if used + adding > limit:
            left = format_size(limit - used)
            raise StorageFull(
                f"not enough space: this needs {format_size(adding)}, {left} of {format_size(limit)} is left"
            )

    async def check_disk(self, server: Server) -> str | None:
        """Why the server is over its disk limit, or None."""
        limit, _ = self.limits_of(server)
        if limit is None:
            return None
        used = await self.disk_usage(server)
        if used <= limit:
            return None
        used_text = f"{format_size(used)} of {format_size(limit)}"
        return f"over its disk limit: {used_text} used, free up space or raise the limit"

    async def _watch_disks(self) -> None:
        while True:
            await asyncio.sleep(DISK_CHECK_SECONDS)
            try:
                await self.stop_servers_over_disk_limit()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("checking disk usage failed")

    async def stop_servers_over_disk_limit(self) -> None:
        """What a game writes itself (worlds, logs, plugins) isn't checked on the way: catch it here."""
        async with self._sessionmaker() as session:
            servers = [s for s in await session.scalars(select(Server).where(Server.should_run))]
        servers = [s for s in servers if self.limits_of(s)[0] is not None]
        statuses = await self.statuses(servers)
        for server in servers:
            if statuses[server.id] not in (ServerStatus.RUNNING, ServerStatus.STARTING):
                continue
            try:
                if problem := await self.check_disk(server):
                    log.warning("stopping %s: %s", server.id, problem)
                    self.disk_errors[server.id] = f"stopped: {problem}"
                    await self.stop(server)
            except ServerBusy:
                pass  # a backup or another stop is running: next round

    # --- status --------------------------------------------------------------

    def status(
        self, server: Server, container: ContainerState | None, docker_ok: bool = True
    ) -> ServerStatus:
        return self.status_info(server, container, docker_ok).status

    def status_info(
        self, server: Server, container: ContainerState | None, docker_ok: bool = True
    ) -> StatusInfo:
        if server.state != ServerState.INSTALLED:
            return StatusInfo(ServerStatus(server.state.value), server.state_message)
        status = self._live_status(server, container, docker_ok)
        if status == ServerStatus.CRASHED:
            return StatusInfo(status, crash_message(container))
        if status == ServerStatus.STOPPED and server.id in self.disk_errors:
            return StatusInfo(status, self.disk_errors[server.id])
        return StatusInfo(status)

    def _live_status(self, server: Server, container: ContainerState | None, docker_ok: bool) -> ServerStatus:
        if server.id in self._restoring:
            return ServerStatus.RESTORING
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
        # Stopped without the panel stopping it: a crash, unless the game exited cleanly
        # (e.g. `stop` typed into the console).
        if server.should_run and container.status in ("exited", "dead"):
            if container.oom_killed or container.exit_code not in (0, None):
                return ServerStatus.CRASHED
        return ServerStatus.STOPPED

    async def status_infos(self, servers: list[Server]) -> dict[str, StatusInfo]:
        try:
            states = await self.runtime.states()
            docker_ok = True
        except RuntimeUnavailable:
            states, docker_ok = {}, False
        return {s.id: self.status_info(s, states.get(s.id), docker_ok) for s in servers}

    async def statuses(self, servers: list[Server]) -> dict[str, ServerStatus]:
        return {sid: info.status for sid, info in (await self.status_infos(servers)).items()}

    async def status_info_of(self, server: Server) -> StatusInfo:
        """One server, with the details only a full inspect has (was it out of memory)."""
        if server.state != ServerState.INSTALLED:
            return self.status_info(server, None)
        try:
            return self.status_info(server, await self.runtime.state(server.id))
        except RuntimeUnavailable:
            return self.status_info(server, None, docker_ok=False)

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
            db_server.should_run = False
            await session.commit()
        server.state, server.state_message, server.should_run = ServerState.INSTALLING, None, False
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

    async def _set_should_run(self, server: Server, should_run: bool) -> None:
        server.should_run = should_run
        async with self._sessionmaker() as session:
            if db_server := await session.get(Server, server.id):
                db_server.should_run = should_run
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
        if problem := await self.check_disk(server):
            self.disk_errors[server.id] = f"can't start: {problem}"
            raise StorageFull(f"the server is {problem}")
        self.disk_errors.pop(server.id, None)
        await self._start(server)

    async def _start(self, server: Server) -> None:
        # Always from the current values and template: edited settings apply on the next start.
        # The data lives in the volume, so a fresh container loses nothing but old console output.
        spec = build_spec(self._templates[server.template_id], server, self._templates_dir)
        await self.runtime.create(server.id, spec.runtime)
        await self._set_should_run(server, True)
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
            await self._set_should_run(server, False)
            state = await self.runtime.state(server.id)
            if state and state.status in ("running", "restarting", "paused"):
                await self.runtime.stop(server.id, stop.command, stop.timeout)
        except Exception:
            log.exception("stop of %s failed", server.id)
        finally:
            self._stopping.discard(server.id)

    async def send_command(self, server: Server, line: str) -> None:
        if not self._templates[server.template_id].console.commands:
            raise ServerBusy("this game has no console commands")
        await self.runtime.send_command(server.id, line)

    # --- config files --------------------------------------------------------

    def container_path(self, server: Server, path: str) -> str:
        """Where the game container sees a file given relative to the server's data (a config, a ban list)."""
        runtime = self._templates[server.template_id].runtime
        if not runtime.mounts:
            return posixpath.join(runtime.data_path, path)
        # With mounts, paths are relative to the volume: find the mount that holds it.
        for mount in runtime.mounts:
            if path == mount.subpath or path.startswith(mount.subpath + "/"):
                return posixpath.join(mount.path, path[len(mount.subpath) :].lstrip("/"))
        raise ValueError(f"{path} isn't inside any of the game's mounts")

    def _config_path(self, server: Server, config: ConfigFile) -> str:
        return self.container_path(server, config.path)

    async def read_game_file(self, server: Server, path: str) -> bytes | None:
        """A file of the game, running or not; None if it doesn't exist (yet)."""
        if server.state != ServerState.INSTALLED:
            raise ServerBusy("the server is not installed")
        await self._ensure_container(server)
        return await self.runtime.read_file(server.id, self.container_path(server, path))

    async def write_game_file(self, server: Server, path: str, data: bytes) -> None:
        await self._ensure_container(server)
        await self.runtime.write_file(server.id, self.container_path(server, path), data)

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
        # Again with the file at hand: only keys it has, or ones the template knows about.
        validate_config_values(config, values, existing=set(document.items()))
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
        # Backup rows go with the server (ON DELETE CASCADE); their files have to go too.
        await asyncio.to_thread(shutil.rmtree, self.backups_dir / server.id, True)

    # --- backups -------------------------------------------------------------

    def backup_file(self, backup: Backup) -> Path:
        return self.backups_dir / backup.server_id / f"{backup.id}.tar.gz"

    async def create_backup(
        self,
        server: Server,
        note: str | None = None,
        schedule_id: str | None = None,
        keep: int | None = None,
    ) -> Backup:
        """Record the backup and archive the server in the background.

        `keep`: afterwards, delete this schedule's backups beyond the newest N.
        """
        self._require_installed(server)
        await self._require_backup_space(server, schedule_id, keep)
        async with self._sessionmaker() as session:
            backup = Backup(server_id=server.id, note=note, schedule_id=schedule_id)
            session.add(backup)
            await session.commit()
        self._spawn(server.id, self._backup(server, backup, keep))
        return backup

    async def backup_usage(self, server_id: str) -> int:
        async with self._sessionmaker() as session:
            sizes = await session.scalars(select(Backup.size).where(Backup.server_id == server_id))
            return sum(sizes)

    async def _require_backup_space(self, server: Server, schedule_id: str | None, keep: int | None) -> None:
        _, limit = self.limits_of(server)
        if limit is None:
            return
        used = await self.backup_usage(server.id)
        if keep:
            # A schedule deletes its oldest backups once the new one is done: count only those it keeps.
            async with self._sessionmaker() as session:
                pruned = await session.scalars(
                    select(Backup.size)
                    .where(
                        Backup.server_id == server.id,
                        Backup.schedule_id == schedule_id,
                        Backup.status == BackupStatus.READY,
                    )
                    .order_by(Backup.created_at.desc())
                    .offset(keep - 1)
                )
                used -= sum(pruned)
        if used >= limit:
            raise StorageFull(
                f"backup space is full: {format_size(used)} of {format_size(limit)} used, delete old backups"
            )

    async def _backup(self, server: Server, backup: Backup, keep: int | None) -> None:
        spec = self._templates[server.template_id].backup
        target = self.backup_file(backup)
        running = await self.status_of(server) in (ServerStatus.RUNNING, ServerStatus.STARTING)
        try:
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            if running:
                for command in spec.before:
                    await self.runtime.send_command(server.id, command)
                if spec.before:
                    await asyncio.sleep(spec.wait)
            try:
                size, paths = await self.runtime.files.archive_to(
                    server.id, target, spec.paths or None, spec.exclude
                )
            finally:
                if running:
                    for command in spec.after:
                        await self.runtime.send_command(server.id, command)
            await self._finish_backup(
                backup.id, BackupStatus.READY, None, size=size, paths=paths, exclude=list(spec.exclude)
            )
            if keep:
                await self._prune_backups(server.id, backup.schedule_id, keep)
        except asyncio.CancelledError:
            await asyncio.to_thread(target.unlink, True)
            raise
        except Exception as exc:
            log.exception("backup of %s failed", server.id)
            await asyncio.to_thread(target.unlink, True)
            await self._finish_backup(backup.id, BackupStatus.FAILED, str(exc)[:1000])

    async def _finish_backup(
        self, backup_id: str, status: BackupStatus, message: str | None, **fields
    ) -> None:
        async with self._sessionmaker() as session:
            backup = await session.get(Backup, backup_id)
            if backup is None:
                return
            backup.status, backup.message = status, message
            for key, value in fields.items():
                setattr(backup, key, value)
            await session.commit()

    async def _prune_backups(self, server_id: str, schedule_id: str | None, keep: int) -> None:
        async with self._sessionmaker() as session:
            old = (
                await session.scalars(
                    select(Backup)
                    .where(
                        Backup.server_id == server_id,
                        Backup.schedule_id == schedule_id,
                        Backup.status == BackupStatus.READY,
                    )
                    .order_by(Backup.created_at.desc())
                    .offset(keep)
                )
            ).all()
            for backup in old:
                await asyncio.to_thread(self.backup_file(backup).unlink, True)
                await session.delete(backup)
            await session.commit()

    async def restore_backup(self, server: Server, backup: Backup) -> None:
        self._require_installed(server)
        if backup.status != BackupStatus.READY:
            raise ServerBusy("this backup isn't complete")
        if await self.status_of(server) not in (ServerStatus.STOPPED, ServerStatus.CRASHED):
            raise ServerBusy("stop the server before restoring a backup")
        self._restoring.add(server.id)
        try:
            self._spawn(server.id, self._restore(server, backup))
        except ServerBusy:
            self._restoring.discard(server.id)
            raise

    async def _restore(self, server: Server, backup: Backup) -> None:
        try:
            await self.runtime.files.restore_from(
                server.id, self.backup_file(backup), backup.paths, backup.exclude or []
            )
        except Exception as exc:
            log.exception("restore of %s from %s failed", server.id, backup.id)
            self.restore_errors[server.id] = str(exc)[:1000]
        else:
            self.restore_errors.pop(server.id, None)
        finally:
            self._restoring.discard(server.id)

    async def delete_backup(self, backup: Backup) -> None:
        if backup.status == BackupStatus.CREATING:
            raise ServerBusy("this backup is still being made")
        await asyncio.to_thread(self.backup_file(backup).unlink, True)
