from datetime import datetime
from typing import Any

from aiodocker.exceptions import DockerError
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import Manager, Providers, Session, Templates, require_template
from app.games.validation import ValuesError, public_values, validate_values
from app.models import Server
from app.runtime.docker import RuntimeUnavailable
from app.runtime.manager import ServerBusy, ServerStatus
from app.runtime.ports import allocate_ports

router = APIRouter(prefix="/servers", tags=["servers"])


class ServerCreate(BaseModel):
    template_id: str
    name: str = Field(min_length=1, max_length=64)
    values: dict[str, Any] = {}


class ServerRead(BaseModel):
    id: str
    name: str
    template_id: str
    values: dict[str, Any]
    ports: dict[str, int]
    status: ServerStatus
    status_message: str | None
    created_at: datetime


class CommandBody(BaseModel):
    command: str = Field(min_length=1, max_length=1000)


class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    # Only fields the template marks `editable`; the rest keep their values.
    values: dict[str, Any] = {}


class ServerUpdateResult(BaseModel):
    server: ServerRead
    # The server is running with the old settings; they apply on the next (re)start.
    restart_required: bool
    # A changed field needs the game files rebuilt, so the install step was started.
    reinstalling: bool


def to_read(server: Server, templates: Templates, server_status: ServerStatus) -> ServerRead:
    return ServerRead(
        id=server.id,
        name=server.name,
        template_id=server.template_id,
        values=public_values(templates.get(server.template_id), server.values),
        ports=server.ports,
        status=server_status,
        status_message=server.state_message,
        created_at=server.created_at,
    )


async def get_server_or_404(session: Session, server_id: str) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(404, "server not found")
    return server


async def read_one(server: Server, templates: Templates, manager: Manager) -> ServerRead:
    return to_read(server, templates, await manager.status_of(server))


@router.get("")
async def list_servers(session: Session, templates: Templates, manager: Manager) -> list[ServerRead]:
    servers = list(await session.scalars(select(Server).order_by(Server.created_at)))
    statuses = await manager.statuses(servers)
    return [to_read(s, templates, statuses[s.id]) for s in servers]


@router.post("", status_code=status.HTTP_201_CREATED)
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
    try:
        ports = allocate_ports(template.ports, taken)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc

    server = Server(name=body.name.strip(), template_id=template.id, values=values, ports=ports)
    session.add(server)
    await session.commit()

    await manager.install(server)
    return await read_one(server, templates, manager)


@router.get("/{server_id}")
async def get_server(server_id: str, session: Session, templates: Templates, manager: Manager) -> ServerRead:
    return await read_one(await get_server_or_404(session, server_id), templates, manager)


@router.patch("/{server_id}")
async def update_server(
    server_id: str,
    body: ServerUpdate,
    session: Session,
    templates: Templates,
    providers: Providers,
    manager: Manager,
) -> ServerUpdateResult:
    server = await get_server_or_404(session, server_id)
    template = require_template(templates, server.template_id)

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

    if body.name is not None:
        server.name = body.name.strip()
    server.values = values
    await session.commit()

    reinstalling = "reinstall" in effects
    if reinstalling:
        await manager.install(server)
    return ServerUpdateResult(
        server=await read_one(server, templates, manager),
        restart_required=running and "restart" in effects,
        reinstalling=reinstalling,
    )


@router.get("/{server_id}/install-log")
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


@router.post("/{server_id}/start")
async def start_server(
    server_id: str, session: Session, templates: Templates, manager: Manager
) -> ServerRead:
    server = await get_server_or_404(session, server_id)
    await _run(manager.start, server)
    return await read_one(server, templates, manager)


@router.post("/{server_id}/stop")
async def stop_server(server_id: str, session: Session, templates: Templates, manager: Manager) -> ServerRead:
    server = await get_server_or_404(session, server_id)
    await _run(manager.stop, server)
    return await read_one(server, templates, manager)


@router.post("/{server_id}/restart")
async def restart_server(
    server_id: str, session: Session, templates: Templates, manager: Manager
) -> ServerRead:
    server = await get_server_or_404(session, server_id)
    await _run(manager.restart, server)
    return await read_one(server, templates, manager)


@router.post("/{server_id}/reinstall")
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


@router.post("/{server_id}/command", status_code=status.HTTP_204_NO_CONTENT)
async def send_command(server_id: str, body: CommandBody, session: Session, manager: Manager) -> None:
    server = await get_server_or_404(session, server_id)
    if await manager.status_of(server) not in (ServerStatus.RUNNING, ServerStatus.STARTING):
        raise HTTPException(409, "the server is not running")
    try:
        await manager.send_command(server, body.command)
    except (RuntimeUnavailable, DockerError) as exc:
        raise HTTPException(503, f"docker: {exc}") from exc


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(server_id: str, session: Session, manager: Manager) -> None:
    server = await get_server_or_404(session, server_id)
    try:
        # Containers and the data volume go with it: the UI asks for confirmation.
        await manager.delete(server)
    except (RuntimeUnavailable, DockerError) as exc:
        raise HTTPException(503, f"docker: {exc}") from exc
    await session.delete(server)
    await session.commit()
