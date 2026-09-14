"""Turn a template + a server's values into concrete container specs.

The runtime (Docker today, the Go agent later) only ever sees these specs;
it knows nothing about Minecraft, loaders or versions.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import StrictUndefined, Undefined
from jinja2.sandbox import SandboxedEnvironment

from app.games.schema import StopSpec, Template
from app.models import Server


@dataclass(frozen=True)
class PortBinding:
    host: int
    container: int
    protocol: str


@dataclass(frozen=True)
class ContainerSpec:
    image: str
    entrypoint: list[str] | None = None
    command: list[str] | None = None
    env: dict[str, str] = field(default_factory=dict)
    ports: list[PortBinding] = field(default_factory=list)
    data_path: str = "/data"
    # Host file mounted read-only at /dgs/install.sh
    script: Path | None = None


@dataclass(frozen=True)
class ServerSpec:
    runtime: ContainerSpec
    install: ContainerSpec | None
    stop: StopSpec


def minecraft_java_tag(version: str) -> str:
    """Tag of itzg/minecraft-server with a Java that this Minecraft version runs on."""
    parts = [int(p) for p in re.findall(r"\d+", version)[:3]]
    if not parts or parts[0] != 1:
        return "latest"  # year-based versions (26.1+) need the newest Java
    minor, patch = (parts + [0, 0])[1:3]
    if minor < 17:
        return "java8"
    if (minor, patch) < (20, 5):
        return "java17"
    return "java21"


# Sandboxed: templates are data, they must not be able to reach Python internals.
_jinja = SandboxedEnvironment(undefined=Undefined, autoescape=False, keep_trailing_newline=False)
_jinja.globals["minecraft_java_tag"] = minecraft_java_tag
# Image names must never silently render as "itzg/minecraft-server:".
_strict_jinja = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
_strict_jinja.globals.update(_jinja.globals)


def _render(text: str, context: dict, strict: bool = False) -> str:
    return (_strict_jinja if strict else _jinja).from_string(text).render(context)


def _render_env(env: dict[str, str], context: dict) -> dict[str, str]:
    rendered = {key: _render(value, context) for key, value in env.items()}
    # A value from a hidden field (loader_version for Paper) renders empty: leave it unset.
    return {key: value for key, value in rendered.items() if value != ""}


def _render_list(items: list[str] | None, context: dict) -> list[str] | None:
    # Each item stays one argument: user input is never split or passed through a shell.
    return None if items is None else [_render(item, context) for item in items]


def build_spec(template: Template, server: Server, templates_dir: Path) -> ServerSpec:
    context = {
        **server.values,
        "server": {"id": server.id, "name": server.name},
        "ports": server.ports,
    }
    runtime = template.runtime
    ports = [
        PortBinding(host=server.ports[p.name], container=p.container, protocol=p.protocol)
        for p in template.ports
    ]
    runtime_env = _render_env(runtime.env, context)

    runtime_spec = ContainerSpec(
        image=_render(runtime.image, context, strict=True),
        entrypoint=_render_list(runtime.entrypoint, context),
        command=_render_list(runtime.command, context),
        env=runtime_env,
        ports=ports,
        data_path=runtime.data_path,
    )

    install_spec = None
    if install := template.install:
        script = None
        if install.script:
            script = (templates_dir / install.script).resolve()
            if not script.is_relative_to(templates_dir.resolve()) or not script.is_file():
                raise ValueError(f"install script not found: {install.script}")
        install_spec = ContainerSpec(
            image=_render(install.image, context, strict=True),
            entrypoint=["sh", "/dgs/install.sh"] if script else _render_list(install.entrypoint, context),
            command=None if script else _render_list(install.command, context),
            env={**runtime_env, **_render_env(install.env, context)},
            data_path=runtime.data_path,
            script=script,
        )

    return ServerSpec(runtime=runtime_spec, install=install_spec, stop=runtime.stop)
