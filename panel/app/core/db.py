import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import Request
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import PANEL_DIR


def create_engine(database_url: str) -> AsyncEngine:
    url = make_url(database_url)
    if url.get_backend_name() != "sqlite":
        return create_async_engine(url)

    if url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)

    engine = create_async_engine(url)

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):
        cursor = dbapi_connection.cursor()
        # WAL lets readers work while a write is in progress;
        # busy_timeout waits for the lock instead of failing with "database is locked".
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


async def run_migrations(database_url: str) -> None:
    """Apply Alembic migrations up to head, so self-hosters never run them by hand."""
    cfg = Config()
    cfg.set_main_option("script_location", str(PANEL_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    # migrations/env.py drives its own event loop, so keep it off ours.
    await asyncio.to_thread(command.upgrade, cfg, "head")


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session
