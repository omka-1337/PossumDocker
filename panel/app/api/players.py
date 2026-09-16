from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import Manager, Session
from app.api.servers import get_server_or_404
from app.core.auth import CurrentUser, allow
from app.core.permissions import Permission
from app.games.art import MEDIA_TYPES, ArtCache
from app.models import Player, ServerState
from app.runtime.manager import ServerBusy
from app.runtime.players import PlayerError
from app.runtime.state import RUNTIME_ERRORS

router = APIRouter(
    prefix="/servers/{server_id}/players", tags=["players"], dependencies=[allow(Permission.PLAYERS)]
)


# Like game art: a picture can't run scripts, whatever its source.
AVATAR_HEADERS = {
    "Cache-Control": "private, max-age=3600",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
    "X-Content-Type-Options": "nosniff",
}


class PlayerRead(BaseModel):
    key: str
    name: str | None
    # SteamID, Minecraft UUID, Xbox XUID
    game_id: str | None
    ip: str | None
    online: bool
    online_since: datetime | None
    first_seen: datetime
    last_seen: datetime


class BanRead(BaseModel):
    kind: Literal["player", "ip"]
    value: str
    name: str | None
    reason: str | None
    # From the panel's record of the ban, or the game's list when it keeps them (Minecraft).
    banned_at: datetime | None
    # None: permanent (or unknown for bans made outside the panel).
    expires_at: datetime | None
    banned_by: str | None


class Abilities(BaseModel):
    """What this game lets the panel do, so the UI shows only those buttons."""

    tracked: bool
    refresh: bool
    kick: bool
    # "name" | "id" | None: what a player ban uses
    ban_by: str | None
    ip_bans: bool
    # Bans written to a file the game reads only when it starts.
    bans_need_restart: bool
    # The game has no bans: the panel kicks banned players as they join.
    bans_by_panel: bool
    # "steam" | "minecraft": players with an id have a picture at /avatar?key=...
    avatars: str | None


class PlayersRead(BaseModel):
    players: list[PlayerRead]
    bans: list[BanRead]
    # Couldn't read the game's ban lists right now (e.g. Docker unreachable).
    bans_error: str | None
    abilities: Abilities


class KickBody(BaseModel):
    key: str = Field(max_length=200)
    reason: str | None = Field(default=None, max_length=200)


class BanBody(BaseModel):
    kind: Literal["player", "ip"]
    # A known player, or a value typed in: a name, an id or an address.
    key: str | None = Field(default=None, max_length=200)
    value: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, max_length=200)
    # A temporary ban: lifted after this many minutes (up to 10 years). None: permanent.
    minutes: int | None = Field(default=None, ge=1, le=60 * 24 * 3653)


class UnbanBody(BaseModel):
    kind: Literal["player", "ip"]
    value: str = Field(max_length=200)


class ActionResult(BaseModel):
    # "now", "after restart" or "on join"
    effective: str


def _errors(exc: Exception) -> HTTPException:
    if isinstance(exc, PlayerError):
        return HTTPException(exc.status, str(exc))
    if isinstance(exc, ServerBusy):
        return HTTPException(409, str(exc))
    return HTTPException(503, f"docker: {exc}")


async def _installed(session: Session, server_id: str):
    server = await get_server_or_404(session, server_id)
    if server.state != ServerState.INSTALLED:
        raise HTTPException(409, "the server is not installed")
    return server


@router.get("")
async def list_players(server_id: str, session: Session, manager: Manager) -> PlayersRead:
    server = await get_server_or_404(session, server_id)
    service = manager.players
    spec = service.spec(server)
    players = await session.scalars(
        select(Player)
        .where(Player.server_id == server_id)
        .order_by(Player.online.desc(), Player.last_seen.desc())
    )
    bans, bans_error = [], None
    if spec and server.state == ServerState.INSTALLED:
        try:
            bans = await service.list_bans(server)
        except (PlayerError, ServerBusy, ValueError, *RUNTIME_ERRORS) as exc:
            bans_error = str(exc)
    ban = spec.bans if spec else None
    return PlayersRead(
        players=[PlayerRead.model_validate(p, from_attributes=True) for p in players],
        bans=[
            BanRead(
                kind=b.kind,
                value=b.value,
                name=b.name,
                reason=b.reason,
                banned_at=b.banned_at,
                expires_at=b.expires_at,
                banned_by=b.banned_by,
            )
            for b in bans
        ],
        bans_error=bans_error,
        abilities=Abilities(
            tracked=bool(spec and spec.events),
            refresh=bool(spec and spec.status),
            kick=bool(spec and spec.kick),
            ban_by=ban.by if ban else None,
            ip_bans=bool(spec and spec.ip_bans),
            bans_need_restart=bool(ban and ban.file_needs_restart),
            bans_by_panel=bool(ban and ban.by_panel),
            avatars=spec.avatars if spec else None,
        ),
    )


@router.get("/avatar")
async def player_avatar(
    server_id: str, key: str, request: Request, session: Session, manager: Manager
) -> FileResponse:
    """The player's picture, fetched and cached by the panel: browsers never contact Steam or Mojang."""
    server = await get_server_or_404(session, server_id)
    spec = manager.players.spec(server)
    player = await session.scalar(select(Player).where(Player.server_id == server_id, Player.key == key))
    if not spec or not spec.avatars or not player or not player.game_id:
        raise HTTPException(404, "no picture for this player")
    art: ArtCache = request.app.state.art
    if spec.avatars == "steam":
        path = await art.steam_avatar(player.game_id)
    else:
        path = await art.minecraft_skin(player.game_id)
    if path is None:
        raise HTTPException(404, "no picture for this player")
    return FileResponse(path, media_type=MEDIA_TYPES[path.suffix[1:]], headers=AVATAR_HEADERS)


@router.post("/refresh", status_code=204)
async def refresh_players(server_id: str, session: Session, manager: Manager) -> None:
    server = await _installed(session, server_id)
    try:
        await manager.players.refresh(server)
    except (PlayerError, ServerBusy, *RUNTIME_ERRORS) as exc:
        raise _errors(exc) from exc


@router.post("/kick", status_code=204)
async def kick_player(server_id: str, body: KickBody, session: Session, manager: Manager) -> None:
    server = await _installed(session, server_id)
    try:
        await manager.players.kick(server, body.key, body.reason)
    except (PlayerError, ServerBusy, *RUNTIME_ERRORS) as exc:
        raise _errors(exc) from exc


@router.post("/ban")
async def ban_player(
    server_id: str, body: BanBody, session: Session, manager: Manager, user: CurrentUser
) -> ActionResult:
    server = await _installed(session, server_id)
    if not body.key and not body.value:
        raise HTTPException(422, "ban a known player (key) or give a value")
    try:
        effective = await manager.players.ban(
            server,
            body.kind,
            key=body.key,
            value=body.value,
            reason=body.reason,
            minutes=body.minutes,
            banned_by=user.username,
        )
    except (PlayerError, ServerBusy, ValueError, *RUNTIME_ERRORS) as exc:
        raise _errors(exc) from exc
    return ActionResult(effective=effective)


@router.post("/unban")
async def unban_player(server_id: str, body: UnbanBody, session: Session, manager: Manager) -> ActionResult:
    server = await _installed(session, server_id)
    try:
        effective = await manager.players.unban(server, body.kind, body.value)
    except (PlayerError, ServerBusy, ValueError, *RUNTIME_ERRORS) as exc:
        raise _errors(exc) from exc
    return ActionResult(effective=effective)
