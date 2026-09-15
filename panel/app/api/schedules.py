from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.api.deps import Session
from app.api.servers import get_server_or_404
from app.games.schema import Template
from app.models import Schedule, ScheduleAction
from app.runtime.scheduler import Scheduler, describe, next_run, validate_cron

router = APIRouter(prefix="/servers/{server_id}/schedules", tags=["schedules"])


class ScheduleWrite(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    # 5-field cron in the panel's time zone (GET /api/meta): "0 4 * * *" = every day at 04:00.
    cron: str = Field(min_length=9, max_length=100)
    action: ScheduleAction
    command: str | None = Field(default=None, max_length=1000)
    # Backups only: how many of this schedule's backups to keep.
    keep: Annotated[int, Field(ge=1, le=100)] | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def _fields_for_action(self) -> "ScheduleWrite":
        if self.action == ScheduleAction.COMMAND:
            self.command = (self.command or "").strip() or None
        else:
            self.command = None
        if self.action != ScheduleAction.BACKUP:
            self.keep = None
        return self


class ScheduleRead(BaseModel):
    id: str
    name: str
    cron: str
    # "At 04:00 every day"
    description: str
    action: ScheduleAction
    command: str | None
    keep: int | None
    enabled: bool
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_status: str | None
    last_message: str | None


def get_scheduler(request: Request) -> Scheduler:
    return request.app.state.scheduler


def to_read(schedule: Schedule, scheduler: Scheduler) -> ScheduleRead:
    return ScheduleRead(
        **{k: getattr(schedule, k) for k in ScheduleRead.model_fields if k != "description"},
        description=describe(schedule.cron, scheduler.tz),
    )


def apply(schedule: Schedule, body: ScheduleWrite, scheduler: Scheduler, template: Template | None) -> None:
    errors = {}
    try:
        cron = validate_cron(body.cron, scheduler.tz)
    except ValueError as exc:
        errors["cron"] = str(exc)
    if body.action == ScheduleAction.COMMAND and not body.command:
        errors["command"] = "a command schedule needs a command"
    if body.action == ScheduleAction.COMMAND and template and not template.console.commands:
        errors["action"] = "this game has no console commands"
    if errors:
        # Keyed by field, like server creation, so the form shows each message under its input.
        raise HTTPException(422, {"errors": errors})
    schedule.name, schedule.cron, schedule.action = body.name.strip(), cron, body.action
    schedule.command, schedule.keep, schedule.enabled = body.command, body.keep, body.enabled
    schedule.next_run_at = next_run(cron, datetime.now(UTC), scheduler.tz) if body.enabled else None


async def get_schedule_or_404(session: Session, server_id: str, schedule_id: str) -> Schedule:
    schedule = await session.get(Schedule, schedule_id)
    if schedule is None or schedule.server_id != server_id:
        raise HTTPException(404, "schedule not found")
    return schedule


@router.get("")
async def list_schedules(server_id: str, session: Session, request: Request) -> list[ScheduleRead]:
    await get_server_or_404(session, server_id)
    schedules = await session.scalars(
        select(Schedule).where(Schedule.server_id == server_id).order_by(Schedule.created_at)
    )
    return [to_read(s, get_scheduler(request)) for s in schedules]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_schedule(
    server_id: str, body: ScheduleWrite, session: Session, request: Request
) -> ScheduleRead:
    server = await get_server_or_404(session, server_id)
    schedule = Schedule(server_id=server_id)
    apply(schedule, body, get_scheduler(request), request.app.state.templates.get(server.template_id))
    session.add(schedule)
    await session.commit()
    return to_read(schedule, get_scheduler(request))


@router.put("/{schedule_id}")
async def update_schedule(
    server_id: str, schedule_id: str, body: ScheduleWrite, session: Session, request: Request
) -> ScheduleRead:
    schedule = await get_schedule_or_404(session, server_id, schedule_id)
    server = await get_server_or_404(session, server_id)
    apply(schedule, body, get_scheduler(request), request.app.state.templates.get(server.template_id))
    await session.commit()
    return to_read(schedule, get_scheduler(request))


@router.delete("/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(server_id: str, schedule_id: str, session: Session) -> None:
    schedule = await get_schedule_or_404(session, server_id, schedule_id)
    await session.delete(schedule)
    await session.commit()


@router.post("/{schedule_id}/run")
async def run_schedule_now(
    server_id: str, schedule_id: str, session: Session, request: Request
) -> ScheduleRead:
    """Run it once right away. The regular timetable is unchanged."""
    scheduler = get_scheduler(request)
    schedule = await get_schedule_or_404(session, server_id, schedule_id)
    server = await get_server_or_404(session, server_id)
    result, message = await scheduler.execute(schedule, server)
    schedule.last_run_at, schedule.last_status, schedule.last_message = datetime.now(UTC), result, message
    await session.commit()
    return to_read(schedule, scheduler)
