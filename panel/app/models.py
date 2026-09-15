import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, TypeDecorator
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Always timezone-aware UTC in Python. SQLite has no time zones and would hand back naive
    datetimes, which the browser then reads as local time."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        value = (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _str_enum(enum_cls: type[enum.StrEnum]) -> SAEnum:
    return SAEnum(
        enum_cls,
        native_enum=False,
        length=32,
        values_callable=lambda e: [m.value for m in e],  # store "pending", not "PENDING"
    )


class ServerState(enum.StrEnum):
    """Lifecycle stored in the database. Whether it's running is asked from Docker, not stored."""

    PENDING = "pending"  # saved, install not started yet
    INSTALLING = "installing"
    INSTALL_FAILED = "install_failed"
    INSTALLED = "installed"  # container exists, ready to start


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(64))
    template_id: Mapped[str] = mapped_column(String(64), index=True)
    # Validated answers to the template's fields.
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Host port per template port name: {"game": 25565}
    ports: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    state: Mapped[ServerState] = mapped_column(_str_enum(ServerState), default=ServerState.PENDING)
    # Why the last install failed, shown to the user.
    state_message: Mapped[str | None] = mapped_column(String(1000), default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class BackupStatus(enum.StrEnum):
    CREATING = "creating"
    READY = "ready"
    FAILED = "failed"


class Backup(Base):
    __tablename__ = "backups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    server_id: Mapped[str] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    note: Mapped[str | None] = mapped_column(String(200), default=None)
    status: Mapped[BackupStatus] = mapped_column(_str_enum(BackupStatus), default=BackupStatus.CREATING)
    message: Mapped[str | None] = mapped_column(String(1000), default=None)
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    # What the archive holds, relative to the data volume. None: everything. Restore replaces exactly these.
    paths: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    # Top-level names left out of a full backup; a restore keeps whatever the server has for them.
    exclude: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Set when a schedule made it; used to keep only that schedule's newest N backups.
    schedule_id: Mapped[str | None] = mapped_column(String(36), default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class ScheduleAction(enum.StrEnum):
    BACKUP = "backup"
    RESTART = "restart"
    START = "start"
    STOP = "stop"
    COMMAND = "command"


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    server_id: Mapped[str] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    # Standard 5-field cron, in the panel's time zone: "0 4 * * *"
    cron: Mapped[str] = mapped_column(String(100))
    action: Mapped[ScheduleAction] = mapped_column(_str_enum(ScheduleAction))
    command: Mapped[str | None] = mapped_column(String(1000), default=None)
    # Backups: how many of this schedule's backups to keep.
    keep: Mapped[int | None] = mapped_column(Integer, default=None)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None, index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    # "ok" | "failed" | "skipped"
    last_status: Mapped[str | None] = mapped_column(String(16), default=None)
    last_message: Mapped[str | None] = mapped_column(String(1000), default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
