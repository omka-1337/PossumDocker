from tests.conftest import wait_for_status


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
