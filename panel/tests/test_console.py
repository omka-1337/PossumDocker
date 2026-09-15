import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect

from app.runtime import console as console_module
from app.runtime.console import follow_console
from app.runtime.docker import ContainerState
from tests.test_servers import create_installed


def test_console_streams_history_and_runs_commands(client, runtime):
    server = create_installed(client)
    runtime.log_lines[server["id"]] = ["Server started\n", "Done (3.2s)!\n"]
    client.post(f"/api/servers/{server['id']}/start")

    with client.websocket_connect(f"/api/servers/{server['id']}/console") as ws:
        assert ws.receive_json() == {"type": "log", "data": "Server started"}
        assert ws.receive_json() == {"type": "log", "data": "Done (3.2s)!"}

        ws.send_json({"type": "command", "data": "status"})
        ws.send_json({"type": "nonsense"})
        assert ws.receive_json() == {"type": "error", "data": "invalid message"}

    assert (server["id"], "status") in runtime.commands


def test_console_refuses_commands_when_stopped(client):
    server = create_installed(client)
    with client.websocket_connect(f"/api/servers/{server['id']}/console") as ws:
        ws.send_json({"type": "command", "data": "status"})
        assert ws.receive_json() == {"type": "error", "data": "the server is not running"}


def test_console_for_unknown_server_is_closed(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/servers/nope/console") as ws:
            ws.receive_json()
    assert exc.value.code == 4404


class RestartingRuntime:
    """First run prints two lines and stops; then the container runs again and prints one more."""

    def __init__(self):
        self.runs = [["a\n", "b\n"], ["c\n"]]
        self.calls = []

    async def state(self, server_id):
        return ContainerState(status="running")

    async def logs(self, server_id, tail=200, since=0):
        self.calls.append((tail, since))
        for line in self.runs.pop(0) if self.runs else []:
            yield line
        if not self.runs:
            await asyncio.sleep(3600)  # still running, nothing new


async def test_follow_console_continues_after_restart(monkeypatch):
    monkeypatch.setattr(console_module, "POLL_SECONDS", 0)
    runtime = RestartingRuntime()
    lines = []

    async def collect():
        async for line in follow_console(runtime, "s1"):
            lines.append(line)
            if len(lines) == 3:
                return

    await asyncio.wait_for(collect(), 1)
    assert lines == ["a\n", "b\n", "c\n"]
    # History only the first time, then only lines newer than the previous stream.
    assert runtime.calls[0] == (console_module.HISTORY_LINES, 0)
    assert runtime.calls[1][0] is None and runtime.calls[1][1] > 0


def test_games_without_console_refuse_commands(client, runtime):
    server = create_installed(client, "valheim", password="secret123")
    client.post(f"/api/servers/{server['id']}/start")
    resp = client.post(f"/api/servers/{server['id']}/command", json={"command": "save"})
    assert resp.status_code == 409 and "no console" in resp.json()["detail"]
    assert client.get("/api/templates/valheim").json()["console"]["commands"] is False

    resp = client.post(
        f"/api/servers/{server['id']}/schedules",
        json={"name": "x", "cron": "0 4 * * *", "action": "command", "command": "save"},
    )
    assert resp.status_code == 422 and "action" in resp.json()["detail"]["errors"]
