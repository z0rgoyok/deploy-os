from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


class CatalogError(ValueError):
    """CatalogError сообщает о нарушении правил базы до чтения значений."""


@dataclass(frozen=True)
class Replica:
    uri: str
    purpose: str
    writable: bool = False


@dataclass(frozen=True)
class Secret:
    id: str
    project: str
    lifecycle: str
    owner: str
    sops_path: Path
    payload_format: str
    rotation_days: int
    consumers: tuple[str, ...]
    replicas: tuple[Replica, ...]


@dataclass(frozen=True)
class Project:
    id: str
    status: str
    note: str = ""


@dataclass(frozen=True)
class Mirror:
    id: str
    adapter: str
    required: bool
    options: dict[str, Any]


@dataclass(frozen=True)
class Catalog:
    root: Path
    projects: tuple[Project, ...]
    secrets: tuple[Secret, ...]
    mirrors: tuple[Mirror, ...]

    def secret(self, secret_id: str) -> Secret:
        for item in self.secrets:
            if item.id == secret_id:
                return item
        raise CatalogError(f"Секрет {secret_id!r} отсутствует в каталоге")
