import asyncio
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from app.core.auth import COOKIE, server_permissions, user_for_token
from app.core.permissions import Permission
from app.models import Server
from app.runtime.console import follow_console
from app.runtime.manager import ServerBusy, ServerManager, ServerStatus
from app.runtime.state import RUNTIME_ERRORS

log = logging.getLogger(__name__)

router = APIRouter(tags=["console"])


class ClientMessage(BaseModel):
    type: str
    data: str = Field(max_length=1000)


@router.websocket("/servers/{server_id}/console")
async def console(websocket: WebSocket, server_id: str) -> None:
    """Server → client: {"type": "log", "data": line} | {"type": "error", "data": message}
    Client → server: {"type": "command", "data": "say hi"}

    Needs the session cookie and the 'view' permission; commands also need 'console'.
    """
    if not _same_origin(websocket):
        # Browsers send cookies with WebSockets from any site: without this check another page
        # could open the console as the logged-in user.
        await websocket.close(code=4403, reason="cross-site connection refused")
        return

    manager: ServerManager = websocket.app.state.manager
    async with websocket.app.state.sessionmaker() as session:
        found = await user_for_token(session, websocket.cookies.get(COOKIE))
        if found is None:
            await websocket.close(code=4401, reason="log in first")
            return
        permissions = await server_permissions(session, found[0], server_id)
        server = await session.get(Server, server_id)
    if server is None or not permissions:
        await websocket.close(code=4404, reason="server not found")
        return

    await websocket.accept()
    pump = asyncio.create_task(_pump_logs(websocket, manager, server_id))
    try:
        while True:
            try:
                message = ClientMessage.model_validate(await websocket.receive_json())
            except (ValidationError, ValueError):
                await websocket.send_json({"type": "error", "data": "invalid message"})
                continue
            if message.type != "command" or not message.data.strip():
                continue
            if Permission.CONSOLE not in permissions:
                await websocket.send_json({"type": "error", "data": "you can't send commands to this server"})
                continue
            await _run_command(websocket, manager, server, message.data)
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)


def _same_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:
        return True  # not a browser (CLI tools, tests): no cookies to abuse
    return urlsplit(origin).netloc == websocket.headers.get("host")


async def _pump_logs(websocket: WebSocket, manager: ServerManager, server_id: str) -> None:
    try:
        async for line in follow_console(manager.runtime, server_id):
            await websocket.send_json({"type": "log", "data": line.rstrip("\n")})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("console stream for %s ended: %s", server_id, exc)
        try:
            await websocket.send_json({"type": "error", "data": "console stream lost, reconnecting…"})
            await websocket.close(code=1011)
        except Exception:
            pass  # the client is already gone


async def _run_command(websocket: WebSocket, manager: ServerManager, server: Server, command: str) -> None:
    if await manager.status_of(server) not in (ServerStatus.RUNNING, ServerStatus.STARTING):
        await websocket.send_json({"type": "error", "data": "the server is not running"})
        return
    try:
        await manager.send_command(server, command)
    except ServerBusy as exc:
        await websocket.send_json({"type": "error", "data": str(exc)})
    except RUNTIME_ERRORS as exc:
        await websocket.send_json({"type": "error", "data": f"docker: {exc}"})
