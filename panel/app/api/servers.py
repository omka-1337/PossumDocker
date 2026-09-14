from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import Providers, Session, Templates, require_template
from app.games.validation import ValuesError, public_values, validate_values
from app.models import Server, ServerStatus

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
    status: ServerStatus
    created_at: datetime


def to_read(server: Server, templates: Templates) -> ServerRead:
    return ServerRead(
        id=server.id,
        name=server.name,
        template_id=server.template_id,
        values=public_values(templates.get(server.template_id), server.values),
        status=server.status,
        created_at=server.created_at,
    )


async def get_server_or_404(session: Session, server_id: str) -> Server:
    server = await session.get(Server, server_id)
    if server is None:
        raise HTTPException(404, "server not found")
    return server


@router.get("")
async def list_servers(session: Session, templates: Templates) -> list[ServerRead]:
    result = await session.scalars(select(Server).order_by(Server.created_at))
    return [to_read(s, templates) for s in result]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_server(
    body: ServerCreate, session: Session, templates: Templates, providers: Providers
) -> ServerRead:
    template = require_template(templates, body.template_id)
    try:
        values = await validate_values(template, body.values, providers)
    except ValuesError as exc:
        # Keyed by field id so the form can show each message under its input.
        raise HTTPException(422, {"errors": exc.errors}) from exc

    server = Server(name=body.name.strip(), template_id=template.id, values=values)
    session.add(server)
    await session.commit()
    # TODO: pull images, run the install step and create the container (status -> installing).
    return to_read(server, templates)


@router.get("/{server_id}")
async def get_server(server_id: str, session: Session, templates: Templates) -> ServerRead:
    return to_read(await get_server_or_404(session, server_id), templates)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(server_id: str, session: Session) -> None:
    server = await get_server_or_404(session, server_id)
    # TODO: remove the container; the data volume is kept until the user confirms.
    await session.delete(server)
    await session.commit()
