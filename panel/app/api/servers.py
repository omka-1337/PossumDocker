from datetime import datetime
from typing import Any

from aiodocker.exceptions import DockerError
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import Manager, Providers, Session, Templates, require_template
from app.core.auth import CurrentUser, allow, require_admin, visible_server_ids
from app.core.permissions import Permission
from app.games.schema import Template
from app.games.validation import ValuesError, public_values, validate_values
from app.models import Server
from app.runtime.docker import RuntimeUnavailable
from app.runtime.manager import ServerBusy, ServerStatus, StatusInfo
from app.runtime.ports import allocate_ports
from app.runtime.spec import MIN_MEMORY_MB, default_limits

router = APIRouter(prefix="/servers", tags=["servers"])
AdminOnly = Depends(require_admin)


class ServerCreate(BaseModel):
    template_id: str
    name: str = Field(min_length=1, max_length=64)
    values: dict[str, Any] = {}


class Limits(BaseModel):
    # None: no limit.
    memory_mb: int | None
    cpus: float | None


class ServerLimits(BaseModel):
    # Set by an administrator for this server: None follows the template, 0 is "no limit".
    memory_mb: int | None
    cpus: float | None
    # What the template would give.
    default: Limits


class ServerRead(BaseModel):
    id: str
    name: str
    template_id: str
    values: dict[str, Any]
    ports: dict[str, int]
    status: ServerStatus
    status_message: str | None
    limits: ServerLimits
    created_at: datetime


class CommandBody(BaseModel):
    command: str = Field(min_length=1, max_length=1000)


class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    # Only fields the template marks `editable`; the rest keep their values.
    values: dict[str, Any] = {}
    # Administrators only. null: back to the template's default; 0: no limit.
    memory_limit_mb: int | None = Field(default=None, ge=0, le=1024 * 1024)
    cpu_limit: float | None = Field(default=None, ge=0, le=1024)


class ServerUpdateResult(BaseModel):
    server: ServerRead
    # The server is running with the old settings; they apply on the next (re)start.
    restart_required: bool
    # A changed field needs the game files rebuilt, so the install step was started.
    reinstalling: bool


def _default_limits(template: Template | None, values: dict[str, Any]) -> Limits:
    try:
        memory, cpus = default_limits(template, values) if template else (None, None)
    except Exception:  # a broken template expression shouldn't hide the server
        memory, cpus = None, None
    return Limits(memory_mb=memory, cpus=cpus)


def to_read(server: Server, templates: Templates, info: StatusInfo) -> ServerRead:
    template = templates.get(server.template_id)
    return ServerRead(
        id=server.id,
        name=server.name,
        template_id=server.template_id,
        values=public_values(template, server.values),
        ports=server.ports,
        status=info.status,
        status_message=info.message,
        limits=ServerLimits(
            memory_mb=server.memory_limit_mb,
            cpus=server.cpu_limit,
            default=_default_limits(template, server.values),
        ),
        created_at=server.created_at,
    )


async def get_server_or_404(session: Session, server_id: str) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(404, "server not found")
    return server


async def read_one(server: Server, templates: Templates, manager: Manager) -> ServerRead:
    return to_read(server, templates, await manager.status_info_of(server))


@router.get("")
async def list_servers(
    session: Session, templates: Templates, manager: Manager, user: CurrentUser
) -> list[ServerRead]:
    query = select(Server).order_by(Server.created_at)
    if (visible := await visible_server_ids(session, user)) is not None:
        query = query.where(Server.id.in_(visible))  # only servers they were given access to
    servers = list(await session.scalars(query))
    statuses = await manager.status_infos(servers)
    return [to_read(s, templates, statuses[s.id]) for s in servers]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[AdminOnly])
async def create_server(
    body: ServerCreate, session: Session, templates: Templates, providers: Providers, manager: Manager
) -> ServerRead:
    template = require_template(templates, body.template_id)
    try:
        values = await validate_values(template, body.values, providers)
    except ValuesError as exc:
        # Keyed by field id so the form can show each message under its input.
        raise HTTPException(422, {"errors": exc.errors}) from exc

    # Ports of other servers count as taken even while they are stopped and not bound.
    rows = (await session.execute(select(Server.ports, Server.template_id))).all()
    taken = {
        (host_port, port.protocol)
        for ports, template_id in rows
        if template_id in templates
        for port in templates[template_id].ports
        if (host_port := ports.get(port.name)) is not None
    }
    published = getattr(manager.runtime, "published_ports", None)
    if published:
        try:
            taken |= await published()  # e.g. another app's container already on 25565
        except (RuntimeUnavailable, DockerError):
            pass
    try:
        ports = allocate_ports(template.ports, taken)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

    server = Server(name=body.name.strip(), template_id=template.id, values=values, ports=ports)
    session.add(server)
    await session.commit()

    await manager.install(server)
    return await read_one(server, templates, manager)


@router.get("/{server_id}", dependencies=[allow(Permission.VIEW)])
async def get_server(server_id: str, session: Session, templates: Templates, manager: Manager) -> ServerRead:
    return await read_one(await get_server_or_404(session, server_id), templates, manager)


@router.patch("/{server_id}", dependencies=[allow(Permission.SETTINGS)])
async def update_server(
    server_id: str,
    body: ServerUpdate,
    session: Session,
    templates: Templates,
    providers: Providers,
    manager: Manager,
    user: CurrentUser,
) -> ServerUpdateResult:
    server = await get_server_or_404(session, server_id)
    template = require_template(templates, server.template_id)

    limit_keys = body.model_fields_set & {"memory_limit_mb", "cpu_limit"}
    if limit_keys and not user.is_admin:
        raise HTTPException(403, "only administrators can change resource limits")
    if body.memory_limit_mb and body.memory_limit_mb < MIN_MEMORY_MB:
        raise HTTPException(
            422, {"errors": {"memory_limit_mb": f"at least {MIN_MEMORY_MB} MB, or 0 for no limit"}}
        )

    errors = {}
    for key in body.values:
        field = template.field(key)
        if field is None:
            errors[key] = "unknown field"
        elif not field.editable:
            errors[key] = "can't be changed after the server is created"
    try:
        if errors:
            raise ValuesError(errors)
        values = await validate_values(template, {**server.values, **body.values}, providers, server.values)
    except ValuesError as exc:
        raise HTTPException(422, {"errors": exc.errors}) from exc

    changed = [f for f in template.fields if values.get(f.id) != server.values.get(f.id)]
    # Editing one field can hide or reveal another; a non-editable one must not change that way.
    if locked := [f.id for f in changed if not f.editable]:
        raise HTTPException(
            422, {"errors": {key: "can't be changed after the server is created" for key in locked}}
        )

    effects = {f.on_change for f in changed}
    status_now = await manager.status_of(server)
    running = status_now in (ServerStatus.RUNNING, ServerStatus.STARTING, ServerStatus.STOPPING)
    if "reinstall" in effects and (running or manager.is_busy(server.id)):
        raise HTTPException(409, "stop the server first: this change reinstalls the game")

    limits_before = (server.memory_limit_mb, server.cpu_limit)
    if body.name is not None:
        server.name = body.name.strip()
    server.values = values
    if "memory_limit_mb" in limit_keys:
        server.memory_limit_mb = body.memory_limit_mb
    if "cpu_limit" in limit_keys:
        server.cpu_limit = body.cpu_limit
    limits_changed = (server.memory_limit_mb, server.cpu_limit) != limits_before
    await session.commit()

    reinstalling = "reinstall" in effects
    if reinstalling:
        await manager.install(server)
    return ServerUpdateResult(
        server=await read_one(server, templates, manager),
        restart_required=running and ("restart" in effects or limits_changed),
        reinstalling=reinstalling,
    )


@router.get("/{server_id}/install-log", dependencies=[allow(Permission.VIEW)])
async def get_install_log(server_id: str, session: Session, manager: Manager) -> list[str]:
    await get_server_or_404(session, server_id)
    return manager.install_log(server_id)


async def _run(action, server: Server) -> None:
    try:
        await action(server)
    except ServerBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except (RuntimeUnavailable, DockerError) as exc:
        raise HTTPException(503, f"docker: {exc}") from exc


@router.post("/{server_id}/start", dependencies=[allow(Permission.CONTROL)])
async def start_server(
    server_id: str, session: Session, templates: Templates, manager: Manager
) -> ServerRead:
    server = await get_server_or_404(session, server_id)
    await _run(manager.start, server)
    return await read_one(server, templates, manager)


@router.post("/{server_id}/stop", dependencies=[allow(Permission.CONTROL)])
async def stop_server(server_id: str, session: Session, templates: Templates, manager: Manager) -> ServerRead:
    server = await get_server_or_404(session, server_id)
    await _run(manager.stop, server)
    return await read_one(server, templates, manager)


@router.post("/{server_id}/restart", dependencies=[allow(Permission.CONTROL)])
async def restart_server(
    server_id: str, session: Session, templates: Templates, manager: Manager
) -> ServerRead:
    server = await get_server_or_404(session, server_id)
    await _run(manager.restart, server)
    return await read_one(server, templates, manager)


@router.post("/{server_id}/reinstall", dependencies=[AdminOnly])
async def reinstall_server(
    server_id: str, session: Session, templates: Templates, manager: Manager
) -> ServerRead:
    server = await get_server_or_404(session, server_id)
    if manager.is_busy(server.id):
        raise HTTPException(409, "another operation is still running for this server")
    if await manager.status_of(server) in (ServerStatus.RUNNING, ServerStatus.STARTING):
        raise HTTPException(409, "stop the server before reinstalling")
    await manager.install(server)
    return await read_one(server, templates, manager)


@router.post(
    "/{server_id}/command", status_code=status.HTTP_204_NO_CONTENT, dependencies=[allow(Permission.CONSOLE)]
)
async def send_command(server_id: str, body: CommandBody, session: Session, manager: Manager) -> None:
    server = await get_server_or_404(session, server_id)
    if await manager.status_of(server) not in (ServerStatus.RUNNING, ServerStatus.STARTING):
        raise HTTPException(409, "the server is not running")
    try:
        await manager.send_command(server, body.command)
    except ServerBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except (RuntimeUnavailable, DockerError) as exc:
        raise HTTPException(503, f"docker: {exc}") from exc


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[AdminOnly])
async def delete_server(server_id: str, session: Session, manager: Manager) -> None:
    server = await get_server_or_404(session, server_id)
    try:
        # Containers and the data volume go with it: the UI asks for confirmation.
        await manager.delete(server)
    except (RuntimeUnavailable, DockerError) as exc:
        raise HTTPException(503, f"docker: {exc}") from exc
    await session.delete(server)
    await session.commit()
