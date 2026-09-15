"""Runs schedules: backups, restarts, starts, stops and console commands on a cron timetable."""

import asyncio
import logging
import os
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cronsim import CronSim, CronSimError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Schedule, ScheduleAction, Server
from app.runtime.manager import ServerBusy, ServerManager, ServerStatus

log = logging.getLogger(__name__)

RUNNING = (ServerStatus.RUNNING, ServerStatus.STARTING)


def resolve_timezone(name: str | None) -> tzinfo:
    """A real IANA zone, never a fixed offset: "every day at 04:00" must follow daylight saving time."""
    for candidate in (name, os.environ.get("TZ", "").lstrip(":"), _system_zone_name()):
        if candidate:
            try:
                return ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError):
                if candidate == name:
                    raise
    log.warning("could not find the system time zone, schedules run in UTC (set DGS_TIMEZONE)")
    return UTC


def _system_zone_name() -> str | None:
    # /etc/localtime -> /usr/share/zoneinfo/Europe/Kyiv
    target = os.path.realpath("/etc/localtime")
    return target.split("/zoneinfo/", 1)[1] if "/zoneinfo/" in target else None


def timezone_name(tz: tzinfo) -> str:
    return getattr(tz, "key", None) or datetime.now(tz).tzname() or "UTC"


def validate_cron(expression: str, tz: tzinfo) -> str:
    expression = " ".join(expression.split())
    if len(expression.split(" ")) != 5:
        raise ValueError("use 5 fields: minute hour day-of-month month day-of-week")
    try:
        CronSim(expression, datetime.now(tz))
    except CronSimError as exc:
        raise ValueError(str(exc)) from exc
    return expression


def next_run(expression: str, after: datetime, tz: tzinfo) -> datetime:
    """The first time strictly after `after`, evaluated in the panel's time zone, returned in UTC."""
    return next(CronSim(expression, after.astimezone(tz))).astimezone(UTC)


def describe(expression: str, tz: tzinfo) -> str:
    try:
        return CronSim(expression, datetime.now(tz)).explain()
    except (CronSimError, ValueError):
        return expression


class Scheduler:
    def __init__(self, manager: ServerManager, sessionmaker: async_sessionmaker[AsyncSession], tz: tzinfo):
        self.manager = manager
        self.tz = tz
        self._sessionmaker = sessionmaker
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _loop(self) -> None:
        await self.skip_missed()
        while True:
            now = datetime.now(UTC)
            # Wake just after each minute starts: cron has minute resolution.
            await asyncio.sleep(60 - now.second - now.microsecond / 1e6 + 0.5)
            try:
                await self.tick()
            except Exception:
                log.exception("scheduler tick failed")

    async def skip_missed(self, now: datetime | None = None) -> None:
        """Runs missed while the panel was down are skipped, not replayed all at once."""
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            stale = await session.scalars(
                select(Schedule).where(Schedule.enabled, Schedule.next_run_at < now)
            )
            for schedule in stale:
                schedule.next_run_at = next_run(schedule.cron, now, self.tz)
            await session.commit()

    async def tick(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        async with self._sessionmaker() as session:
            due = (
                await session.scalars(
                    select(Schedule)
                    .where(Schedule.enabled, Schedule.next_run_at <= now)
                    .order_by(Schedule.next_run_at)
                )
            ).all()
            for schedule in due:
                server = await session.get(Server, schedule.server_id)
                status, message = await self.execute(schedule, server)
                schedule.last_run_at, schedule.last_status, schedule.last_message = now, status, message
                schedule.next_run_at = next_run(schedule.cron, now, self.tz)
                log.info("schedule %s (%s): %s %s", schedule.name, schedule.action, status, message or "")
            await session.commit()

    async def execute(self, schedule: Schedule, server: Server | None) -> tuple[str, str | None]:
        """Do what the schedule says. Returns ("ok" | "skipped" | "failed", message)."""
        if server is None:
            return "failed", "the server no longer exists"
        manager = self.manager
        try:
            status = await manager.status_of(server)
            match schedule.action:
                case ScheduleAction.BACKUP:
                    await manager.create_backup(
                        server, note=schedule.name, schedule_id=schedule.id, keep=schedule.keep
                    )
                    return "ok", "backup started"
                case ScheduleAction.RESTART:
                    if status not in RUNNING:
                        return "skipped", "the server isn't running"
                    await manager.restart(server)
                case ScheduleAction.START:
                    if status not in (ServerStatus.STOPPED, ServerStatus.CRASHED):
                        return "skipped", f"the server is {status.value.replace('_', ' ')}"
                    await manager.start(server)
                case ScheduleAction.STOP:
                    if status not in RUNNING:
                        return "skipped", "the server isn't running"
                    await manager.stop(server)
                case ScheduleAction.COMMAND:
                    if status not in RUNNING:
                        return "skipped", "the server isn't running"
                    await manager.send_command(server, schedule.command or "")
            return "ok", None
        except ServerBusy as exc:
            return "failed", str(exc)
        except Exception as exc:
            log.exception("schedule %s failed", schedule.id)
            return "failed", str(exc)[:1000]
