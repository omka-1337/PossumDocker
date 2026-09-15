"""File operations on a server's data volume.

Paths in the API are relative to the volume root and always normalised with `resolve()`,
so "../" can never leave it.
"""

import posixpath
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import BinaryIO, Literal, Protocol

MAX_TEXT_BYTES = 2 * 1024 * 1024


class FileError(Exception):
    """A user-facing problem: not found, already exists, not a text file..."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class FileEntry:
    name: str
    type: Literal["file", "dir", "symlink", "other"]
    size: int
    mtime: int


@dataclass(frozen=True)
class Upload:
    # Relative to the upload's target directory; may contain folders ("world/level.dat").
    path: str
    file: BinaryIO


def resolve(path: str) -> str:
    """'/a/../b', 'b/', '' → 'b', ''. The result never starts with '/' or contains '..'."""
    normalised = posixpath.normpath("/" + (path or "")).lstrip("/")
    return "" if normalised == "." else normalised


def validate_name(name: str) -> str:
    if not name or name in (".", "..") or "/" in name or "\0" in name or len(name) > 255:
        raise FileError("invalid name")
    return name


class Files(Protocol):
    async def list(self, server_id: str, path: str) -> list[FileEntry]: ...
    async def mkdir(self, server_id: str, path: str) -> None: ...
    async def rename(self, server_id: str, path: str, new_name: str) -> None: ...
    async def move(self, server_id: str, sources: list[str], destination: str) -> None: ...
    async def copy(self, server_id: str, sources: list[str], destination: str) -> None: ...
    async def delete(self, server_id: str, paths: list[str]) -> None: ...
    async def read_text(self, server_id: str, path: str) -> str: ...
    async def write_text(self, server_id: str, path: str, content: str) -> None: ...
    async def upload(self, server_id: str, directory: str, uploads: list[Upload]) -> None: ...
    async def extract(self, server_id: str, path: str) -> str: ...
    def download(self, server_id: str, path: str) -> AsyncIterator[bytes]: ...
    def download_archive(self, server_id: str, directory: str, names: list[str]) -> AsyncIterator[bytes]: ...
    async def stat(self, server_id: str, path: str) -> FileEntry | None: ...
    async def close(self, server_id: str) -> None: ...
