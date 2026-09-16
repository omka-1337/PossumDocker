import json

import pytest

from app.games.schema import Template
from app.runtime.players import parse_line, player_key, split_timestamp
from app.runtime.state import ContainerState
from tests.conftest import add_user, login
from tests.test_runtime import load_template
from tests.test_servers import create_installed

T = "2026-09-16T12:00:00.000000000Z"


def events(template_id: str, lines: list[str]) -> list[tuple[str, dict]]:
    spec = load_template(template_id).players
    parsed = [parse_line(spec, split_timestamp(f"{T} {line}")[1]) for line in lines]
    return [(p.on, p.groups) if p else None for p in parsed]


def test_minecraft_java_lines():
    spec = load_template("minecraft-java").players
    assert events(
        "minecraft-java",
        [
            "[12:00:00 INFO]: UUID of player Steve is 069a79f4-44e9-4726-a5be-fca90e38aaf5",
            "[12:00:00 INFO]: Steve[/203.0.113.9:53012] logged in with entity id 123 at ([world]0, 64, 0)",
            "[12:00:01] [Server thread/INFO]: Steve left the game",
            # Chat can't pass for a join or a leave.
            "[12:00:02 INFO]: <Alex> Steve[/1.2.3.4:1] logged in with entity id 1",
            "[12:00:02 INFO]: <Alex> Steve left the game",
        ],
    ) == [
        ("info", {"name": "Steve", "id": "069a79f4-44e9-4726-a5be-fca90e38aaf5"}),
        ("join", {"name": "Steve", "ip": "203.0.113.9"}),
        ("leave", {"name": "Steve"}),
        None,
        None,
    ]
    assert player_key(spec, {"name": "Steve", "id": "x"}) == "name:Steve"


def test_counter_strike_lines():
    spec = load_template("cs16").players
    prefix = "L 09/16/2026 - 12:00:00: "
    parsed = events(
        "cs16",
        [
            prefix + '"Steve<2><STEAM_ID_PENDING><>" connected, address "203.0.113.9:27005"',
            prefix + '"Steve<2><STEAM_0:1:12345><>" entered the game',
            prefix + '"Steve<2><STEAM_0:1:12345><CT>" disconnected',
            prefix + '"Bot<3><BOT><>" entered the game',
            prefix + '"Steve<2><STEAM_0:1:12345><CT>" say "x<4><STEAM_0:0:1><>" entered the game"',
        ],
    )
    assert parsed[0] == (
        "info",
        {"name": "Steve", "slot": "2", "id": "STEAM_ID_PENDING", "ip": "203.0.113.9"},
    )
    assert parsed[1] == ("join", {"name": "Steve", "slot": "2", "id": "STEAM_0:1:12345"})
    assert parsed[2][0] == "leave"
    assert parsed[3] is None  # bots aren't players
    assert parsed[4] is None  # nor is someone quoting a join in chat
    # LAN clients all share an id: told apart by name.
    assert player_key(spec, {"name": "Steve", "id": "STEAM_ID_LAN"}) == "name:Steve"


def test_other_games_lines():
    assert events("factorio", ["2026-09-16 12:00:00 [JOIN] Steve joined the game"]) == [
        ("join", {"name": "Steve"})
    ]
    assert events(
        "minecraft-bedrock", ["[2026-09-16 12:00:00:000 INFO] Player connected: Steve Two, xuid: 2535"]
    ) == [("join", {"name": "Steve Two", "id": "2535"})]
    assert events(
        "valheim",
        [
            "09/16/2026 12:00:00: Got connection SteamID 76561198000000001",
            "09/16/2026 12:00:05: Got character ZDOID from Viking : -1234:1",
        ],
    ) == [("join", {"id": "76561198000000001"}), ("name", {"name": "Viking"})]


def feed(client, server_id: str, *lines: str) -> None:
    manager = client.app.state.manager

    async def run():
        from app.models import Server

        async with manager._sessionmaker() as session:
            server = await session.get(Server, server_id)
        for line in lines:
            await manager.players.handle_line(server, f"{T} {line}")

    client.portal.call(run)


def players(client, server_id: str) -> dict:
    resp = client.get(f"/api/servers/{server_id}/players")
    assert resp.status_code == 200, resp.text
    return resp.json()


MC_JOIN = [
    "[12:00:00 INFO]: UUID of player Steve is 069a79f4-44e9-4726-a5be-fca90e38aaf5",
    "[12:00:00 INFO]: Steve[/203.0.113.9:53012] logged in with entity id 123 at (0, 64, 0)",
]


def minecraft(client):
    return create_installed(client, "minecraft-java", version="1.21.1", eula=True)


def test_joins_and_leaves_become_players(client):
    server = minecraft(client)
    feed(client, server["id"], *MC_JOIN)
    data = players(client, server["id"])
    [steve] = data["players"]
    assert steve | {"first_seen": None, "last_seen": None, "online_since": None} == {
        "key": "name:Steve",
        "name": "Steve",
        "game_id": "069a79f4-44e9-4726-a5be-fca90e38aaf5",
        "ip": "203.0.113.9",
        "online": True,
        "first_seen": None,
        "last_seen": None,
        "online_since": None,
    }
    assert data["abilities"] == {
        "tracked": True,
        "refresh": True,
        "kick": True,
        "ban_by": "name",
        "ip_bans": True,
        "bans_need_restart": False,
        "bans_by_panel": False,
    }
    feed(client, server["id"], "[12:30:00 INFO]: Steve left the game")
    [steve] = players(client, server["id"])["players"]
    assert steve["online"] is False and steve["last_seen"].startswith("2026-09-16T12:00:00")


def test_minecraft_bans_use_commands_while_running_and_the_file_when_stopped(client, runtime):
    server = minecraft(client)
    sid = server["id"]
    url = f"/api/servers/{sid}/players"
    feed(client, sid, *MC_JOIN)

    client.post(f"/api/servers/{sid}/start")
    assert client.post(f"{url}/kick", json={"key": "name:Steve"}).status_code == 204
    resp = client.post(f"{url}/ban", json={"kind": "player", "key": "name:Steve", "reason": "griefing"})
    assert resp.json() == {"effective": "now"}
    assert client.post(f"{url}/ban", json={"kind": "ip", "key": "name:Steve"}).status_code == 200
    assert runtime.commands[-3:] == [
        (sid, "kick Steve"),
        (sid, "ban Steve griefing"),
        (sid, "ban-ip 203.0.113.9"),
    ]

    client.post(f"/api/servers/{sid}/stop")
    runtime.containers[sid] = ContainerState("exited", exit_code=0)
    resp = client.post(f"{url}/ban", json={"kind": "player", "key": "name:Steve", "reason": "again"})
    assert resp.json() == {"effective": "now"}
    [entry] = json.loads(runtime.config_files[(sid, "/data/banned-players.json")])
    assert (entry["uuid"], entry["name"], entry["reason"], entry["expires"]) == (
        "069a79f4-44e9-4726-a5be-fca90e38aaf5",
        "Steve",
        "again",
        "forever",
    )
    assert players(client, sid)["bans"] == [
        {"kind": "player", "value": "Steve", "name": None, "reason": "again"}
    ]

    # Someone who never joined has no UUID yet: only the running game can look them up.
    resp = client.post(f"{url}/ban", json={"kind": "player", "value": "Stranger"})
    assert resp.status_code == 409

    assert client.post(f"{url}/unban", json={"kind": "player", "value": "Steve"}).json() == {
        "effective": "now"
    }
    assert json.loads(runtime.config_files[(sid, "/data/banned-players.json")]) == []


def test_counter_strike_bans_by_steam_id(client, runtime):
    server = create_installed(client, "cs16")
    sid = server["id"]
    url = f"/api/servers/{sid}/players"
    prefix = "L 09/16/2026 - 12:00:00: "
    feed(
        client,
        sid,
        prefix + '"Steve<2><STEAM_ID_PENDING><>" connected, address "203.0.113.9:27005"',
        prefix + '"Steve<2><STEAM_0:1:12345><>" entered the game',
    )
    [steve] = players(client, sid)["players"]
    assert (steve["key"], steve["ip"], steve["online"]) == ("id:STEAM_0:1:12345", "203.0.113.9", True)

    client.post(f"/api/servers/{sid}/start")
    client.post(f"{url}/kick", json={"key": steve["key"]})
    client.post(f"{url}/ban", json={"kind": "player", "key": steve["key"]})
    assert runtime.commands[-3:] == [
        (sid, "kick #2"),
        (sid, "banid 0 STEAM_0:1:12345 kick"),
        (sid, "writeid"),
    ]

    client.post(f"/api/servers/{sid}/stop")
    runtime.containers[sid] = ContainerState("exited", exit_code=0)
    client.post(f"{url}/ban", json={"kind": "ip", "value": "198.51.100.7"})
    assert runtime.config_files[(sid, "/data/cstrike/listip.cfg")] == b"addip 0.000000 198.51.100.7\n"
    runtime.config_files[(sid, "/data/cstrike/banned.cfg")] = b"banid 0.000000 STEAM_0:1:12345\n"
    bans = players(client, sid)["bans"]
    assert {"kind": "player", "value": "STEAM_0:1:12345", "name": "Steve", "reason": None} in bans


def test_valheim_bans_wait_for_a_restart(client, runtime):
    server = create_installed(client, "valheim", password="secret123")
    sid = server["id"]
    feed(client, sid, "Got connection SteamID 76561198000000001", "Got character ZDOID from Viking : -1:1")
    [viking] = players(client, sid)["players"]
    assert viking["name"] == "Viking"
    client.post(f"/api/servers/{sid}/start")
    resp = client.post(f"/api/servers/{sid}/players/ban", json={"kind": "player", "key": viking["key"]})
    assert resp.json() == {"effective": "after restart"}
    assert runtime.config_files[(sid, "/config/bannedlist.txt")] == b"76561198000000001\n"
    assert client.post(f"/api/servers/{sid}/players/kick", json={"key": viking["key"]}).status_code == 409


def test_bedrock_bans_are_kept_by_the_panel(client, runtime):
    server = create_installed(client, "minecraft-bedrock", eula=True)
    sid = server["id"]
    client.post(f"/api/servers/{sid}/start")
    resp = client.post(f"/api/servers/{sid}/players/ban", json={"kind": "player", "value": "Griefer"})
    assert resp.json() == {"effective": "on join"}
    feed(client, sid, "[2026-09-16 12:00:00:000 INFO] Player connected: Griefer, xuid: 2535")
    assert runtime.commands[-1] == (sid, 'kick "Griefer" banned')
    assert players(client, sid)["bans"][0]["value"] == "name:Griefer"


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "player", "value": "Steve\nop Mallory"},
        {"kind": "player", "value": "Steve; op Mallory"},
        {"kind": "player", "value": "Steve", "reason": "x\nop Mallory"},
        {"kind": "ip", "value": "not-an-address"},
    ],
)
def test_nothing_extra_reaches_the_console(client, runtime, body):
    server = minecraft(client)
    client.post(f"/api/servers/{server['id']}/start")
    before = list(runtime.commands)
    resp = client.post(f"/api/servers/{server['id']}/players/ban", json=body)
    assert resp.status_code == 422, resp.text
    assert runtime.commands == before


def test_refresh_asks_the_game(client, runtime, monkeypatch):
    server = minecraft(client)
    sid = server["id"]
    feed(client, sid, *MC_JOIN)  # Steve is online according to the log
    client.post(f"/api/servers/{sid}/start")

    async def logs(server_id, tail=200, since=0, timestamps=False):
        import time
        from datetime import UTC, datetime

        stamp = datetime.fromtimestamp(time.time() + 0.1, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        yield f"{stamp} [12:00:00 INFO]: There are 1 of a max of 20 players online: Alex\n"

    monkeypatch.setattr(runtime, "logs", logs)
    client.app.state.templates["minecraft-java"].players.status.wait = 0.5
    assert client.post(f"/api/servers/{sid}/players/refresh").status_code == 204
    online = {p["name"]: p["online"] for p in players(client, sid)["players"]}
    assert online == {"Alex": True, "Steve": False}
    assert (sid, "list") in runtime.commands


def stamped(*lines: str):
    import time
    from datetime import UTC, datetime

    async def logs(server_id, tail=200, since=0, timestamps=False):
        stamp = datetime.fromtimestamp(time.time() + 0.1, UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        for line in lines:
            yield f"{stamp} {line}\n"

    return logs


def test_no_answer_changes_nothing(client, runtime, monkeypatch):
    server = minecraft(client)
    sid = server["id"]
    feed(client, sid, *MC_JOIN)
    client.post(f"/api/servers/{sid}/start")
    monkeypatch.setattr(runtime, "logs", stamped("[12:00:00 INFO]: something else"))
    client.app.state.templates["minecraft-java"].players.status.wait = 0.5
    resp = client.post(f"/api/servers/{sid}/players/refresh")
    assert resp.status_code == 409 and "didn't answer" in resp.json()["detail"]
    assert players(client, sid)["players"][0]["online"] is True


def test_counter_strike_status_rows(client, runtime, monkeypatch):
    server = create_installed(client, "cs16")
    sid = server["id"]
    client.post(f"/api/servers/{sid}/start")
    monkeypatch.setattr(
        runtime,
        "logs",
        stamped(
            "hostname:  Counter-Strike 1.6 Server",
            "players :  2 active (20 max)",
            "#      name userid uniqueid frag time ping loss adr",
            '# 1 "Steve"  2 STEAM_0:1:12345   3 01:23   25    0 203.0.113.9:27005',
            '# 2 "Bot"  3 BOT   0 01:00    0    0',
            "2 users",
        ),
    )
    client.app.state.templates["cs16"].players.status.wait = 1
    assert client.post(f"/api/servers/{sid}/players/refresh").status_code == 204
    [steve] = players(client, sid)["players"]
    assert (steve["key"], steve["ip"], steve["online"]) == ("id:STEAM_0:1:12345", "203.0.113.9", True)


def test_players_need_their_permission(client):
    from tests.test_auth import PASSWORD, grant

    server = minecraft(client)
    user_id = add_user(client, "player", PASSWORD)
    grant(client, server["id"], user_id, "console")
    login(client, "player", PASSWORD)
    assert client.get(f"/api/servers/{server['id']}/players").status_code == 403


def test_template_players_section_is_checked():
    base = {"id": "g", "name": "G", "runtime": {"image": "alpine"}}
    with pytest.raises(ValueError, match="kick"):
        Template.model_validate({**base, "players": {"bans": {"by_panel": True}}})
    with pytest.raises(ValueError, match="pattern"):
        Template.model_validate({**base, "players": {"events": [{"type": "join", "pattern": "("}]}})
