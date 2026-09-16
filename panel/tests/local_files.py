"""A Files implementation on a local directory, standing in for the Docker helper in tests."""

import io
import os
import posixpath
import shutil
import tarfile
import zipfile
from pathlib import Path

from app.runtime.files import MAX_TEXT_BYTES, FileEntry, FileError, Upload, resolve, validate_name


class LocalFiles:
    def __init__(self, root: Path):
        self.root = root

    def _path(self, server_id: str, rel: str) -> Path:
        base = self.root / server_id
        base.mkdir(parents=True, exist_ok=True)
        return base / resolve(rel)

    def _entry(self, path: Path) -> FileEntry:
        kind = "symlink" if path.is_symlink() else "dir" if path.is_dir() else "file"
        stat = path.lstat()
        return FileEntry(name=path.name, type=kind, size=stat.st_size, mtime=int(stat.st_mtime))

    async def stat(self, server_id, path):
        p = self._path(server_id, path)
        return self._entry(p) if p.exists() or p.is_symlink() else None

    async def list(self, server_id, path):
        p = self._path(server_id, path)
        if not p.is_dir():
            raise FileError("not found", 404)
        return [self._entry(child) for child in p.iterdir()]

    async def mkdir(self, server_id, path):
        p = self._path(server_id, path)
        if p.exists():
            raise FileError(f"'{p.name}' already exists", 409)
        p.mkdir()

    async def ensure_dirs(self, server_id, paths):
        for path in paths:
            self._path(server_id, path).mkdir(parents=True, exist_ok=True)

    async def rename(self, server_id, path, new_name):
        p = self._path(server_id, path)
        target = p.with_name(validate_name(new_name))
        if target.exists():
            raise FileError(f"'{new_name}' already exists", 409)
        p.rename(target)

    def _transfer(self, server_id, sources, destination, op):
        dest = resolve(destination)
        for source in sources:
            rel = resolve(source)
            if not rel:
                raise FileError("can't move the root folder")
            if dest == rel or dest.startswith(rel + "/"):
                raise FileError(f"can't put '{rel}' inside itself")
            target = self._path(server_id, dest) / posixpath.basename(rel)
            if target.exists():
                raise FileError(f"'{target.name}' already exists in the destination", 409)
            op(self._path(server_id, rel), target)

    async def move(self, server_id, sources, destination):
        self._transfer(server_id, sources, destination, shutil.move)

    async def copy(self, server_id, sources, destination):
        def copy(src: Path, dst: Path):
            shutil.copytree(src, dst) if src.is_dir() else shutil.copy2(src, dst)

        self._transfer(server_id, sources, destination, copy)

    async def delete(self, server_id, paths):
        for path in paths:
            if not resolve(path):
                raise FileError("can't delete the root folder")
            p = self._path(server_id, path)
            shutil.rmtree(p) if p.is_dir() else p.unlink()

    async def read_text(self, server_id, path):
        data = self._path(server_id, path).read_bytes()
        if len(data) > MAX_TEXT_BYTES:
            raise FileError("too big", 413)
        if b"\0" in data:
            raise FileError("this doesn't look like a text file", 415)
        return data.decode()

    async def write_text(self, server_id, path, content):
        self._path(server_id, path).write_text(content)

    async def upload(self, server_id, directory, uploads: list[Upload]):
        for upload in uploads:
            rel = resolve(upload.path)
            if not rel or any(validate_name(p) != p for p in rel.split("/")):
                raise FileError(f"invalid upload path '{upload.path}'")
            target = self._path(server_id, posixpath.join(resolve(directory), rel))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(upload.file.read())

    async def usage(self, server_id, paths):
        targets = [self._path(server_id, p) for p in paths] or [self._path(server_id, "")]
        files = {f for t in targets for f in ([t] if t.is_file() else t.rglob("*")) if f.is_file()}
        return sum(f.stat().st_size for f in files)

    async def unpacked_size(self, server_id, path):
        with zipfile.ZipFile(self._path(server_id, path)) as archive:
            return sum(info.file_size for info in archive.infolist())

    async def extract(self, server_id, path):
        rel = resolve(path)
        target = rel[:-4]
        with zipfile.ZipFile(self._path(server_id, rel)) as archive:
            archive.extractall(self._path(server_id, target))
        return target

    async def download(self, server_id, path):
        yield self._path(server_id, path).read_bytes()

    async def download_archive(self, server_id, directory, names):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
            for name in names:
                tar.add(self._path(server_id, posixpath.join(directory, name)), arcname=name)
        yield buffer.getvalue()

    async def archive_to(self, server_id, target, paths, exclude):
        base = self._path(server_id, "")
        present = None if paths is None else [p for p in paths if (base / p).exists()]
        if present == []:
            raise FileError("nothing to back up: none of the game's backup paths exist yet")
        with tarfile.open(target, "w:gz") as tar:
            for member in present if present is not None else sorted(os.listdir(base)):
                if member not in exclude:
                    tar.add(base / member, arcname=f"./{member}")
        return target.stat().st_size, present

    async def restore_from(self, server_id, source, paths, exclude):
        import fnmatch

        base = self._path(server_id, "")
        full = [c for c in base.iterdir() if not any(fnmatch.fnmatch(c.name, p) for p in exclude)]
        for child in [base / p for p in paths] if paths is not None else full:
            if child.is_dir():
                shutil.rmtree(child)
            elif child.exists():
                child.unlink()
        with tarfile.open(source, "r:gz") as tar:
            tar.extractall(base, filter="data")

    async def close(self, server_id):
        pass
