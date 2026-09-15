import posixpath
from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import quote

from aiodocker.exceptions import DockerError
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import Manager, Session
from app.api.servers import get_server_or_404
from app.models import ServerState
from app.runtime.docker import RuntimeUnavailable
from app.runtime.files import FileEntry, FileError, Upload, resolve

router = APIRouter(prefix="/servers/{server_id}/files", tags=["files"])


class Listing(BaseModel):
    path: str
    entries: list[FileEntry]


class PathBody(BaseModel):
    path: str


class RenameBody(BaseModel):
    path: str
    name: str = Field(min_length=1, max_length=255)


class TransferBody(BaseModel):
    sources: list[str] = Field(min_length=1, max_length=1000)
    destination: str


class DeleteBody(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=1000)


class ContentBody(BaseModel):
    path: str
    content: str


class ContentRead(BaseModel):
    path: str
    content: str


async def _files(server_id: str, session: Session, manager: Manager):
    server = await get_server_or_404(session, server_id)
    if server.state != ServerState.INSTALLED:
        raise HTTPException(409, "files are available once the server is installed")
    return manager.runtime.files


class _Errors:
    """FileError → its status; Docker trouble → 503."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if isinstance(exc, FileError):
            raise HTTPException(exc.status, str(exc)) from exc
        if isinstance(exc, RuntimeUnavailable | DockerError):
            raise HTTPException(503, f"docker: {exc}") from exc
        return False


errors = _Errors()


@router.get("")
async def list_files(server_id: str, session: Session, manager: Manager, path: str = "") -> Listing:
    files = await _files(server_id, session, manager)
    async with errors:
        entries = await files.list(server_id, path)
    # Folders first, then by name, case-insensitive: like every file manager.
    entries.sort(key=lambda e: (e.type != "dir", e.name.lower()))
    return Listing(path=resolve(path), entries=entries)


@router.post("/mkdir", status_code=status.HTTP_204_NO_CONTENT)
async def make_directory(server_id: str, body: PathBody, session: Session, manager: Manager) -> None:
    files = await _files(server_id, session, manager)
    async with errors:
        await files.mkdir(server_id, body.path)


@router.post("/rename", status_code=status.HTTP_204_NO_CONTENT)
async def rename(server_id: str, body: RenameBody, session: Session, manager: Manager) -> None:
    files = await _files(server_id, session, manager)
    async with errors:
        await files.rename(server_id, body.path, body.name)


@router.post("/move", status_code=status.HTTP_204_NO_CONTENT)
async def move(server_id: str, body: TransferBody, session: Session, manager: Manager) -> None:
    files = await _files(server_id, session, manager)
    async with errors:
        await files.move(server_id, body.sources, body.destination)


@router.post("/copy", status_code=status.HTTP_204_NO_CONTENT)
async def copy(server_id: str, body: TransferBody, session: Session, manager: Manager) -> None:
    files = await _files(server_id, session, manager)
    async with errors:
        await files.copy(server_id, body.sources, body.destination)


@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete(server_id: str, body: DeleteBody, session: Session, manager: Manager) -> None:
    files = await _files(server_id, session, manager)
    async with errors:
        await files.delete(server_id, body.paths)


@router.post("/extract")
async def extract(server_id: str, body: PathBody, session: Session, manager: Manager) -> PathBody:
    files = await _files(server_id, session, manager)
    async with errors:
        return PathBody(path=await files.extract(server_id, body.path))


@router.get("/content")
async def read_content(server_id: str, path: str, session: Session, manager: Manager) -> ContentRead:
    files = await _files(server_id, session, manager)
    async with errors:
        return ContentRead(path=resolve(path), content=await files.read_text(server_id, path))


@router.put("/content", status_code=status.HTTP_204_NO_CONTENT)
async def write_content(server_id: str, body: ContentBody, session: Session, manager: Manager) -> None:
    files = await _files(server_id, session, manager)
    async with errors:
        await files.write_text(server_id, body.path, body.content)


@router.post("/upload", status_code=status.HTTP_204_NO_CONTENT)
async def upload(
    server_id: str,
    session: Session,
    manager: Manager,
    files: Annotated[list[UploadFile], File()],
    # Relative path per file, same order: dropped folders keep their structure.
    paths: Annotated[list[str], Form()],
    # An empty form value arrives as missing, so the root is the default.
    directory: Annotated[str, Form()] = "",
) -> None:
    if len(paths) != len(files):
        raise HTTPException(422, "every file needs a path")
    file_ops = await _files(server_id, session, manager)
    uploads = [Upload(path=p, file=f.file) for p, f in zip(paths, files, strict=True)]
    async with errors:
        await file_ops.upload(server_id, directory, uploads)


def _attachment(filename: str) -> dict[str, str]:
    # RFC 5987, so non-ASCII names survive.
    return {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}


@router.get("/download")
async def download(
    server_id: str,
    session: Session,
    manager: Manager,
    path: str = "",
    names: Annotated[list[str] | None, Query()] = None,
) -> StreamingResponse:
    """One file as is: ?path=world/level.dat. Several items as .tar.gz: ?path=world&names=a&names=b"""
    files = await _files(server_id, session, manager)
    rel = resolve(path)

    stream: AsyncIterator[bytes]
    async with errors:
        if names:
            filename = (
                f"{posixpath.basename(rel) or 'server'}.tar.gz" if len(names) > 1 else f"{names[0]}.tar.gz"
            )
            entry = await files.stat(server_id, rel)
            if entry is None or entry.type != "dir":
                raise FileError("folder not found", 404)
            stream, media = files.download_archive(server_id, rel, names), "application/gzip"
        else:
            entry = await files.stat(server_id, rel)
            if entry is None:
                raise FileError("file not found", 404)
            if entry.type == "dir":
                parent, name = posixpath.split(rel)
                filename = f"{name or 'server'}.tar.gz"
                stream, media = files.download_archive(server_id, parent, [name]), "application/gzip"
            else:
                filename = entry.name
                stream, media = files.download(server_id, rel), "application/octet-stream"
    headers = _attachment(filename)
    if not names and entry.type != "dir":
        headers["Content-Length"] = str(entry.size)
    return StreamingResponse(stream, media_type=media, headers=headers)
