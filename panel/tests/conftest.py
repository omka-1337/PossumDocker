from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.games.providers import OptionsProviders
from app.games.schema import Option
from app.main import create_app


async def fake_versions(_client: httpx.AsyncClient, params: dict[str, Any]) -> list[Option]:
    return [Option(value=v, label=v) for v in ("1.21.1", "1.20.4")]


async def fake_loader_versions(_client: httpx.AsyncClient, params: dict[str, Any]) -> list[Option]:
    if params.get("loader") != "fabric":
        return []
    return [Option(value="0.16.5", label="0.16.5")]


@pytest.fixture
def providers() -> OptionsProviders:
    # No network in tests: same provider names as the real ones, canned data.
    registry = OptionsProviders(httpx.AsyncClient())
    registry.register("minecraft.versions", fake_versions)
    registry.register("minecraft.loader_versions", fake_loader_versions)
    return registry


@pytest.fixture
def client(tmp_path, providers) -> TestClient:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    with TestClient(create_app(settings, providers)) as test_client:
        yield test_client
