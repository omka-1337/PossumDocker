import asyncio
from datetime import datetime
from pathlib import Path

from aiodocker.exceptions import DockerError
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import Manager, Session
from app.api.servers import get_server_or_404
from app.core.auth import allow
from app.core.permissions import Permission
from app.models import Backup, BackupStatus
from app.runtime.docker import RuntimeUnavailable
from app.runtime.manager import ServerBusy, StorageFull

router = APIRouter(prefix="/servers/{server_id}/backups", tags=["backups"])


class BackupRead(BaseModel):
    id: str
    note: str | None
    status: BackupStatus
    message: str | None
    size: int
    # What it holds; None: the whole server.
    paths: list[str] | None
    # Made by a schedule (its id), or by hand (None).
    schedule_id: str | None
    created_at: datetime


class BackupList(BaseModel):
    backups: list[BackupRead]
    total_size: int
    # Bytes this server's backups may take; None: no limit.
    limit: int | None
    # The last restore of this server failed with this message.
    restore_error: str | None


class BackupCreate(BaseModel):
    note: str | None = Field(default=None, max_length=200)


def to_read(backup: Backup) -> BackupRead:
    return BackupRead.model_validate(backup, from_attributes=True)


async def get_backup_or_404(session: Session, server_id: str, backup_id: str) -> Backup:
    backup = await session.get(Backup, backup_id)
    if backup is None or backup.server_id != server_id:
        raise HTTPException(404, "backup not found")
    return backup


def _conflict(exc: Exception) -> HTTPException:
    if isinstance(exc, StorageFull):
        return HTTPException(507, str(exc))
    if isinstance(exc, ServerBusy):
        return HTTPException(409, str(exc))
    return HTTPException(503, f"docker: {exc}")


@router.get("", dependencies=[allow(Permission.BACKUPS, Permission.RESTORE)])
async def list_backups(server_id: str, session: Session, manager: Manager) -> BackupList:
    server = await get_server_or_404(session, server_id)
    backups = (
        await session.scalars(
            select(Backup).where(Backup.server_id == server_id).order_by(Backup.created_at.desc())
        )
    ).all()
    return BackupList(
        backups=[to_read(b) for b in backups],
        total_size=sum(b.size for b in backups),
        limit=manager.limits_of(server)[1],
        restore_error=manager.restore_errors.get(server_id),
    )


@router.post("", status_code=status.HTTP_202_ACCEPTED, dependencies=[allow(Permission.BACKUPS)])
async def create_backup(server_id: str, body: BackupCreate, session: Session, manager: Manager) -> BackupRead:
    """Starts the backup; it shows up as `creating` and turns `ready` or `failed`."""
    server = await get_server_or_404(session, server_id)
    note = body.note.strip() if body.note and body.note.strip() else None
    try:
        return to_read(await manager.create_backup(server, note=note))
    except (ServerBusy, RuntimeUnavailable, DockerError) as exc:
        raise _conflict(exc) from exc


@router.post(
    "/{backup_id}/restore", status_code=status.HTTP_202_ACCEPTED, dependencies=[allow(Permission.RESTORE)]
)
async def restore_backup(server_id: str, backup_id: str, session: Session, manager: Manager) -> None:
    """Replaces the server's files with the backup. The server must be stopped; it shows `restoring`."""
    server = await get_server_or_404(session, server_id)
    backup = await get_backup_or_404(session, server_id, backup_id)
    if not await _exists(manager.backup_file(backup)):
        raise HTTPException(410, "the backup file is missing from the panel's disk")
    try:
        await manager.restore_backup(server, backup)
    except (ServerBusy, RuntimeUnavailable, DockerError) as exc:
        raise _conflict(exc) from exc


@router.get("/{backup_id}/download", dependencies=[allow(Permission.BACKUPS)])
async def download_backup(server_id: str, backup_id: str, session: Session, manager: Manager) -> FileResponse:
    server = await get_server_or_404(session, server_id)
    backup = await get_backup_or_404(session, server_id, backup_id)
    path = manager.backup_file(backup)
    if backup.status != BackupStatus.READY or not await _exists(path):
        raise HTTPException(404, "this backup has no file to download")
    stamp = backup.created_at.strftime("%Y%m%d-%H%M%S")
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in server.name)[:40] or "server"
    return FileResponse(path, media_type="application/gzip", filename=f"{safe_name}-{stamp}.tar.gz")


@router.delete(
    "/{backup_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[allow(Permission.BACKUPS)]
)
async def delete_backup(server_id: str, backup_id: str, session: Session, manager: Manager) -> None:
    backup = await get_backup_or_404(session, server_id, backup_id)
    try:
        await manager.delete_backup(backup)
    except ServerBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    await session.delete(backup)
    await session.commit()


async def _exists(path: Path) -> bool:
    return await asyncio.to_thread(path.exists)
