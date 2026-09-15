import pytest

from app.games.configs import ConfigDocument, ConfigValuesError, validate_config_values
from app.games.schema import ConfigFile
from tests.test_servers import create_installed

PROPERTIES = b"""#Minecraft server properties
#Mon Sep 14 23:17:37 UTC 2026
difficulty=easy
motd=A Minecraft Server
server-port=25565
level-seed=
"""


def test_properties_round_trip_keeps_untouched_lines():
    doc = ConfigDocument("properties", PROPERTIES)
    assert doc.render() == PROPERTIES
    assert doc.items() == {
        "difficulty": "easy",
        "motd": "A Minecraft Server",
        "server-port": "25565",
        "level-seed": "",
    }


def test_properties_set_changes_one_line_and_appends_new_keys():
    doc = ConfigDocument("properties", PROPERTIES)
    doc.set("difficulty", "hard")
    doc.set("new-key", "x")
    text = doc.render().decode()
    assert "difficulty=hard\nmotd=A Minecraft Server" in text
    assert text.startswith("#Minecraft server properties\n#Mon Sep")
    assert text.endswith("new-key=x\n")


@pytest.mark.parametrize(
    "value",
    ["Welcome: to my=server #1!", "  leading spaces", "Привіт, світ", "emoji 😀", "back\\slash"],
)
def test_properties_escaping_round_trips(value):
    doc = ConfigDocument("properties", b"")
    doc.set("motd", value)
    rendered = doc.render()
    rendered.decode("ascii")  # everything non-ASCII is \u-escaped, like Java does
    assert ConfigDocument("properties", rendered).items() == {"motd": value}


def test_properties_parses_java_escapes_and_separators():
    doc = ConfigDocument("properties", b"motd=\\u00a7aGreen\\: text\nkey value\nother : spaced\n")
    assert doc.items() == {"motd": "§aGreen: text", "key": "value", "other": "spaced"}


def test_latin1_file_stays_latin1():
    data = "motd=caf\xe9\n".encode("latin-1")
    doc = ConfigDocument("properties", data)
    assert doc.items() == {"motd": "café"}
    assert doc.render() == data


CFG = b"""// server.cfg
hostname "Counter-Strike 1.6 Server"
rcon_password ""
mp_timelimit 30 // minutes
sv_password ""
"""


def test_cvars_parse_and_set():
    doc = ConfigDocument("cvars", CFG)
    assert doc.items() == {
        "hostname": "Counter-Strike 1.6 Server",
        "rcon_password": "",
        "mp_timelimit": "30",
        "sv_password": "",
    }
    doc.set("mp_timelimit", "45")
    doc.set("sv_alltalk", "1")
    text = doc.render().decode()
    assert '// server.cfg\nhostname "Counter-Strike 1.6 Server"' in text
    assert 'mp_timelimit "45"\n' in text
    assert text.endswith('sv_alltalk "1"\n')


CONFIG = ConfigFile.model_validate(
    {
        "id": "cfg",
        "label": "cfg",
        "path": "server.cfg",
        "format": "cvars",
        "managed": ["rcon_password"],
        "hints": {
            "mp_timelimit": {"label": "Time", "type": "number", "min": 0, "max": 100},
            "sv_alltalk": {"label": "All talk", "type": "boolean", "true_value": "1", "false_value": "0"},
            "map": {"label": "Map", "type": "select", "options": ["de_dust2"]},
        },
    }
)


def test_config_validation():
    with pytest.raises(ConfigValuesError) as exc:
        validate_config_values(
            CONFIG,
            {
                "rcon_password": "x",
                "mp_timelimit": "500",
                "sv_alltalk": "true",
                "map": "de_nope",
                "hostname": 'bad "quote"',
                "other": "two\nlines",
            },
        )
    assert exc.value.errors == {
        "rcon_password": "managed by the panel",
        "mp_timelimit": "must be at most 100",
        "sv_alltalk": "must be 1 or 0",
        "map": "not one of the available options",
        "hostname": 'cannot contain "',
        "other": "must be a single line",
    }
    assert validate_config_values(CONFIG, {"mp_timelimit": "20", "sv_alltalk": "1"})


@pytest.mark.parametrize("path", ["../etc/passwd", "/etc/passwd", "a/../../b"])
def test_config_path_cannot_escape_data_dir(path):
    with pytest.raises(ValueError):
        ConfigFile.model_validate({"id": "x", "label": "x", "path": path, "format": "cvars"})


# --- API ----------------------------------------------------------------------

MC_PATH = "/data/server.properties"


def minecraft_server(client):
    return create_installed(client, "minecraft-java", version="1.21.1", eula=True)


def test_config_before_first_start(client):
    server = minecraft_server(client)
    assert client.get(f"/api/servers/{server['id']}/configs").json() == [
        {"id": "server_properties", "label": "Server properties", "path": "server.properties"}
    ]
    config = client.get(f"/api/servers/{server['id']}/configs/server_properties").json()
    assert config["exists"] is False and config["entries"] == []


def test_config_shows_only_keys_in_the_file(client, runtime):
    server = minecraft_server(client)
    runtime.config_files[(server["id"], MC_PATH)] = PROPERTIES + b"custom-mod-key=1\n"

    entries = client.get(f"/api/servers/{server['id']}/configs/server_properties").json()["entries"]
    keys = [e["key"] for e in entries]
    # Hinted keys in template order (motd before difficulty), then the rest; no hints for missing keys.
    assert keys == ["motd", "difficulty", "level-seed", "server-port", "custom-mod-key"]
    assert "pvp" not in keys
    port = next(e for e in entries if e["key"] == "server-port")
    assert port["managed"] is True and port["hint"] is None


def test_config_update(client, runtime):
    server = minecraft_server(client)
    runtime.config_files[(server["id"], MC_PATH)] = PROPERTIES
    url = f"/api/servers/{server['id']}/configs/server_properties"

    resp = client.put(url, json={"values": {"difficulty": "hard", "motd": "Hi: all"}})
    assert resp.status_code == 200, resp.text
    written = runtime.config_files[(server["id"], MC_PATH)].decode()
    assert "difficulty=hard\n" in written and "motd=Hi\\: all\n" in written

    resp = client.put(url, json={"values": {"server-port": "1", "difficulty": "insane"}})
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"] == {
        "server-port": "managed by the panel",
        "difficulty": "not one of the available options",
    }


def test_config_unknown_id(client):
    server = minecraft_server(client)
    assert client.get(f"/api/servers/{server['id']}/configs/nope").status_code == 404


FACTORIO = b"""{
  "name": "Name of the game",
  "_comment_name": "This is a comment",
  "max_players": 0,
  "visibility": { "public": true, "lan": true },
  "tags": ["game", "tags"],
  "autosave_interval": 10,
  "afk_autokick_interval": 0.5
}
"""


def test_json_values_keep_their_types():
    doc = ConfigDocument("json", FACTORIO)
    items = doc.items()
    assert items["visibility.public"] == "true" and items["max_players"] == "0"
    assert "_comment_name" not in items and "tags" not in items  # comments and lists aren't editable

    doc.set("visibility.public", "false")
    doc.set("max_players", "12")
    doc.set("name", "Привіт")
    doc.set("afk_autokick_interval", "2.5")
    data = __import__("json").loads(doc.render())
    assert data["visibility"] == {"public": False, "lan": True}
    assert data["max_players"] == 12 and data["name"] == "Привіт" and data["afk_autokick_interval"] == 2.5
    assert data["tags"] == ["game", "tags"] and data["_comment_name"] == "This is a comment"


@pytest.mark.parametrize(
    ("key", "value", "error"),
    [("max_players", "lots", "must be an integer"), ("visibility.lan", "yes", "must be true or false"),
     ("nope", "1", "not a setting in this file")],
)  # fmt: skip
def test_json_type_errors(key, value, error):
    doc = ConfigDocument("json", FACTORIO)
    with pytest.raises(ConfigValuesError) as exc:
        doc.set(key, value)
    assert exc.value.errors == {key: error}


def test_broken_json_is_reported():
    with pytest.raises(ValueError, match="valid JSON"):
        ConfigDocument("json", b"{not json")


def test_config_path_inside_mounts(client, runtime):
    server = create_installed(client, "valheim", password="secret123")
    manager = client.app.state.manager
    template = manager._templates["valheim"]
    from app.games.schema import ConfigFile as CF

    config = CF.model_validate({"id": "x", "label": "x", "path": "config/adminlist.txt", "format": "cvars"})
    db_server = type("S", (), {"template_id": "valheim", "id": server["id"]})()
    assert manager._config_path(db_server, config) == "/config/adminlist.txt"
    assert template.runtime.mounts


def test_factorio_settings_through_the_api(client, runtime):
    server = create_installed(client, "factorio")
    runtime.config_files[(server["id"], "/factorio/config/server-settings.json")] = FACTORIO
    url = f"/api/servers/{server['id']}/configs/server_settings"

    keys = [e["key"] for e in client.get(url).json()["entries"]]
    assert keys[:2] == ["name", "max_players"]  # hinted ones in template order

    resp = client.put(url, json={"values": {"visibility.public": "false", "max_players": "lots"}})
    assert resp.status_code == 422
    assert resp.json()["detail"]["errors"] == {"max_players": "must be an integer"}
