from app.runtime.docker import ContainerState
from tests.conftest import add_user, login, wait_for_status


def create(client, template_id="minecraft-java", name="Survival", **values):
    return client.post("/api/servers", json={"template_id": template_id, "name": name, "values": values})


def create_installed(client, template_id="cs16", **values) -> dict:
    server = create(client, template_id, **values).json()
    return wait_for_status(client, server["id"], "stopped")


def test_create_minecraft_server_applies_defaults(client):
    resp = create(client, version="1.21.1", eula=True)
    assert resp.status_code == 201, resp.text
    server = resp.json()
    assert server["status"] == "installing"
    assert server["values"] == {"loader": "paper", "version": "1.21.1", "memory_mb": 2048, "eula": True}
    assert server["ports"] == {"game": 25565}


def test_install_leaves_server_stopped(client, runtime):
    server = create_installed(client, "minecraft-java", version="1.21.1", eula=True)
    assert server["status"] == "stopped"
    assert runtime.installed == [server["id"]]
    spec = runtime.specs[server["id"]]
    assert spec.image == "itzg/minecraft-server:java21"
    assert spec.env["TYPE"] == "PAPER"
    # Hidden field renders empty and must not end up as an empty env var.
    assert "FABRIC_LOADER_VERSION" not in spec.env
    assert "install" in "\n".join(client.get(f"/api/servers/{server['id']}/install-log").json())


def test_failed_install_is_reported(client, runtime):
    runtime.fail_install = True
    server_id = create(client, "cs16").json()["id"]
    server = wait_for_status(client, server_id, "install_failed")
    assert server["status"] == "install_failed"
    assert "exited with code 1" in server["status_message"]


def test_second_server_gets_next_port(client):
    first = create(client, "cs16", name="one").json()
    second = create(client, "cs16", name="two").json()
    assert (first["ports"]["game"], second["ports"]["game"]) == (27015, 27016)


def test_start_stop(client, runtime):
    server = create_installed(client)
    url = f"/api/servers/{server['id']}"

    assert client.post(f"{url}/start").json()["status"] == "running"
    assert client.post(f"{url}/command", json={"command": "status"}).status_code == 204

    client.post(f"{url}/stop")
    assert wait_for_status(client, server["id"], "stopped")["status"] == "stopped"
    # cs16 template stops with its console command.
    assert runtime.commands == [(server["id"], "status"), (server["id"], "quit")]


def test_command_requires_running_server(client):
    server = create_installed(client)
    resp = client.post(f"/api/servers/{server['id']}/command", json={"command": "status"})
    assert resp.status_code == 409


def test_start_recreates_missing_container(client, runtime):
    server = create_installed(client)
    runtime.containers.clear()  # someone ran `docker rm`
    assert client.post(f"/api/servers/{server['id']}/start").json()["status"] == "running"


def test_generated_secret_is_stored_but_not_returned(client, runtime):
    server = create_installed(client)
    assert "rcon_password" not in server["values"]
    # ...but it did reach the container.
    assert "+rcon_password" in runtime.specs[server["id"]].command


def test_eula_must_be_accepted(client):
    resp = create(client, version="1.21.1", eula=False)
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"] == {"eula": "must be accepted"}


def test_errors_are_reported_per_field(client):
    resp = create(client, version="0.0.1", memory_mb=100, bogus=1)
    errors = resp.json()["detail"]["errors"]
    assert errors == {
        "version": "not one of the available options",
        "memory_mb": "must be at least 512",
        "eula": "must be accepted",
        "bogus": "unknown field",
    }


def test_loader_version_only_required_for_fabric(client):
    resp = create(client, loader="fabric", version="1.21.1", eula=True)
    assert resp.json()["detail"]["errors"] == {"loader_version": "required"}

    resp = create(client, loader="fabric", version="1.21.1", loader_version="0.16.5", eula=True)
    assert resp.status_code == 201


def test_hidden_field_values_are_dropped(client):
    resp = create(client, loader="paper", version="1.21.1", loader_version="0.16.5", eula=True)
    assert "loader_version" not in resp.json()["values"]


def test_bool_is_not_a_number(client):
    resp = create(client, "cs16", max_players=True)
    assert resp.json()["detail"]["errors"] == {"max_players": "must be an integer"}


def test_list_get_delete(client, runtime):
    server_id = create_installed(client, name="Public")["id"]
    assert [s["id"] for s in client.get("/api/servers").json()] == [server_id]

    assert client.delete(f"/api/servers/{server_id}").status_code == 204
    assert server_id not in runtime.containers
    assert client.get(f"/api/servers/{server_id}").status_code == 404
    assert client.get("/api/servers").json() == []


def test_unknown_template(client):
    assert create(client, "nope").status_code == 404


def test_edit_game_settings(client, runtime):
    server = create_installed(client, "cs16", name="Old")
    url = f"/api/servers/{server['id']}"
    password = runtime.specs[server["id"]].command[
        runtime.specs[server["id"]].command.index("+rcon_password") + 1
    ]

    resp = client.patch(url, json={"name": "New", "values": {"vac": False, "max_players": 12}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["server"]["name"] == "New"
    assert body["server"]["values"] == {"map": "de_dust2", "max_players": 12, "vac": False}
    assert body["restart_required"] is False and body["reinstalling"] is False

    # Applies on the next start, and the generated RCON password survives the edit.
    client.post(f"{url}/start")
    command = runtime.specs[server["id"]].command
    assert "-insecure" in command and "12" in command
    assert command[command.index("+rcon_password") + 1] == password


def test_edit_while_running_asks_for_restart(client):
    server = create_installed(client, "cs16")
    url = f"/api/servers/{server['id']}"
    client.post(f"{url}/start")
    assert client.patch(url, json={"values": {"map": "de_nuke"}}).json()["restart_required"] is True


def test_non_editable_fields_are_rejected(client):
    server = create_installed(client, "minecraft-java", version="1.21.1", eula=True)
    resp = client.patch(f"/api/servers/{server['id']}", json={"values": {"version": "1.20.4", "bogus": 1}})
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"] == {
        "version": "can't be changed after the server is created",
        "bogus": "unknown field",
    }
    resp = client.patch(f"/api/servers/{server['id']}", json={"values": {"memory_mb": 100}})
    assert resp.json()["detail"]["errors"] == {"memory_mb": "must be at least 512"}


def test_edit_does_not_recheck_unchanged_versions(client, providers):
    server = create_installed(client, "minecraft-java", version="1.21.1", eula=True)

    async def offline(_client, _params):
        raise __import__("httpx").ConnectError("offline")

    providers.register("minecraft.versions", offline)
    resp = client.patch(f"/api/servers/{server['id']}", json={"values": {"memory_mb": 4096}})
    assert resp.status_code == 200, resp.text


def test_start_refuses_a_running_server(client):
    server = create_installed(client, "cs16")
    client.post(f"/api/servers/{server['id']}/start")
    assert client.post(f"/api/servers/{server['id']}/start").status_code == 409


def test_a_crash_is_not_a_stop(client, runtime):
    server = create_installed(client, "cs16")
    url = f"/api/servers/{server['id']}"
    client.post(f"{url}/start")

    # Docker gave up restarting it.
    runtime.containers[server["id"]] = ContainerState("exited", exit_code=137, oom_killed=True)
    crashed = client.get(url).json()
    assert crashed["status"] == "crashed"
    assert crashed["status_message"] == "ran out of memory (exit code 137)"
    assert client.get("/api/servers").json()[0]["status"] == "crashed"

    # The game quitting cleanly by itself is just stopped.
    runtime.containers[server["id"]] = ContainerState("exited", exit_code=0)
    assert client.get(url).json()["status"] == "stopped"

    # Stopped from the panel, a non-zero exit is no crash either.
    runtime.containers[server["id"]] = ContainerState("running")
    client.post(f"{url}/stop")
    wait_for_status(client, server["id"], "stopped")
    runtime.containers[server["id"]] = ContainerState("exited", exit_code=143)
    assert client.get(url).json()["status"] == "stopped"


def test_servers_that_were_running_start_again_with_the_panel(client, runtime):
    server = create_installed(client, "cs16")
    other = create_installed(client, "cs16", name="other")
    client.post(f"/api/servers/{server['id']}/start")
    runtime.containers[server["id"]] = ContainerState("exited", exit_code=255)  # the host rebooted
    runtime.containers[other["id"]] = ContainerState("exited", exit_code=0)

    client.portal.call(client.app.state.manager._resume)
    assert runtime.containers[server["id"]].status == "running"
    assert runtime.containers[other["id"]].status == "exited"  # never started from the panel


def test_resource_limits(client, runtime):
    server = create_installed(client, "cs16")
    url = f"/api/servers/{server['id']}"
    assert server["limits"] == {
        "memory_mb": None,
        "cpus": None,
        "disk_mb": None,
        "backups_mb": None,
        "default": {"memory_mb": 512, "cpus": None, "disk_mb": 2048, "backups_mb": 4096},
    }

    client.post(f"{url}/start")
    resp = client.patch(url, json={"memory_limit_mb": 1024, "cpu_limit": 2})
    assert resp.status_code == 200, resp.text
    assert resp.json()["restart_required"] is True
    assert resp.json()["server"]["limits"]["memory_mb"] == 1024

    client.post(f"{url}/stop")
    wait_for_status(client, server["id"], "stopped")
    client.post(f"{url}/start")
    assert (runtime.specs[server["id"]].memory_mb, runtime.specs[server["id"]].cpus) == (1024, 2)

    # Back to the template's default.
    assert client.patch(url, json={"memory_limit_mb": None}).json()["server"]["limits"]["memory_mb"] is None
    assert client.patch(url, json={"memory_limit_mb": 10}).status_code == 422


def test_only_administrators_change_limits(client):
    from tests.test_auth import PASSWORD, grant  # it imports this module

    server = create_installed(client, "cs16")
    user_id = add_user(client, "player", PASSWORD)
    grant(client, server["id"], user_id, "settings")
    login(client, "player", PASSWORD)
    url = f"/api/servers/{server['id']}"
    assert client.patch(url, json={"memory_limit_mb": 0}).status_code == 403
    assert client.patch(url, json={"name": "Renamed"}).status_code == 200


def test_change_ports(client, runtime):
    valheim = create_installed(client, "valheim", password="secret123")
    cs = create_installed(client, "cs16")
    url = f"/api/servers/{valheim['id']}"

    resp = client.patch(url, json={"ports": {"game": 3456}})
    assert resp.status_code == 200, resp.text
    # The query port follows the game port.
    assert resp.json()["server"]["ports"] == {"game": 3456, "query": 3457}
    assert client.post(f"{url}/start").status_code == 200
    assert runtime.specs[valheim["id"]].env["SERVER_PORT"] == "3456"

    errors = client.patch(url, json={"ports": {"query": 4000}}).json()["detail"]["errors"]
    assert errors == {"port.query": "moves with game"}
    # The query port would land on udp 27015, the CS server's.
    errors = client.patch(url, json={"ports": {"game": cs["ports"]["game"] - 1}}).json()["detail"]["errors"]
    assert errors == {"port.query": "27015/udp is used by another server"}
    assert client.patch(url, json={"ports": {"game": 70000}}).status_code == 422
    assert client.patch(url, json={"ports": {"game": 3500}}).json()["restart_required"] is True


def test_a_port_taken_by_another_program_is_explained(client, runtime, monkeypatch):
    from app.runtime.state import RuntimeUnavailable

    server = create_installed(client, "cs16")

    async def start(server_id):
        raise RuntimeUnavailable(
            "docker: failed to set up container networking: driver failed programming external connectivity"
            " on endpoint possum-x (81a2):"
            " failed to bind host port 0.0.0.0:27015/udp: address already in use (500)"
        )

    monkeypatch.setattr(runtime, "start", start)
    resp = client.post(f"/api/servers/{server['id']}/start")
    assert resp.status_code == 409
    assert resp.json()["detail"].startswith("port 27015/udp is already used by another program")
