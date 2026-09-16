"""Files through a helper container.

The game container may be stopped, and panel access to the volume on the host needs root.
So file operations run in a tiny busybox container that mounts the server's volume at /data:
no network, started on demand and removed after a few idle minutes. A symlink in the volume
pointing "outside" leads into the empty helper filesystem, never onto the host.
"""

import asyncio
import io
import logging
import posixpath
import re
import tarfile
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path

import aiodocker
from aiodocker.exceptions import DockerError

from app.runtime.docker import container_name, volume_name
from app.runtime.files import (
    MAX_TEXT_BYTES,
    SEARCH_LIMIT,
    FileEntry,
    FileError,
    SearchMatch,
    Upload,
    resolve,
    validate_name,
)

log = logging.getLogger(__name__)

HELPER_IMAGE = "busybox:stable"
ROOT = "/data"
IDLE_SECONDS = 600
_TYPES = {
    "directory": "dir",
    "regular file": "file",
    "regular empty file": "file",
    "symbolic link": "symlink",
}


def _abs(path: str) -> str:
    rel = resolve(path)
    return f"{ROOT}/{rel}" if rel else ROOT


def _parse_stat_line(line: str) -> FileEntry | None:
    parts = line.split("|", 3)
    if len(parts) != 4:
        return None
    kind, size, mtime, full_path = parts
    return FileEntry(
        name=posixpath.basename(full_path),
        type=_TYPES.get(kind, "other"),
        size=int(size),
        mtime=int(mtime),
    )


class DockerFiles:
    def __init__(self, docker: aiodocker.Docker):
        self._docker = docker
        self._last_used: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # --- helper container ----------------------------------------------------

    def _name(self, server_id: str) -> str:
        return f"{container_name(server_id)}-files"

    async def _helper(self, server_id: str):
        self._last_used[server_id] = time.monotonic()
        async with self._locks.setdefault(server_id, asyncio.Lock()):
            try:
                container = await self._docker.containers.get(self._name(server_id))
                if container["State"]["Running"]:
                    return container
                await container.delete(force=True)
            except DockerError as exc:
                if exc.status != 404:
                    raise
            config = {
                "Image": HELPER_IMAGE,
                "Cmd": ["sleep", "infinity"],
                "Labels": {"possum.managed": "true", "possum.server_id": server_id, "possum.role": "files"},
                "HostConfig": {
                    "Binds": [f"{volume_name(server_id)}:{ROOT}"],
                    "NetworkMode": "none",
                    "AutoRemove": True,
                    "Init": True,
                },
            }
            try:
                return await self._docker.containers.run(config, name=self._name(server_id))
            except DockerError as exc:
                if exc.status != 404:
                    raise
                await self._docker.images.pull(HELPER_IMAGE)
                return await self._docker.containers.run(config, name=self._name(server_id))

    async def close(self, server_id: str) -> None:
        self._last_used.pop(server_id, None)
        try:
            container = await self._docker.containers.get(self._name(server_id))
            await container.delete(force=True)
        except DockerError as exc:
            if exc.status != 404:
                raise

    async def reap_idle(self, idle_seconds: int = IDLE_SECONDS) -> None:
        now = time.monotonic()
        for server_id, used in list(self._last_used.items()):
            if now - used > idle_seconds:
                log.info("stopping idle file helper for %s", server_id)
                try:
                    await self.close(server_id)
                except DockerError:
                    log.exception("could not stop the file helper for %s", server_id)

    async def _exec(self, server_id: str, *cmd: str) -> bytes:
        """Run a command (argv, never a shell string) in the helper; stdout, or FileError on failure."""
        container = await self._helper(server_id)
        execution = await container.exec(list(cmd))
        out, err = bytearray(), bytearray()
        async with execution.start(detach=False) as stream:
            while (message := await stream.read_out()) is not None:
                (out if message.stream == 1 else err).extend(message.data)
        if (await execution.inspect())["ExitCode"] != 0:
            detail = err.decode(errors="replace").strip().splitlines()
            raise FileError(detail[-1] if detail else f"{cmd[0]} failed")
        return bytes(out)

    async def _exec_stream(self, server_id: str, *cmd: str) -> AsyncIterator[bytes]:
        container = await self._helper(server_id)
        execution = await container.exec(list(cmd), stderr=False)
        async with execution.start(detach=False) as stream:
            while (message := await stream.read_out()) is not None:
                self._last_used[server_id] = time.monotonic()
                yield message.data

    # --- queries -------------------------------------------------------------

    async def stat(self, server_id: str, path: str) -> FileEntry | None:
        try:
            out = await self._exec(server_id, "stat", "-c", "%F|%s|%Y|%n", _abs(path))
        except FileError:
            return None
        return _parse_stat_line(out.decode(errors="replace").strip())

    async def _require(self, server_id: str, path: str, kind: str | None = None) -> FileEntry:
        entry = await self.stat(server_id, path)
        if entry is None:
            raise FileError(f"'{resolve(path) or '/'}' not found", 404)
        if kind and entry.type != kind:
            raise FileError(f"'{resolve(path)}' is not a {'folder' if kind == 'dir' else 'file'}")
        return entry

    async def _owner(self, server_id: str, path: str) -> str:
        return (await self._exec(server_id, "stat", "-c", "%u:%g", _abs(path))).decode().strip()

    async def list(self, server_id: str, path: str) -> list[FileEntry]:
        await self._require(server_id, path, "dir")
        out = await self._exec(
            server_id, "find", _abs(path), "-mindepth", "1", "-maxdepth", "1",
            "-exec", "stat", "-c", "%F|%s|%Y|%n", "{}", "+",
        )  # fmt: skip
        entries = [_parse_stat_line(line) for line in out.decode(errors="replace").splitlines()]
        return [e for e in entries if e is not None]

    async def search(self, server_id: str, path: str, query: str) -> tuple[list[SearchMatch], bool]:
        await self._require(server_id, path, "dir")
        pattern = "*" + re.sub(r"([*?\[\]\\])", r"\\\1", query) + "*"
        out = await self._exec(
            server_id, "find", _abs(path), "-mindepth", "1", "-iname", pattern,
            "-exec", "stat", "-c", "%F|%s|%Y|%n", "{}", "+",
        )  # fmt: skip
        matches = []
        for line in out.decode(errors="replace").splitlines():
            entry = _parse_stat_line(line)
            if entry is None:
                continue
            if len(matches) == SEARCH_LIMIT:
                return matches, True
            full = line.split("|", 3)[3]
            relative = full[len(ROOT) + 1 :] if full.startswith(ROOT + "/") else full
            matches.append(SearchMatch(path=relative, type=entry.type, size=entry.size, mtime=entry.mtime))
        return matches, False

    # --- changes -------------------------------------------------------------

    async def ensure_dirs(self, server_id: str, paths: list[str]) -> None:
        if targets := [_abs(p) for p in paths if resolve(p)]:
            await self._exec(server_id, "mkdir", "-p", *targets)

    async def mkdir(self, server_id: str, path: str) -> None:
        rel = resolve(path)
        if not rel:
            raise FileError("invalid folder name")
        parent = posixpath.dirname(rel)
        await self._require(server_id, parent, "dir")
        if await self.stat(server_id, rel):
            raise FileError(f"'{posixpath.basename(rel)}' already exists", 409)
        await self._exec(server_id, "mkdir", _abs(rel))
        # The helper runs as root; hand the folder to whoever owns the parent (the game's user).
        await self._exec(server_id, "chown", await self._owner(server_id, parent), _abs(rel))

    async def rename(self, server_id: str, path: str, new_name: str) -> None:
        rel = resolve(path)
        if not rel:
            raise FileError("can't rename the root folder")
        target = posixpath.join(posixpath.dirname(rel), validate_name(new_name))
        await self._require(server_id, rel)
        if await self.stat(server_id, target):
            raise FileError(f"'{new_name}' already exists", 409)
        await self._exec(server_id, "mv", "-n", _abs(rel), _abs(target))

    async def _check_transfer(self, server_id: str, sources: list[str], destination: str) -> list[str]:
        dest = resolve(destination)
        await self._require(server_id, dest, "dir")
        existing = {e.name for e in await self.list(server_id, dest)}
        rels = []
        for source in sources:
            rel = resolve(source)
            if not rel:
                raise FileError("can't move the root folder")
            if dest == rel or dest.startswith(rel + "/"):
                raise FileError(f"can't put '{rel}' inside itself")
            if posixpath.dirname(rel) == dest:
                continue  # already there
            if posixpath.basename(rel) in existing:
                raise FileError(f"'{posixpath.basename(rel)}' already exists in the destination", 409)
            rels.append(rel)
        return rels

    async def move(self, server_id: str, sources: list[str], destination: str) -> None:
        if rels := await self._check_transfer(server_id, sources, destination):
            await self._exec(server_id, "mv", "-n", *map(_abs, rels), _abs(destination))

    async def copy(self, server_id: str, sources: list[str], destination: str) -> None:
        if rels := await self._check_transfer(server_id, sources, destination):
            await self._exec(server_id, "cp", "-a", "-n", *map(_abs, rels), _abs(destination))

    async def delete(self, server_id: str, paths: list[str]) -> None:
        rels = [resolve(p) for p in paths]
        if not rels or "" in rels:
            raise FileError("can't delete the root folder")
        await self._exec(server_id, "rm", "-rf", *map(_abs, rels))

    # --- content -------------------------------------------------------------

    async def read_text(self, server_id: str, path: str) -> str:
        entry = await self._require(server_id, path, "file")
        if entry.size > MAX_TEXT_BYTES:
            raise FileError("the file is too big to edit in the browser, download it instead", 413)
        data = await self._exec(server_id, "cat", _abs(path))
        if b"\0" in data:
            raise FileError("this doesn't look like a text file", 415)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return data.decode("latin-1")

    async def write_text(self, server_id: str, path: str, content: str) -> None:
        rel = resolve(path)
        data = content.encode("utf-8")
        if len(data) > MAX_TEXT_BYTES:
            raise FileError("the file is too big", 413)
        await self._require(server_id, rel, "file")
        # Keep owner and permissions: the game must still be able to read its file.
        owner, mode = (await self._exec(server_id, "stat", "-c", "%u:%g %a", _abs(rel))).decode().split()
        info = self._tar_info(posixpath.basename(rel), len(data), owner, int(mode, 8))
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            tar.addfile(info, io.BytesIO(data))
        container = await self._helper(server_id)
        await container.put_archive(_abs(posixpath.dirname(rel)), buffer.getvalue())

    async def upload(self, server_id: str, directory: str, uploads: list[Upload]) -> None:
        directory = resolve(directory)
        await self._require(server_id, directory, "dir")
        owner = await self._owner(server_id, directory)
        # Spooled to disk past 32 MB: a world upload must not live in RAM.
        with tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024) as archive:
            await asyncio.to_thread(self._build_upload_tar, archive, uploads, owner)
            archive.seek(0)
            container = await self._helper(server_id)
            await container.put_archive(_abs(directory), archive)

    @staticmethod
    def _tar_info(
        name: str, size: int, owner: str, mode: int = 0o644, is_dir: bool = False
    ) -> tarfile.TarInfo:
        uid, gid = (int(x) for x in owner.split(":"))
        info = tarfile.TarInfo(name)
        info.type = tarfile.DIRTYPE if is_dir else tarfile.REGTYPE
        info.size, info.mtime, info.uid, info.gid = (0 if is_dir else size), int(time.time()), uid, gid
        info.mode = 0o755 if is_dir else mode
        return info

    def _build_upload_tar(self, fileobj, uploads: list[Upload], owner: str) -> None:
        folders: set[str] = set()
        with tarfile.open(fileobj=fileobj, mode="w") as tar:
            for upload in uploads:
                rel = resolve(upload.path)
                parts = rel.split("/")
                if not rel or any(validate_name(p) != p for p in parts):
                    raise FileError(f"invalid upload path '{upload.path}'")
                # Explicit folder entries, otherwise Docker creates missing parents owned by root.
                for i in range(1, len(parts)):
                    folder = "/".join(parts[:i])
                    if folder not in folders:
                        folders.add(folder)
                        tar.addfile(self._tar_info(folder, 0, owner, is_dir=True))
                upload.file.seek(0, io.SEEK_END)
                size = upload.file.tell()
                upload.file.seek(0)
                tar.addfile(self._tar_info(rel, size, owner), upload.file)

    async def usage(self, server_id: str, paths: list[str]) -> int:
        out = await self._exec(server_id, "du", "-skc", *([_abs(p) for p in paths] or [_abs("")]))
        return int(out.decode().strip().splitlines()[-1].split()[0]) * 1024

    async def unpacked_size(self, server_id: str, path: str) -> int:
        await self._require(server_id, path, "file")
        try:
            out = await self._exec(server_id, "unzip", "-l", _abs(path))
            # The last line of "unzip -l": "  5000002                     2 files"
            return int(out.decode().strip().splitlines()[-1].split()[0])
        except (FileError, ValueError, IndexError) as exc:
            raise FileError("not a readable .zip archive") from exc

    async def extract(self, server_id: str, path: str) -> str:
        """Unzip next to the archive, into a folder named after it. Returns that folder."""
        rel = resolve(path)
        await self._require(server_id, rel, "file")
        base = posixpath.basename(rel)
        if not base.lower().endswith(".zip"):
            raise FileError("only .zip archives can be extracted")
        target = posixpath.join(posixpath.dirname(rel), base[:-4] or "archive")
        if await self.stat(server_id, target):
            raise FileError(f"'{posixpath.basename(target)}' already exists", 409)
        await self._exec(server_id, "mkdir", _abs(target))
        try:
            await self._exec(server_id, "unzip", "-q", "-o", _abs(rel), "-d", _abs(target))
        except FileError:
            await self._exec(server_id, "rm", "-rf", _abs(target))
            raise
        owner = await self._owner(server_id, posixpath.dirname(rel))
        await self._exec(server_id, "chown", "-R", owner, _abs(target))
        return target

    async def download(self, server_id: str, path: str) -> AsyncIterator[bytes]:
        async for chunk in self._exec_stream(server_id, "cat", _abs(path)):
            yield chunk

    # --- backups -------------------------------------------------------------

    async def archive_to(
        self, server_id: str, target: Path, paths: list[str] | None, exclude: list[str]
    ) -> tuple[int, list[str] | None]:
        """Write a .tar.gz of the volume (or just `paths`) to a file on the panel's disk.

        Returns its size and the paths it really contains (missing ones are skipped).
        """
        if paths is None:
            members, present = ["."], None
        else:
            present = [
                p for p in dict.fromkeys(resolve(p) for p in paths) if p and await self.stat(server_id, p)
            ]
            if not present:
                raise FileError("nothing to back up: none of the game's backup paths exist yet")
            members = [f"./{p}" for p in present]

        args = ["tar", "-czf", "-", "-C", ROOT]
        for pattern in exclude:
            # Anchored at the top: "./cache" leaves out /data/cache but not config/cache.
            args += ["--exclude", f"./{pattern}"]

        container = await self._helper(server_id)
        execution = await container.exec([*args, *members])
        errors, size = bytearray(), 0
        handle = await asyncio.to_thread(open, target, "wb")
        try:
            async with execution.start(detach=False) as stream:
                while (message := await stream.read_out()) is not None:
                    if message.stream != 1:
                        errors.extend(message.data)
                        continue
                    await asyncio.to_thread(handle.write, message.data)
                    size += len(message.data)
                    self._last_used[server_id] = time.monotonic()  # a long backup isn't "idle"
        finally:
            await asyncio.to_thread(handle.close)
        if (await execution.inspect())["ExitCode"] != 0:
            detail = errors.decode(errors="replace").strip().splitlines()
            raise FileError(detail[-1] if detail else "tar failed")
        return size, present

    async def restore_from(
        self, server_id: str, source: Path, paths: list[str] | None, exclude: list[str]
    ) -> None:
        """Replace the volume's contents (or just `paths`) with a backup made by archive_to.

        Top-level entries the backup excluded (caches, downloaded libraries) are left as they are,
        so the server still starts without having to download them again.
        """
        if paths is None:
            keep = [arg for pattern in exclude for arg in ("!", "-name", pattern)]
            await self._exec(
                server_id,
                "find",
                ROOT,
                "-mindepth",
                "1",
                "-maxdepth",
                "1",
                *keep,
                "-exec",
                "rm",
                "-rf",
                "{}",
                "+",
            )
        elif targets := [_abs(p) for p in paths if resolve(p)]:
            await self._exec(server_id, "rm", "-rf", *targets)

        container = await self._helper(server_id)
        handle = await asyncio.to_thread(open, source, "rb")
        try:
            # Docker unpacks .tar.gz itself and keeps owners from the archive.
            await container.put_archive(ROOT, handle)
        finally:
            await asyncio.to_thread(handle.close)

    async def download_archive(
        self, server_id: str, directory: str, names: list[str]
    ) -> AsyncIterator[bytes]:
        # "./name": a file called "-rf" must stay a file name, not become a tar option.
        names = [f"./{validate_name(n)}" for n in names]
        async for chunk in self._exec_stream(server_id, "tar", "-czf", "-", "-C", _abs(directory), *names):
            yield chunk
