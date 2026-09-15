import io

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import cli
from app.core.db import create_engine, run_migrations
from app.core.security import verify_password
from app.models import User


@pytest.fixture
async def sessionmaker(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path / 'cli.db'}"
    await run_migrations(url)
    engine = create_engine(url)
    yield async_sessionmaker(engine)
    await engine.dispose()


async def users(sessionmaker) -> list[User]:
    async with sessionmaker() as session:
        return list(await session.scalars(select(User)))


async def test_first_start_creates_the_admin(sessionmaker, monkeypatch, capsys):
    # A bad name and a short password are asked again; passwords must match.
    typed = "a\nadmin\nshort\nyoushallnotpass\ntypo\nyoushallnotpass\nyoushallnotpass\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(typed))
    await cli.create_admin(sessionmaker, only_if_no_users=True)

    out = capsys.readouterr().out
    assert "Create administrator account" in out and "don't match" in out and "at least 8" in out
    [admin] = await users(sessionmaker)
    assert admin.username == "admin" and admin.is_admin
    assert verify_password(admin.password_hash, "youshallnotpass")
    assert "youshallnotpass" not in admin.password_hash


async def test_ensure_admin_does_nothing_once_users_exist(sessionmaker, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("admin\nyoushallnotpass\nyoushallnotpass\n"))
    await cli.create_admin(sessionmaker, only_if_no_users=True)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # would exit if it asked anything
    await cli.create_admin(sessionmaker, only_if_no_users=True)
    assert len(await users(sessionmaker)) == 1


async def test_create_admin_resets_a_forgotten_password(sessionmaker, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("admin\nyoushallnotpass\nyoushallnotpass\n"))
    await cli.create_admin(sessionmaker, only_if_no_users=True)
    monkeypatch.setattr("sys.stdin", io.StringIO("admin\ny\nnew-password-1\nnew-password-1\n"))
    await cli.create_admin(sessionmaker, only_if_no_users=False)
    [admin] = await users(sessionmaker)
    assert verify_password(admin.password_hash, "new-password-1")
