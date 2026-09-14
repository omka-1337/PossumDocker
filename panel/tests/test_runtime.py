import pytest

from app.core.config import REPO_ROOT
from app.games.schema import Port, Template
from app.models import Server
from app.runtime.docker import _health_from_status
from app.runtime.ports import allocate_ports
from app.runtime.spec import build_spec, minecraft_java_tag

TEMPLATES_DIR = REPO_ROOT / "templates"


@pytest.mark.parametrize(
    ("version", "tag"),
    [
        ("1.12.2", "java8"),
        ("1.16.5", "java8"),
        ("1.17", "java17"),
        ("1.20.4", "java17"),
        ("1.20.5", "java21"),
        ("1.21.1", "java21"),
        ("26.1", "latest"),
    ],
)
def test_minecraft_java_tag(version, tag):
    assert minecraft_java_tag(version) == tag


def test_user_input_stays_one_argument():
    template = Template.model_validate(
        {
            "id": "t",
            "name": "T",
            "fields": [{"id": "map", "label": "Map", "type": "string"}],
            "runtime": {"image": "alpine", "command": ["run", "+map", "{{ map }}"]},
        }
    )
    server = Server(id="s1", name="x", template_id="t", values={"map": "de_dust2; rm -rf /"}, ports={})
    spec = build_spec(template, server, TEMPLATES_DIR)
    assert spec.runtime.command == ["run", "+map", "de_dust2; rm -rf /"]


def test_template_cannot_escape_sandbox():
    template = Template.model_validate(
        {"id": "t", "name": "T", "runtime": {"image": "alpine", "env": {"X": "{{ ''.__class__.__mro__ }}"}}}
    )
    server = Server(id="s1", name="x", template_id="t", values={}, ports={})
    with pytest.raises(Exception, match="unsafe|not safely"):
        build_spec(template, server, TEMPLATES_DIR)


def test_install_script_must_stay_in_templates_dir():
    template = Template.model_validate(
        {
            "id": "t",
            "name": "T",
            "runtime": {"image": "alpine"},
            "install": {"image": "alpine", "script": "../../etc/passwd"},
        }
    )
    server = Server(id="s1", name="x", template_id="t", values={}, ports={})
    with pytest.raises(ValueError, match="install script not found"):
        build_spec(template, server, TEMPLATES_DIR)


def test_cs16_install_uses_bundled_script():
    import yaml

    template = Template.model_validate(yaml.safe_load((TEMPLATES_DIR / "cs16.yaml").read_text()))
    server = Server(
        id="s1",
        name="x",
        template_id="cs16",
        values={"map": "de_nuke", "max_players": 10, "rcon_password": "pw"},
        ports={"game": 27020},
    )
    spec = build_spec(template, server, TEMPLATES_DIR)
    assert spec.install.script == TEMPLATES_DIR / "install" / "cs16.sh"
    assert spec.install.entrypoint == ["sh", "/dgs/install.sh"]
    assert spec.runtime.ports[0].host == 27020 and spec.runtime.ports[0].protocol == "udp"


def test_allocate_ports_skips_taken_and_busy():
    ports = [Port(name="game", container=27015, protocol="udp", default_host=27015)]
    busy = {27016}
    result = allocate_ports(ports, taken={(27015, "udp")}, is_free=lambda p, proto: p not in busy)
    assert result == {"game": 27017}


def test_same_number_different_protocol_is_fine():
    ports = [Port(name="game", container=25565, protocol="tcp", default_host=25565)]
    assert allocate_ports(ports, taken={(25565, "udp")}, is_free=lambda p, proto: True) == {"game": 25565}


@pytest.mark.parametrize(
    ("text", "health"),
    [
        ("Up 5 minutes (healthy)", "healthy"),
        ("Up 3 seconds (health: starting)", "starting"),
        ("Up 2 hours", None),
        ("Exited (0) 1 minute ago", None),
    ],
)
def test_health_from_status(text, health):
    assert _health_from_status(text) == health
