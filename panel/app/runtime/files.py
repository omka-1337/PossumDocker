"""File operations on a server's data volume.

Paths in the API are relative to the volume root and always normalised with `resolve()`,
so "../" can never leave it.
"""

import posixpath
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
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
class SearchMatch:
    # Relative to the volume, e.g. "world/region/r.0.0.mca"
    path: str
    type: Literal["file", "dir", "symlink", "other"]
    size: int
    mtime: int


SEARCH_LIMIT = 500


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
    # Names containing `query` (ignoring case) anywhere under `path`; True: more than SEARCH_LIMIT.
    async def search(self, server_id: str, path: str, query: str) -> tuple[list[SearchMatch], bool]: ...
    async def mkdir(self, server_id: str, path: str) -> None: ...
    async def ensure_dirs(self, server_id: str, paths: list[str]) -> None: ...
    async def rename(self, server_id: str, path: str, new_name: str) -> None: ...
    async def move(self, server_id: str, sources: list[str], destination: str) -> None: ...
    async def copy(self, server_id: str, sources: list[str], destination: str) -> None: ...
    async def delete(self, server_id: str, paths: list[str]) -> None: ...
    async def read_text(self, server_id: str, path: str) -> str: ...
    async def write_text(self, server_id: str, path: str, content: str) -> None: ...
    async def upload(self, server_id: str, directory: str, uploads: list[Upload]) -> None: ...
    async def extract(self, server_id: str, path: str) -> str: ...
    # Bytes on disk of these paths (none: the whole volume), and what a .zip says it unpacks to.
    async def usage(self, server_id: str, paths: list[str]) -> int: ...
    async def unpacked_size(self, server_id: str, path: str) -> int: ...
    def download(self, server_id: str, path: str) -> AsyncIterator[bytes]: ...
    def download_archive(self, server_id: str, directory: str, names: list[str]) -> AsyncIterator[bytes]: ...
    async def stat(self, server_id: str, path: str) -> FileEntry | None: ...
    async def archive_to(
        self, server_id: str, target: Path, paths: list[str] | None, exclude: list[str]
    ) -> tuple[int, list[str] | None]: ...
    async def restore_from(
        self, server_id: str, source: Path, paths: list[str] | None, exclude: list[str]
    ) -> None: ...
    async def close(self, server_id: str) -> None: ...
