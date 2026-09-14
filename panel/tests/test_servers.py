def create(client, template_id="minecraft-java", name="Survival", **values):
    return client.post(
        "/api/servers", json={"template_id": template_id, "name": name, "values": values}
    )


def test_create_minecraft_server_applies_defaults(client):
    resp = create(client, version="1.21.1", eula=True)
    assert resp.status_code == 201, resp.text
    server = resp.json()
    assert server["status"] == "pending"
    assert server["values"] == {"loader": "paper", "version": "1.21.1", "memory_mb": 2048, "eula": True}


def test_generated_secret_is_stored_but_not_returned(client):
    server = create(client, version="1.21.1", eula=True).json()
    assert "rcon_password" not in server["values"]
    assert "rcon_password" not in client.get(f"/api/servers/{server['id']}").json()["values"]


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


def test_list_get_delete(client):
    server_id = create(client, "cs16", name="Public").json()["id"]
    assert [s["id"] for s in client.get("/api/servers").json()] == [server_id]

    assert client.delete(f"/api/servers/{server_id}").status_code == 204
    assert client.get(f"/api/servers/{server_id}").status_code == 404
    assert client.get("/api/servers").json() == []


def test_unknown_template(client):
    assert create(client, "nope").status_code == 404
