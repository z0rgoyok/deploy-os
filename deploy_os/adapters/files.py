from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from .base import AdapterError, ReplicaAdapter


class FileAdapter(ReplicaAdapter):
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()
        self.workspace_root = Path(
            os.environ.get(
                "DEPLOY_OS_WORKSPACE_ROOT",
                str(self.repository_root.parent),
            )
        ).resolve()

    def _path(self, uri: str) -> Path:
        parsed = urlparse(uri)
        if parsed.scheme == "file":
            path = Path(unquote(parsed.path)).resolve()
        elif parsed.scheme == "workspace-file":
            if not parsed.netloc:
                raise AdapterError("workspace-file требует имя соседнего репозитория")
            if parsed.netloc in {".", ".."} or "/" in unquote(parsed.netloc):
                raise AdapterError("Invalid workspace repository name")
            base = (self.workspace_root / parsed.netloc).resolve()
            if not base.is_relative_to(self.workspace_root):
                raise AdapterError("Workspace repository escapes workspace root")
            path = (base / unquote(parsed.path).lstrip("/")).resolve()
            if not path.is_relative_to(base):
                raise AdapterError("Путь workspace-file выходит за корень репозитория")
        else:
            raise AdapterError(f"FileAdapter не поддерживает схему {parsed.scheme!r}")
        return path

    def probe(self, uri: str) -> bool:
        return self._path(uri).is_file()

    def read(self, uri: str) -> bytes:
        path = self._path(uri)
        try:
            return path.read_bytes()
        except OSError as error:
            raise AdapterError(f"Не удалось прочитать локальную реплику {uri}") from error

    def write(self, uri: str, value: bytes) -> None:
        path = self._path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.secretsctl-",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            mode = 0o600
            if path.exists():
                metadata = path.stat()
                mode = metadata.st_mode & 0o777
                os.fchown(descriptor, metadata.st_uid, metadata.st_gid)
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
