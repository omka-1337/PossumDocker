import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ServerStatus(enum.StrEnum):
    PENDING = "pending"  # saved, not installed yet
    INSTALLING = "installing"
    INSTALL_FAILED = "install_failed"
    STOPPED = "stopped"  # installed and ready to start
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(64))
    template_id: Mapped[str] = mapped_column(String(64), index=True)
    # Validated answers to the template's fields.
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[ServerStatus] = mapped_column(
        SAEnum(
            ServerStatus,
            native_enum=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],  # store "pending", not "PENDING"
        ),
        default=ServerStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
