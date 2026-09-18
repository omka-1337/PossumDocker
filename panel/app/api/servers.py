from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import Manager, Providers, Session, Templates, require_template
from app.core.auth import CurrentUser, allow, require_admin, visible_server_ids
from app.core.permissions import Permission
from app.games.schema import Template
from app.games.validation import ValuesError, public_values, validate_values
from app.models import Server, ServerState
from app.runtime.files import FileError
from app.runtime.manager import ServerBusy, ServerStatus, StatusInfo
from app.runtime.ports import PortError, allocate_ports, move_ports
from app.runtime.spec import MIN_MEMORY_MB, default_disk_mb, default_limits, storage_limits
from app.runtime.state import RUNTIME_ERRORS, explain_runtime_error

router = APIRouter(prefix="/servers", tags=["servers"])
AdminOnly = Depends(require_admin)

MIN_DISK_MB = 100
# null: the template's default (backups: twice the disk limit); 0: no limit.
DiskLimit = Field(default=None, ge=0, le=1024 * 1024 * 1024)


class ServerCreate(BaseModel):
    template_id: str
    name: str = Field(min_length=1, max_length=64)
    values: dict[str, Any] = {}
    disk_limit_mb: int | None = DiskLimit
    backup_limit_mb: int | None = DiskLimit


class Limits(BaseModel):
    # None: no limit.
    memory_mb: int | None
    cpus: float | None
    disk_mb: int | None
    backups_mb: int | None


class ServerLimits(BaseModel):
    # Set by an administrator for this server: None follows the default, 0 is "no limit".
    memory_mb: int | None
    cpus: float | None
    disk_mb: int | None
    backups_mb: int | None
    # What the server gets without them: the template's values, backups twice the disk limit.
    default: Limits


class StorageRead(BaseModel):
    # Bytes; limits None: no limit. disk_used None: the server isn't installed.
    disk_used: int | None
    disk_limit: int | None
    backups_used: int
    backups_limit: int | None


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
    disk_limit_mb: int | None = DiskLimit
    backup_limit_mb: int | None = DiskLimit
    # Administrators only: host ports by name. Ports that follow another move with it.
    ports: dict[str, int] | None = None


class ServerUpdateResult(BaseModel):
    server: ServerRead
    # The server is running with the old settings; they apply on the next (re)start.
    restart_required: bool
    # A changed field needs the game files rebuilt, so the install step was started.
    reinstalling: bool


def _default_limits(template: Template | None, server: Server) -> Limits:
    memory = cpus = disk = backups = None
    try:
        if template:
            memory, cpus = default_limits(template, server.values)
            disk = default_disk_mb(template, server.values)
            effective_disk, _ = storage_limits(template, server)
            backups = effective_disk * 2 if effective_disk else None
    except Exception:  # a broken template expression shouldn't hide the server
        pass
    return Limits(memory_mb=memory, cpus=cpus, disk_mb=disk, backups_mb=backups)


def _limit_errors(body: BaseModel) -> dict[str, str]:
    errors = {}
    if getattr(body, "memory_limit_mb", None) and body.memory_limit_mb < MIN_MEMORY_MB:
        errors["memory_limit_mb"] = f"at least {MIN_MEMORY_MB} MB, or 0 for no limit"
    for key in ("disk_limit_mb", "backup_limit_mb"):
        if getattr(body, key) and getattr(body, key) < MIN_DISK_MB:
            errors[key] = f"at least {MIN_DISK_MB} MB, or 0 for no limit"
    return errors


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
            disk_mb=server.disk_limit_mb,
            backups_mb=server.backup_limit_mb,
            default=_default_limits(template, server),
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


async def _taken_ports(
    session: Session, templates: Templates, manager: Manager, except_server: Server | None = None
) -> set[tuple[int, str]]:
    """Ports of other servers (even stopped ones, which don't bind them) and of other containers."""
    rows = (await session.execute(select(Server.id, Server.ports, Server.template_id))).all()
    taken = {
        (host_port, port.protocol)
        for server_id, ports, template_id in rows
        if template_id in templates and (except_server is None or server_id != except_server.id)
        for port in templates[template_id].ports
        if (host_port := ports.get(port.name)) is not None
    }
    published = getattr(manager.runtime, "published_ports", None)
    if published:
        try:
            own = set()
            if except_server:
                template = templates.get(except_server.template_id)
                own = {
                    (except_server.ports.get(p.name), p.protocol)
                    for p in (template.ports if template else [])
                }
            taken |= await published() - own  # e.g. another app's container already on 25565
        except RUNTIME_ERRORS:
            pass
    return taken


class ServerStats(BaseModel):
    id: str
    # Running servers only: share of one CPU core, memory in use and what it may use.
    cpus: float | None
    memory_bytes: int | None
    memory_limit: int | None
    # What the server's files take, measured every few minutes; None until first measured.
    disk_used: int | None


@router.get("/stats")
async def list_stats(
    session: Session, templates: Templates, manager: Manager, user: CurrentUser
) -> list[ServerStats]:
    """CPU, memory and size of the servers this user can see, for the list."""
    query = select(Server.id)
    if (visible := await visible_server_ids(session, user)) is not None:
        query = query.where(Server.id.in_(visible))
    ids = list(await session.scalars(query))
    try:
        stats = await manager.runtime.stats()
    except RUNTIME_ERRORS:
        stats = {}
    return [
        ServerStats(
            id=server_id,
            cpus=stats[server_id].cpus if server_id in stats else None,
            memory_bytes=stats[server_id].memory_bytes if server_id in stats else None,
            memory_limit=stats[server_id].memory_limit if server_id in stats else None,
            disk_used=manager.disk_usage.get(server_id),
        )
        for server_id in ids
    ]


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=[AdminOnly])
async def create_server(
    body: ServerCreate, session: Session, templates: Templates, providers: Providers, manager: Manager
) -> ServerRead:
    template = require_template(templates, body.template_id)
    try:
        values = await validate_values(template, body.values, providers)
        if limit_errors := _limit_errors(body):
            raise ValuesError(limit_errors)
    except ValuesError as exc:
        # Keyed by field id so the form can show each message under its input.
        raise HTTPException(422, {"errors": exc.errors}) from exc

    taken = await _taken_ports(session, templates, manager)
    try:
        ports = allocate_ports(template.ports, taken)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

    server = Server(
        name=body.name.strip(),
        template_id=template.id,
        values=values,
        ports=ports,
        disk_limit_mb=body.disk_limit_mb,
        backup_limit_mb=body.backup_limit_mb,
    )
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

    limit_keys = body.model_fields_set & {"memory_limit_mb", "cpu_limit", "disk_limit_mb", "backup_limit_mb"}
    if limit_keys and not user.is_admin:
        raise HTTPException(403, "only administrators can change resource limits")
    new_ports = None
    if body.ports is not None:
        if not user.is_admin:
            raise HTTPException(403, "only administrators can change ports")
        try:
            new_ports = move_ports(
                template.ports,
                server.ports,
                body.ports,
                await _taken_ports(session, templates, manager, except_server=server),
            )
        except PortError as exc:
            raise HTTPException(422, {"errors": {f"port.{k}": v for k, v in exc.errors.items()}}) from exc
    if limit_errors := _limit_errors(body):
        raise HTTPException(422, {"errors": limit_errors})

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
    # Disk limits are checked by the panel, not Docker: they apply at once, no restart.
    if "disk_limit_mb" in limit_keys:
        server.disk_limit_mb = body.disk_limit_mb
        manager.disk_errors.pop(server.id, None)
    if "backup_limit_mb" in limit_keys:
        server.backup_limit_mb = body.backup_limit_mb
    ports_changed = new_ports is not None and new_ports != server.ports
    if ports_changed:
        server.ports = new_ports
    await session.commit()

    reinstalling = "reinstall" in effects
    if reinstalling:
        await manager.install(server)
    return ServerUpdateResult(
        server=await read_one(server, templates, manager),
        restart_required=running and ("restart" in effects or limits_changed or ports_changed),
        reinstalling=reinstalling,
    )


@router.get(
    "/{server_id}/storage",
    dependencies=[allow(Permission.FILES, Permission.BACKUPS, Permission.SETTINGS)],
)
async def get_storage(server_id: str, session: Session, manager: Manager) -> StorageRead:
    """Space used against the limits. Measuring a big server takes a moment."""
    server = await get_server_or_404(session, server_id)
    disk_limit, backups_limit = manager.limits_of(server)
    disk_used = None
    if server.state == ServerState.INSTALLED:
        try:
            disk_used = await manager.disk_usage_of(server)
        except (FileError, *RUNTIME_ERRORS) as exc:
            raise HTTPException(503, f"could not measure the server: {exc}") from exc
    return StorageRead(
        disk_used=disk_used,
        disk_limit=disk_limit,
        backups_used=await manager.backup_usage(server.id),
        backups_limit=backups_limit,
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
    except RUNTIME_ERRORS as exc:
        if explanation := explain_runtime_error(exc):
            raise HTTPException(409, explanation) from exc
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
    except RUNTIME_ERRORS as exc:
        raise HTTPException(503, f"docker: {exc}") from exc


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[AdminOnly])
async def delete_server(server_id: str, session: Session, manager: Manager) -> None:
    server = await get_server_or_404(session, server_id)
    try:
        # Containers and the data volume go with it: the UI asks for confirmation.
        await manager.delete(server)
    except RUNTIME_ERRORS as exc:
        raise HTTPException(503, f"docker: {exc}") from exc
    await session.delete(server)
    await session.commit()
