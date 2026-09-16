import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    TypeDecorator,
    UniqueConstraint,
    false,
)
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
    # Started from the panel and not stopped since: brought back after a reboot, and a crash is a crash.
    should_run: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Set by an administrator; None: the template's default, 0: no limit.
    memory_limit_mb: Mapped[int | None] = mapped_column(Integer, default=None)
    cpu_limit: Mapped[float | None] = mapped_column(Float, default=None)
    # Soft limits on disk space, same rule: None follows the template (backups: twice the disk), 0 none.
    disk_limit_mb: Mapped[int | None] = mapped_column(Integer, default=None)
    backup_limit_mb: Mapped[int | None] = mapped_column(Integer, default=None)
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


class Player(Base):
    """Someone who played on a server, as its console output told the panel."""

    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("server_id", "key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    server_id: Mapped[str] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    # What tells players apart on this game: "id:<SteamID/UUID>" or "name:<name>".
    key: Mapped[str] = mapped_column(String(200))
    name: Mapped[str | None] = mapped_column(String(128), default=None)
    # The game's id for them: SteamID, Minecraft UUID, Xbox XUID.
    game_id: Mapped[str | None] = mapped_column(String(128), default=None)
    ip: Mapped[str | None] = mapped_column(String(64), default=None)
    # A number some games' commands use for a connected player (CS 1.6: kick #2).
    slot: Mapped[str | None] = mapped_column(String(16), default=None)
    online: Mapped[bool] = mapped_column(Boolean, default=False)
    online_since: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None)
    first_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    last_seen: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class BanRecord(Base):
    """A ban made through the panel: what game ban lists don't keep (reason, who, until when).

    For games without bans (Minecraft Bedrock) it is the ban list itself, enforced by the panel.
    """

    __tablename__ = "ban_records"
    __table_args__ = (UniqueConstraint("server_id", "kind", "value"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    server_id: Mapped[str] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    # "player" or "ip"
    kind: Mapped[str] = mapped_column(String(8))
    # As the game's list has it: a name, a SteamID, an address (panel-enforced: the player's key).
    value: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str | None] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    # None: permanent. The panel lifts it once this passes.
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, default=None, index=True)
    # Username of whoever banned; kept if the account is deleted.
    banned_by: Mapped[str | None] = mapped_column(String(32), default=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    # Admins see and do everything, including creating servers and managing users.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Can't log in; existing sessions stop working.
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class UserSession(Base):
    __tablename__ = "sessions"

    # SHA-256 of the cookie token: a leaked database doesn't hand out logged-in sessions.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_now)


class ServerAccess(Base):
    """What a non-admin user may do on one server. No row: the server is invisible to them."""

    __tablename__ = "server_access"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    server_id: Mapped[str] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True)
    # Permission names, see app.core.permissions.
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
