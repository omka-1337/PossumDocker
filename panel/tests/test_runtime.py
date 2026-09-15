import pytest

from app.core.config import REPO_ROOT
from app.games.schema import Port, Template
from app.models import Server
from app.runtime.docker import ContainerState, _exit_code_from_status, _health_from_status, host_limits
from app.runtime.manager import crash_message
from app.runtime.ports import allocate_ports
from app.runtime.spec import ContainerSpec, build_spec, default_limits, effective_limits, minecraft_java_tag

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
    assert spec.install.mounts == []  # the script is copied in, not bind-mounted from the host
    assert spec.install.entrypoint == ["sh", "/possum/install.sh"]
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


def load_template(name: str) -> Template:
    import yaml

    return Template.model_validate(yaml.safe_load((TEMPLATES_DIR / f"{name}.yaml").read_text()))


@pytest.mark.parametrize(("vac", "insecure"), [(True, False), (False, True)])
def test_cs16_vac_switch_adds_insecure_only_when_off(vac, insecure):
    server = Server(
        id="s1",
        name="x",
        template_id="cs16",
        values={"map": "de_dust2", "max_players": 10, "vac": vac, "rcon_password": "pw"},
        ports={"game": 27015},
    )
    command = build_spec(load_template("cs16"), server, TEMPLATES_DIR).runtime.command
    assert ("-insecure" in command) is insecure
    assert "" not in command  # the optional flag leaves no empty argument behind


def colour_of(template: Template, lines: list[str]) -> list[str | None]:
    """The same logic the browser console runs, to check the template's patterns on real lines."""
    import re

    colours, previous = [], None
    for line in lines:
        if template.console.continuation and re.search(template.console.continuation, line):
            colour = previous
        else:
            colour = next((r.color for r in template.console.highlight if re.search(r.pattern, line)), None)
        colours.append(colour)
        previous = colour
    return colours


def test_minecraft_console_colours():
    lines = [
        '[23:09:55 INFO]: Done (34.170s)! For help, type "help"',
        "[23:10:01 WARN]: Can't keep up! Is the server overloaded?",
        "[23:02:28] [Server thread/WARN]: Ambiguity between arguments",
        "[23:10:02 ERROR]: Could not load 'plugins/Broken.jar'",
        "java.lang.IllegalStateException: boom",
        "\tat org.bukkit.plugin.SimplePluginManager.loadPlugin(SimplePluginManager.java:123)",
        "\t... 5 more",
        "[23:02:30] [Server thread/ERROR]: Encountered an unexpected exception",
        "Starting org.bukkit.craftbukkit.Main",
        "[init] Setting initial memory to 1024M",
        "WARN StatusConsoleListener Advanced terminal features are not available",
    ]
    assert colour_of(load_template("minecraft-java"), lines) == [
        None, "yellow", "yellow", "red", "red", "red", "red", "red", None, "gray", "yellow",
    ]  # fmt: skip


def test_invalid_highlight_pattern_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="invalid pattern"):
        Template.model_validate(
            {"id": "t", "name": "T", "runtime": {"image": "a"},
             "console": {"highlight": [{"pattern": "([", "color": "red"}]}}
        )  # fmt: skip


def test_port_groups_move_together():
    ports = [
        Port(name="game", protocol="udp", default_host=2456),
        Port(name="query", protocol="udp", default_host=2457, follows="game"),
    ]
    assert allocate_ports(ports, taken=set(), is_free=lambda p, proto: True) == {"game": 2456, "query": 2457}
    # 2457 is busy: the pair moves, it doesn't split into 2456 + 2458.
    busy = {2457}
    assert allocate_ports(ports, taken=set(), is_free=lambda p, proto: p not in busy) == {
        "game": 2458,
        "query": 2459,
    }
    assert allocate_ports(ports, taken={(2456, "udp")}, is_free=lambda p, proto: True) == {
        "game": 2457,
        "query": 2458,
    }


def test_follows_must_point_back():
    from pydantic import ValidationError

    with pytest.raises(ValidationError, match="not declared before it"):
        Template.model_validate(
            {"id": "t", "name": "T", "runtime": {"image": "a"},
             "ports": [{"name": "a", "protocol": "udp", "default_host": 1, "follows": "b"},
                       {"name": "b", "protocol": "udp", "default_host": 2}]}
        )  # fmt: skip


def test_valheim_spec():
    server = Server(
        id="s1",
        name="Viking Hall",
        template_id="valheim",
        values={"world_name": "Midgard", "password": "secret123", "public": False, "crossplay": True},
        ports={"game": 2466, "query": 2467},
    )
    spec = build_spec(load_template("valheim"), server, TEMPLATES_DIR).runtime
    # No container port in the template: inside == outside, so the server list shows the right one.
    assert [(p.host, p.container) for p in spec.ports] == [(2466, 2466), (2467, 2467)]
    assert spec.env["SERVER_PORT"] == "2466" and spec.env["SERVER_NAME"] == "Viking Hall"
    assert spec.env["SERVER_PUBLIC"] == "false" and spec.env["CROSSPLAY"] == "true"
    # Literal "" switches the image's cron jobs off instead of being dropped.
    assert spec.env["UPDATE_CRON"] == "" and spec.env["RESTART_CRON"] == ""
    assert [(m.subpath, m.path) for m in spec.mounts] == [("config", "/config"), ("server", "/opt/valheim")]


def test_factorio_spec():
    server = Server(
        id="s1",
        name="x",
        template_id="factorio",
        values={"version": "stable", "space_age": False},
        ports={"game": 34197},
    )
    spec = build_spec(load_template("factorio"), server, TEMPLATES_DIR).runtime
    assert spec.image == "factoriotools/factorio:stable"
    assert spec.env["DLC_SPACE_AGE"] == "false" and spec.env["PORT"] == "34197"


def test_install_script_goes_in_as_an_archive():
    import io
    import tarfile

    from app.runtime.docker import _tar_with

    with tarfile.open(fileobj=io.BytesIO(_tar_with("possum/install.sh", b"echo hi"))) as tar:
        member = tar.getmember("possum/install.sh")
        assert tar.extractfile(member).read() == b"echo hi" and member.mode == 0o755
        assert tar.getmember("possum").isdir()


def test_minecraft_memory_limit_follows_the_heap():
    template = load_template("minecraft-java")
    server = Server(id="abc", name="mc", template_id="minecraft-java", ports={"game": 25565})
    server.values = {"version": "1.21.1", "loader": "paper", "memory_mb": 2048, "eula": True}
    assert default_limits(template, server.values) == (2048 + 768, None)
    server.values["memory_mb"] = 8192
    assert build_spec(template, server, TEMPLATES_DIR).runtime.memory_mb == 8192 + 2048


def test_administrator_limits_override_the_template():
    template = load_template("cs16")
    server = Server(id="abc", name="cs", template_id="cs16", ports={"game": 27015})
    server.values = {"map": "de_dust2", "max_players": 16, "vac": True, "rcon_password": "x" * 24}
    assert effective_limits(template, server) == (512, None)
    server.memory_limit_mb, server.cpu_limit = 0, 1.5  # 0: no limit, even though the template has one
    assert effective_limits(template, server) == (None, 1.5)
    server.memory_limit_mb = 2048
    spec = build_spec(template, server, TEMPLATES_DIR).runtime
    assert host_limits(spec) == {
        "RestartPolicy": {"Name": "on-failure", "MaximumRetryCount": 3},
        "PidsLimit": 4096,
        "Memory": 2048 * 1024 * 1024,
        "MemorySwap": 2048 * 1024 * 1024,
        "NanoCpus": 1_500_000_000,
    }


def test_no_limits_by_default():
    assert host_limits(ContainerSpec(image="x")) == {
        "RestartPolicy": {"Name": "on-failure", "MaximumRetryCount": 3},
        "PidsLimit": 4096,
    }


@pytest.mark.parametrize(
    ("text", "code"),
    [("Exited (137) 2 minutes ago", 137), ("Exited (0) 5 seconds ago", 0), ("Up 3 minutes", None)],
)
def test_exit_code_from_status(text, code):
    assert _exit_code_from_status(text) == code


def test_crash_message():
    assert crash_message(ContainerState("exited", exit_code=1)) == "exited with code 1"
    assert crash_message(ContainerState("exited", exit_code=137)) == (
        "exited with code 137 (SIGKILL, possibly out of memory)"
    )
    assert crash_message(ContainerState("exited", exit_code=137, oom_killed=True)) == (
        "ran out of memory (exit code 137)"
    )
