"""File and blob storage for things the app writes but never treats as data (PRD 8.12, DK6).

Each user space has two stores: `output` (exported PDFs, HTML and the static public site; always
disposable) and `backups` (database backups). Keys are relative, `/`-separated paths. Locally each
store is a directory under the output or backup root. A host can supply another implementation
through `App(file_store_factory=...)`.

User input files in the data folder are not blobs: only the YAML/Markdown sync adapter reads and
writes them.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Callable, Protocol


class FileStore(Protocol):
    def put(self, key: str, data: bytes) -> str:
        """Store `data` under `key`; return where it went, for people to read (a path, a URL)."""
        ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def list(self, prefix: str = "") -> list[str]:
        """Keys starting with `prefix`, sorted."""
        ...

    def delete(self, key: str) -> None: ...

    def location(self, key: str = "") -> str:
        """Where `key` lives, for people to read; with no key, where the store itself is."""
        ...


def check_key(key: str) -> PurePosixPath:
    p = PurePosixPath(key)
    if not key or p.is_absolute() or ".." in p.parts or "\\" in key or (p.parts and ":" in p.parts[0]):
        raise ValueError(f"invalid file store key `{key}`")
    return p


class LocalFileStore:
    """A directory on disk."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root.joinpath(*check_key(key).parts)

    def put(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except (FileNotFoundError, IsADirectoryError) as exc:
            raise KeyError(key) from exc

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str = "") -> list[str]:
        if not self.root.is_dir():
            return []
        keys = (p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix))

    def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)
        parent = path.parent
        while parent != self.root and self.root in parent.parents:
            try:
                parent.rmdir()  # only removes empty folders
            except OSError:
                break
            parent = parent.parent

    def location(self, key: str = "") -> str:
        return str(self._path(key)) if key else str(self.root)


FileStoreFactory = Callable[["object", str, str], FileStore]  # (app, user_id, area) -> FileStore


def local_file_store(app, user_id: str, area: str) -> FileStore:
    """The default: `output` -> RB_OUTPUT_ROOT/<user>, `backups` -> RB_BACKUP_ROOT/<user>."""
    if area == "output":
        return LocalFileStore(app.settings.user_output(user_id))
    if area == "backups":
        return LocalFileStore(app.settings.user_backups(user_id))
    raise ValueError(f"unknown file store area `{area}`")
