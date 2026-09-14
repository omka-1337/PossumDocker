import time
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.games.providers import OptionsProviders
from app.games.schema import Option
from app.main import create_app
from app.runtime.docker import ContainerState, LogFn
from app.runtime.spec import ContainerSpec


async def fake_versions(_client: httpx.AsyncClient, params: dict[str, Any]) -> list[Option]:
    return [Option(value=v, label=v) for v in ("1.21.1", "1.20.4")]


async def fake_loader_versions(_client: httpx.AsyncClient, params: dict[str, Any]) -> list[Option]:
    if params.get("loader") != "fabric":
        return []
    return [Option(value="0.16.5", label="0.16.5")]


class FakeRuntime:
    """In-memory stand-in for Docker."""

    def __init__(self):
        self.containers: dict[str, ContainerState] = {}
        self.specs: dict[str, ContainerSpec] = {}
        self.installed: list[str] = []
        self.commands: list[tuple[str, str]] = []
        self.fail_install = False

    async def states(self):
        return dict(self.containers)

    async def state(self, server_id):
        return self.containers.get(server_id)

    async def pull(self, image: str, on_log: LogFn):
        on_log(f"pulling {image}")

    async def ensure_volume(self, server_id):
        return f"dgs-{server_id}-data"

    async def run_install(self, server_id, spec, on_log):
        if self.fail_install:
            raise RuntimeError("install step exited with code 1")
        self.installed.append(server_id)

    async def create(self, server_id, spec):
        self.specs[server_id] = spec
        self.containers[server_id] = ContainerState(status="created")

    async def start(self, server_id):
        self.containers[server_id] = ContainerState(status="running")

    async def stop(self, server_id, command, timeout):
        self.commands.append((server_id, command))
        self.containers[server_id] = ContainerState(status="exited")

    async def send_command(self, server_id, line):
        self.commands.append((server_id, line))

    async def remove(self, server_id):
        self.containers.pop(server_id, None)


@pytest.fixture
def providers() -> OptionsProviders:
    # No network in tests: same provider names as the real ones, canned data.
    registry = OptionsProviders(httpx.AsyncClient())
    registry.register("minecraft.versions", fake_versions)
    registry.register("minecraft.loader_versions", fake_loader_versions)
    return registry


@pytest.fixture
def runtime() -> FakeRuntime:
    return FakeRuntime()


@pytest.fixture
def client(tmp_path, providers, runtime) -> TestClient:
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    with TestClient(create_app(settings, providers, runtime)) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def free_ports(monkeypatch):
    # Don't depend on what's listening on the machine running the tests.
    monkeypatch.setattr("app.runtime.ports.is_port_free", lambda port, protocol: True)


def wait_for_status(client: TestClient, server_id: str, *statuses: str, timeout: float = 5) -> dict:
    """Background tasks run in the TestClient's event loop thread; poll until they settle."""
    deadline = time.monotonic() + timeout
    while True:
        server = client.get(f"/api/servers/{server_id}").json()
        if server["status"] in statuses or time.monotonic() > deadline:
            return server
        time.sleep(0.02)
