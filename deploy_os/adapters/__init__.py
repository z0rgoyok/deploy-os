from pathlib import Path
from urllib.parse import urlparse
from .base import AdapterError, ReplicaAdapter
from .files import FileAdapter
from .ssh_file import SSHFileAdapter


def adapter_for(repository_root: Path, uri: str) -> ReplicaAdapter:
    scheme = urlparse(uri).scheme
    if scheme in {"file", "workspace-file"}:
        return FileAdapter(repository_root)
    if scheme == "ssh-file":
        return SSHFileAdapter(repository_root)
    raise AdapterError("Unsupported replica scheme; use file, workspace-file or ssh-file")


__all__ = ["AdapterError", "ReplicaAdapter", "adapter_for"]
