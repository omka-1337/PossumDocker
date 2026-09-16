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
from tests.local_files import LocalFiles


async def fake_versions(_client: httpx.AsyncClient, params: dict[str, Any]) -> list[Option]:
    return [Option(value=v, label=v) for v in ("1.21.1", "1.20.4")]


async def fake_loader_versions(_client: httpx.AsyncClient, params: dict[str, Any]) -> list[Option]:
    if params.get("loader") != "fabric":
        return []
    return [Option(value="0.16.5", label="0.16.5")]


class FakeRuntime:
    """In-memory stand-in for Docker."""

    def __init__(self, files_root=None):
        self.files = LocalFiles(files_root) if files_root else None
        self.containers: dict[str, ContainerState] = {}
        self.specs: dict[str, ContainerSpec] = {}
        self.installed: list[str] = []
        self.commands: list[tuple[str, str]] = []
        self.log_lines: dict[str, list[str]] = {}
        self.config_files: dict[tuple[str, str], bytes] = {}
        self.fail_install = False

    async def states(self):
        return dict(self.containers)

    async def state(self, server_id):
        return self.containers.get(server_id)

    async def pull(self, image: str, on_log: LogFn):
        on_log(f"pulling {image}")

    async def ensure_volume(self, server_id):
        return f"possum-{server_id}-data"

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
        self.log_lines.setdefault(server_id, []).append(f"> {line}\n")

    async def read_file(self, server_id, path):
        return self.config_files.get((server_id, path))

    async def write_file(self, server_id, path, data):
        self.config_files[(server_id, path)] = data

    async def logs(self, server_id, tail=200, since=0, timestamps=False):
        if since:  # like Docker: nothing newer than the previous stream
            return
        for line in self.log_lines.get(server_id, []):
            yield f"2026-09-16T12:00:00.000000000Z {line}" if timestamps else line

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
def runtime(tmp_path) -> FakeRuntime:
    return FakeRuntime(files_root=tmp_path / "volumes")


ADMIN_PASSWORD = "admin-password"


@pytest.fixture
def client(tmp_path, providers, runtime) -> TestClient:
    """Logged in as an administrator. Use `login()` to act as someone else."""
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        cache_dir=tmp_path / "cache",
        backups_dir=tmp_path / "backups",
    )
    # Like the web UI: every request carries the CSRF header.
    with TestClient(
        create_app(settings, providers, runtime), headers={"X-Requested-With": "possum"}
    ) as test_client:
        add_user(test_client, "admin", ADMIN_PASSWORD, is_admin=True)
        login(test_client, "admin", ADMIN_PASSWORD)
        yield test_client


def add_user(client: TestClient, username: str, password: str, is_admin: bool = False) -> str:
    from app.core.security import hash_password
    from app.models import User

    async def create():
        async with client.app.state.sessionmaker() as session:
            user = User(username=username, password_hash=hash_password(password), is_admin=is_admin)
            session.add(user)
            await session.commit()
            return user.id

    return client.portal.call(create)


def login(client: TestClient, username: str, password: str) -> None:
    client.cookies.clear()
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text


@pytest.fixture(autouse=True)
def no_player_watchers(monkeypatch):
    # Following consoles runs forever in the background; tests feed lines to the player service directly.
    monkeypatch.setattr("app.runtime.players.PlayerService.start", lambda self: None)


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
