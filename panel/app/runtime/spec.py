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
class VolumeMount:
    subpath: str
    path: str


@dataclass(frozen=True)
class ContainerSpec:
    image: str
    entrypoint: list[str] | None = None
    command: list[str] | None = None
    env: dict[str, str] = field(default_factory=dict)
    ports: list[PortBinding] = field(default_factory=list)
    data_path: str = "/data"
    # Parts of the volume at several paths; empty: the whole volume at data_path.
    mounts: list[VolumeMount] = field(default_factory=list)
    # Limits; None: no limit.
    memory_mb: int | None = None
    cpus: float | None = None
    # Host file mounted read-only at /possum/install.sh
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
    result = {}
    for key, value in env.items():
        rendered = _render(value, context)
        # A hidden field (loader_version for Paper) renders empty: leave it unset. A literal ""
        # in the template is meant as empty, e.g. to switch off an image's own cron job.
        if rendered != "" or "{" not in value:
            result[key] = rendered
    return result


def _render_list(items: list[str] | None, context: dict) -> list[str] | None:
    # Each item stays one argument: user input is never split or passed through a shell.
    # An item that renders empty is dropped, so optional flags work: "{{ '' if vac else '-insecure' }}"
    if items is None:
        return None
    rendered = (_render(item, context) for item in items)
    return [arg for arg in rendered if arg != ""]


MIN_MEMORY_MB = 64


def default_limits(template: Template, values: dict) -> tuple[int | None, float | None]:
    """The template's memory (MB) and CPU limits for these field values."""
    resources = template.runtime.resources
    memory = cpus = None
    if resources.memory_mb:
        memory = max(MIN_MEMORY_MB, int(float(_render(resources.memory_mb, values, strict=True))))
    if resources.cpus:
        cpus = float(_render(resources.cpus, values, strict=True))
    return memory, cpus


def effective_limits(template: Template, server: Server) -> tuple[int | None, float | None]:
    """What the container gets: the administrator's override for this server, else the template default.

    A limit of 0 set by the administrator means "no limit", even if the template has a default.
    """
    memory, cpus = default_limits(template, server.values)
    if server.memory_limit_mb is not None:
        memory = server.memory_limit_mb or None
    if server.cpu_limit is not None:
        cpus = server.cpu_limit or None
    return memory, cpus


def default_disk_mb(template: Template, values: dict) -> int | None:
    if not template.runtime.resources.disk_mb:
        return None
    return int(float(_render(template.runtime.resources.disk_mb, values, strict=True)))


def storage_limits(template: Template, server: Server) -> tuple[int | None, int | None]:
    """Disk and backup space in MB for this server; None: no limit.

    Backups default to twice the disk limit: a few full copies of the server.
    """
    disk = default_disk_mb(template, server.values)
    if server.disk_limit_mb is not None:
        disk = server.disk_limit_mb or None
    backups = disk * 2 if disk else None
    if server.backup_limit_mb is not None:
        backups = server.backup_limit_mb or None
    return disk, backups


def build_spec(template: Template, server: Server, templates_dir: Path) -> ServerSpec:
    context = {
        **server.values,
        "server": {"id": server.id, "name": server.name},
        "ports": server.ports,
    }
    runtime = template.runtime
    ports = [
        PortBinding(host=host, container=p.container or host, protocol=p.protocol)
        for p in template.ports
        if (host := server.ports[p.name])
    ]
    runtime_env = _render_env(runtime.env, context)

    runtime_spec = ContainerSpec(
        image=_render(runtime.image, context, strict=True),
        entrypoint=_render_list(runtime.entrypoint, context),
        command=_render_list(runtime.command, context),
        env=runtime_env,
        ports=ports,
        data_path=runtime.data_path,
        mounts=[VolumeMount(subpath=m.subpath, path=m.path) for m in runtime.mounts],
        memory_mb=(limits := effective_limits(template, server))[0],
        cpus=limits[1],
    )

    install_spec = None
    if install := template.install:
        script = None
        if install.script:
            base = template.directory or templates_dir
            script = (base / install.script).resolve()
            if not script.is_relative_to(base.resolve()) or not script.is_file():
                raise ValueError(f"install script not found: {install.script}")
        install_spec = ContainerSpec(
            image=_render(install.image, context, strict=True),
            entrypoint=["sh", "/possum/install.sh"] if script else _render_list(install.entrypoint, context),
            command=None if script else _render_list(install.command, context),
            env={**runtime_env, **_render_env(install.env, context)},
            data_path=runtime.data_path,
            script=script,
        )

    return ServerSpec(runtime=runtime_spec, install=install_spec, stop=runtime.stop)
