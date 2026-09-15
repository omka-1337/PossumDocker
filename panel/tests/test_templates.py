import pytest
from pydantic import ValidationError

from app.games.schema import Template

MINIMAL_RUNTIME = {"image": "alpine"}


def test_bundled_templates_are_listed(client):
    ids = {t["id"] for t in client.get("/api/templates").json()}
    assert {"minecraft-java", "cs16"} <= ids


def test_hidden_secrets_are_not_in_form(client):
    fields = client.get("/api/templates/minecraft-java").json()["fields"]
    assert "rcon_password" not in {f["id"] for f in fields}


def test_install_and_runtime_are_not_exposed(client):
    body = client.get("/api/templates/cs16").json()
    assert "runtime" not in body and "install" not in body


def test_unknown_template_is_404(client):
    assert client.get("/api/templates/nope").status_code == 404


def test_dynamic_options_receive_dependencies(client):
    url = "/api/templates/minecraft-java/fields/loader_version/options"
    assert client.get(url, params={"loader": "fabric", "version": "1.21.1"}).json() == [
        {"value": "0.16.5", "label": "0.16.5"}
    ]
    assert client.get(url, params={"loader": "paper"}).json() == []


def test_static_options_expand_shorthand(client):
    options = client.get("/api/templates/cs16/fields/map/options").json()
    assert {"value": "de_dust2", "label": "de_dust2"} in options


def test_reference_to_later_field_is_rejected():
    with pytest.raises(ValidationError, match="not declared before it"):
        Template.model_validate(
            {
                "id": "broken",
                "name": "Broken",
                "runtime": MINIMAL_RUNTIME,
                "fields": [
                    {"id": "a", "label": "A", "type": "string", "visible_if": {"b": ["x"]}},
                    {"id": "b", "label": "B", "type": "string"},
                ],
            }
        )


def test_select_needs_exactly_one_options_source():
    with pytest.raises(ValidationError, match="exactly one"):
        Template.model_validate(
            {
                "id": "broken",
                "name": "Broken",
                "runtime": MINIMAL_RUNTIME,
                "fields": [{"id": "a", "label": "A", "type": "select"}],
            }
        )


def test_unknown_template_key_is_rejected():
    with pytest.raises(ValidationError):
        Template.model_validate({"id": "x", "name": "X", "runtime": MINIMAL_RUNTIME, "fieldz": []})


def test_icons_are_served_safely(client):
    summary = {t["id"]: t for t in client.get("/api/templates").json()}["cs16"]
    assert summary["icon_url"] == "/api/templates/cs16/icon"
    assert summary["color"] == "#c9862e"
    assert summary["steam_icon_url"] == "/api/templates/cs16/steam/icon"
    assert summary["cover_url"] == "/api/templates/cs16/steam/header"

    resp = client.get(summary["icon_url"])
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert "default-src 'none'" in resp.headers["content-security-policy"]


def test_icon_must_exist_inside_templates_dir(tmp_path):
    from app.games.providers import OptionsProviders
    from app.games.registry import TemplateLoadError, load_templates

    (tmp_path / "game.yaml").write_text(
        "id: game\nname: Game\nicon: icons/missing.svg\nruntime: {image: alpine}\n"
    )
    with pytest.raises(TemplateLoadError, match="not found"):
        load_templates(tmp_path, OptionsProviders(None))

    (tmp_path / "game.yaml").write_text(
        "id: game\nname: Game\nicon: ../../etc/passwd.svg\nruntime: {image: alpine}\n"
    )
    with pytest.raises(TemplateLoadError, match="not found"):
        load_templates(tmp_path, OptionsProviders(None))


def test_steam_art_endpoints(client, tmp_path):
    image = tmp_path / "header.jpg"
    image.write_bytes(b"\xff\xd8jpeg")

    class FakeAssets:
        async def get(self, appid, kind):
            assert appid == 10
            return image if kind == "header" else None

    client.app.state.steam = FakeAssets()
    resp = client.get("/api/templates/cs16/steam/header")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/jpeg"
    assert client.get("/api/templates/cs16/steam/icon").status_code == 404  # Steam had none
    assert client.get("/api/templates/cs16/steam/logo").status_code == 422
    # Not a Steam game: no URLs, and the endpoint says so.
    minecraft = client.get("/api/templates/minecraft-java").json()
    assert minecraft["steam_icon_url"] is None and minecraft["cover_url"] is None
    assert client.get("/api/templates/minecraft-java/steam/header").status_code == 404


def test_console_rules_reach_the_ui(client):
    console = client.get("/api/templates/minecraft-java").json()["console"]
    assert {rule["color"] for rule in console["highlight"]} >= {"red", "yellow"}
    assert console["continuation"]
