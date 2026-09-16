import posixpath
from collections.abc import AsyncIterator
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from app.api.deps import Manager, Session
from app.api.servers import get_server_or_404
from app.core.auth import allow
from app.core.permissions import Permission
from app.models import ServerState
from app.runtime.files import FileEntry, FileError, SearchMatch, Upload, resolve
from app.runtime.manager import StorageFull
from app.runtime.state import RUNTIME_ERRORS

router = APIRouter(
    prefix="/servers/{server_id}/files", tags=["files"], dependencies=[allow(Permission.FILES)]
)


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


async def _server(server_id: str, session: Session):
    server = await get_server_or_404(session, server_id)
    if server.state != ServerState.INSTALLED:
        raise HTTPException(409, "files are available once the server is installed")
    return server


async def _files(server_id: str, session: Session, manager: Manager):
    await _server(server_id, session)
    return manager.runtime.files


class _Errors:
    """FileError → its status; Docker trouble → 503."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if isinstance(exc, FileError):
            raise HTTPException(exc.status, str(exc)) from exc
        if isinstance(exc, StorageFull):
            raise HTTPException(507, str(exc)) from exc
        if isinstance(exc, RUNTIME_ERRORS):
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


class SearchResults(BaseModel):
    matches: list[SearchMatch]
    # There were more than these: narrow the search.
    truncated: bool


@router.get("/search")
async def search_files(
    server_id: str,
    session: Session,
    manager: Manager,
    q: Annotated[str, Query(min_length=2, max_length=100)],
    path: str = "",
) -> SearchResults:
    """Files and folders whose name contains `q`, in `path` and everything under it."""
    files = await _files(server_id, session, manager)
    async with errors:
        matches, truncated = await files.search(server_id, path, q)
    matches.sort(key=lambda m: (m.path.count("/"), m.path.lower()))
    return SearchResults(matches=matches, truncated=truncated)


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
    server = await _server(server_id, session)
    files = manager.runtime.files
    async with errors:
        if manager.limits_of(server)[0] is not None:
            await manager.require_space(server, await files.usage(server_id, body.sources))
        await files.copy(server_id, body.sources, body.destination)


@router.post("/delete", status_code=status.HTTP_204_NO_CONTENT)
async def delete(server_id: str, body: DeleteBody, session: Session, manager: Manager) -> None:
    files = await _files(server_id, session, manager)
    async with errors:
        await files.delete(server_id, body.paths)


@router.post("/extract")
async def extract(server_id: str, body: PathBody, session: Session, manager: Manager) -> PathBody:
    server = await _server(server_id, session)
    files = manager.runtime.files
    async with errors:
        limited = manager.limits_of(server)[0] is not None
        if limited:
            # What the archive says it holds first; a zip bomb usually tells the truth about that.
            await manager.require_space(server, await files.unpacked_size(server_id, body.path))
        target = await files.extract(server_id, body.path)
        if limited and (problem := await manager.check_disk(server)):
            # It lied: the files came out bigger. Take them away again.
            await files.delete(server_id, [target])
            raise StorageFull(f"the unpacked archive took the server {problem}")
        return PathBody(path=target)


@router.get("/content")
async def read_content(server_id: str, path: str, session: Session, manager: Manager) -> ContentRead:
    files = await _files(server_id, session, manager)
    async with errors:
        return ContentRead(path=resolve(path), content=await files.read_text(server_id, path))


@router.put("/content", status_code=status.HTTP_204_NO_CONTENT)
async def write_content(server_id: str, body: ContentBody, session: Session, manager: Manager) -> None:
    server = await _server(server_id, session)
    files = manager.runtime.files
    async with errors:
        if manager.limits_of(server)[0] is not None:
            await manager.require_space(server, len(body.content.encode()))
        await files.write_text(server_id, body.path, body.content)


MAX_UPLOAD_FILES = 10_000


@router.post(
    "/upload",
    status_code=status.HTTP_204_NO_CONTENT,
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {"type": "array", "items": {"type": "string", "format": "binary"}},
                            # Relative path per file, same order: dropped folders keep their structure.
                            "paths": {"type": "array", "items": {"type": "string"}},
                            "directory": {"type": "string"},
                        },
                    }
                }
            }
        }
    },
)
async def upload(server_id: str, request: Request, session: Session, manager: Manager) -> None:
    server = await _server(server_id, session)
    file_ops = manager.runtime.files
    async with errors:
        if manager.limits_of(server)[0] is not None:
            # Before reading the body: a form is saved to the panel's disk while it's parsed, so a
            # too-big upload has to be turned away by its announced size.
            try:
                announced = int(request.headers["content-length"])
            except (KeyError, ValueError):
                raise HTTPException(411, "uploads need a Content-Length") from None
            await manager.require_space(server, announced)

        form = await request.form(max_files=MAX_UPLOAD_FILES, max_fields=MAX_UPLOAD_FILES + 10)
        try:
            files = [f for f in form.getlist("files") if isinstance(f, UploadFile)]
            paths = [p for p in form.getlist("paths") if isinstance(p, str)]
            directory = form.get("directory")
            if not files or len(paths) != len(files):
                raise HTTPException(422, "every file needs a path")
            uploads = [Upload(path=p, file=f.file) for p, f in zip(paths, files, strict=True)]
            # An empty form value is the root.
            await file_ops.upload(server_id, directory if isinstance(directory, str) else "", uploads)
        finally:
            await form.close()


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
