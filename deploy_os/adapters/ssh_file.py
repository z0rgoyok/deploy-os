from __future__ import annotations

import shlex
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from .base import AdapterError, ReplicaAdapter
from .ssh_transport import connection_for


class SSHFileAdapter(ReplicaAdapter):
    def __init__(self, repository_root: Path) -> None:
        self.repository_root = repository_root.resolve()

    def _target(self, uri: str) -> tuple[list[str], str, bool]:
        parsed = urlparse(uri)
        if parsed.scheme != "ssh-file" or not parsed.netloc:
            raise AdapterError("ssh-file URI требует логическое имя хоста")
        connection = connection_for(self.repository_root, parsed.netloc)
        remote_path = unquote(parsed.path)
        pure_path = PurePosixPath(remote_path)
        if not pure_path.is_absolute() or ".." in pure_path.parts:
            raise AdapterError("ssh-file требует абсолютный путь без переходов ..")
        return list(connection.command), remote_path, connection.is_root

    @staticmethod
    def _remote_reader(remote_path: str, is_root: bool, probe: bool = False) -> str:
        path = shlex.quote(remote_path)
        action = f"test -f {path}" if probe else f"cat -- {path}"
        if is_root:
            return action
        return f"sudo -n sh -c {shlex.quote(action)}"

    def probe(self, uri: str) -> bool:
        command, remote_path, is_root = self._target(uri)
        completed = subprocess.run(
            [*command, self._remote_reader(remote_path, is_root, probe=True)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return completed.returncode == 0

    def read(self, uri: str) -> bytes:
        command, remote_path, is_root = self._target(uri)
        completed = subprocess.run(
            [*command, self._remote_reader(remote_path, is_root)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise AdapterError(f"Не удалось прочитать серверную реплику {uri}")
        return completed.stdout

    @staticmethod
    def _remote_writer(remote_path: str, is_root: bool) -> str:
        target = shlex.quote(remote_path)
        script = (
            "set -eu; "
            f"target={target}; "
            'test -f "$target"; '
            'directory=$(dirname -- "$target"); '
            "umask 077; "
            'temporary=$(mktemp "$directory/.secretsctl.XXXXXXXX"); '
            "trap 'rm -f -- \"$temporary\"' EXIT HUP INT TERM; "
            'cp -p -- "$target" "$temporary"; '
            'cat > "$temporary"; '
            'mv -f -- "$temporary" "$target"; '
            "trap - EXIT HUP INT TERM"
        )
        if is_root:
            return f"sh -c {shlex.quote(script)}"
        return f"sudo -n sh -c {shlex.quote(script)}"

    def write(self, uri: str, value: bytes) -> None:
        command, remote_path, is_root = self._target(uri)
        completed = subprocess.run(
            [*command, self._remote_writer(remote_path, is_root)],
            input=value,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise AdapterError(f"Не удалось обновить серверную реплику {uri}")
