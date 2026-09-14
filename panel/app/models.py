import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class ServerState(enum.StrEnum):
    """Lifecycle stored in the database. Whether it's running is asked from Docker, not stored."""

    PENDING = "pending"  # saved, install not started yet
    INSTALLING = "installing"
    INSTALL_FAILED = "install_failed"
    INSTALLED = "installed"  # container exists, ready to start


class Server(Base):
    __tablename__ = "servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(64))
    template_id: Mapped[str] = mapped_column(String(64), index=True)
    # Validated answers to the template's fields.
    values: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Host port per template port name: {"game": 25565}
    ports: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    state: Mapped[ServerState] = mapped_column(
        SAEnum(
            ServerState,
            native_enum=False,
            length=32,
            values_callable=lambda e: [m.value for m in e],  # store "pending", not "PENDING"
        ),
        default=ServerState.PENDING,
    )
    # Why the last install failed, shown to the user.
    state_message: Mapped[str | None] = mapped_column(String(1000), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
