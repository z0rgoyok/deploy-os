from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

from .model import Secret


class SopsError(RuntimeError):
    """SopsError не включает stdout/stderr, чтобы ошибка не раскрыла значение."""


class SopsStore:
    def __init__(self, repository_root: Path) -> None:
        self.root = repository_root.resolve()

    def _environment(self) -> dict[str, str]:
        # SOPS owns credential discovery. The toolkit adds no operator selector.
        return dict(os.environ)

    @staticmethod
    def _encode(secret: Secret, value: bytes) -> bytes:
        if secret.payload_format == "binary":
            payload = base64.b64encode(value).decode("ascii")
            encoding = "base64"
        else:
            try:
                payload = value.decode("utf-8")
            except UnicodeDecodeError as error:
                raise SopsError(
                    f"Секрет {secret.id!r} объявлен текстовым, но содержит не UTF-8"
                ) from error
            encoding = "utf-8"
        return (
            json.dumps(
                {"encoding": encoding, "payload": payload},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def _decode(secret: Secret, document: bytes) -> bytes:
        try:
            decoded = json.loads(document)
            encoding = decoded["encoding"]
            payload = decoded["payload"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise SopsError(f"SOPS-контейнер {secret.id!r} имеет неверную схему") from error
        if encoding == "base64":
            try:
                return base64.b64decode(payload, validate=True)
            except (ValueError, TypeError) as error:
                raise SopsError(f"SOPS-контейнер {secret.id!r} повреждён") from error
        if encoding == "utf-8" and isinstance(payload, str):
            return payload.encode("utf-8")
        raise SopsError(f"SOPS-контейнер {secret.id!r} использует неизвестную кодировку")

    def probe(self, secret: Secret) -> bool:
        return secret.sops_path.is_file()

    def read(self, secret: Secret) -> bytes:
        completed = subprocess.run(
            ["sops", "--decrypt", str(secret.sops_path)],
            cwd=self.root,
            env=self._environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise SopsError(f"Не удалось расшифровать SOPS-копию {secret.id!r}")
        return self._decode(secret, completed.stdout)

    def write(self, secret: Secret, value: bytes) -> None:
        plaintext = self._encode(secret, value)
        completed = subprocess.run(
            [
                "sops",
                "--encrypt",
                "--input-type",
                "json",
                "--output-type",
                "json",
                "--filename-override",
                str(secret.sops_path.relative_to(self.root)),
                "/dev/stdin",
            ],
            cwd=self.root,
            input=plaintext,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if completed.returncode != 0:
            raise SopsError(f"Не удалось зашифровать SOPS-копию {secret.id!r}")
        secret.sops_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{secret.sops_path.name}.secretsctl-",
            dir=secret.sops_path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(completed.stdout)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(secret.sops_path)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
