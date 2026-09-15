from aiodocker.exceptions import DockerError
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import Manager, Session, Templates
from app.api.servers import get_server_or_404
from app.core.auth import allow
from app.core.permissions import Permission
from app.games.configs import ConfigDocument, ConfigValuesError, validate_config_values
from app.games.schema import ConfigFile, ConfigHint
from app.runtime.docker import RuntimeUnavailable
from app.runtime.manager import ServerBusy

router = APIRouter(
    prefix="/servers/{server_id}/configs", tags=["configs"], dependencies=[allow(Permission.SETTINGS)]
)


class ConfigSummary(BaseModel):
    id: str
    label: str
    path: str


class ConfigEntry(BaseModel):
    key: str
    value: str
    hint: ConfigHint | None
    managed: bool


class ConfigRead(ConfigSummary):
    # False until the game has written the file, usually on its first start.
    exists: bool
    entries: list[ConfigEntry]


class ConfigUpdate(BaseModel):
    values: dict[str, str]


def to_read(config: ConfigFile, document: ConfigDocument | None) -> ConfigRead:
    values = document.items() if document else {}
    # Only keys the file really has: this game version wrote exactly the settings it supports.
    # Hinted ones first, in the template's order, then the rest in file order.
    keys = [k for k in config.hints if k in values] + [k for k in values if k not in config.hints]
    return ConfigRead(
        id=config.id,
        label=config.label,
        path=config.path,
        exists=document is not None,
        entries=[
            ConfigEntry(key=k, value=values[k], hint=config.hints.get(k), managed=k in config.managed)
            for k in keys
        ],
    )


async def _load(server_id: str, config_id: str, session: Session, templates: Templates):
    server = await get_server_or_404(session, server_id)
    template = templates.get(server.template_id)
    config = template.config_file(config_id) if template else None
    if config is None:
        raise HTTPException(404, f"no config file '{config_id}'")
    return server, config


def _docker_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, ServerBusy):
        return HTTPException(409, str(exc))
    if isinstance(exc, ConfigValuesError):
        return HTTPException(422, {"errors": exc.errors})
    if isinstance(exc, ValueError):  # a broken file, e.g. invalid JSON
        return HTTPException(422, str(exc))
    return HTTPException(503, f"docker: {exc}")


@router.get("")
async def list_configs(server_id: str, session: Session, templates: Templates) -> list[ConfigSummary]:
    server = await get_server_or_404(session, server_id)
    template = templates.get(server.template_id)
    return [
        ConfigSummary(id=c.id, label=c.label, path=c.path)
        for c in (template.config_files if template else [])
    ]


@router.get("/{config_id}")
async def get_config(
    server_id: str, config_id: str, session: Session, templates: Templates, manager: Manager
) -> ConfigRead:
    server, config = await _load(server_id, config_id, session, templates)
    try:
        return to_read(config, await manager.read_config(server, config))
    except (ServerBusy, RuntimeUnavailable, DockerError, ValueError) as exc:
        raise _docker_errors(exc) from exc


@router.put("/{config_id}")
async def update_config(
    server_id: str,
    config_id: str,
    body: ConfigUpdate,
    session: Session,
    templates: Templates,
    manager: Manager,
) -> ConfigRead:
    """Changes only the given keys. The game picks them up on its next start."""
    server, config = await _load(server_id, config_id, session, templates)
    try:
        values = validate_config_values(config, body.values)
    except ConfigValuesError as exc:
        raise HTTPException(422, {"errors": exc.errors}) from exc
    try:
        return to_read(config, await manager.write_config(server, config, values))
    except (ServerBusy, RuntimeUnavailable, DockerError, ValueError) as exc:
        raise _docker_errors(exc) from exc
