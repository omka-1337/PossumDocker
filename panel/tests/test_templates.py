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
