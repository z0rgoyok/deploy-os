from __future__ import annotations

import tomllib
import re
from pathlib import Path
from urllib.parse import urlparse

from .model import Catalog, CatalogError, Mirror, Project, Replica, Secret


SUPPORTED_REPLICA_SCHEMES = {"file", "workspace-file", "ssh-file"}
SUPPORTED_PAYLOAD_FORMATS = {"binary", "dotenv", "text"}
SUPPORTED_LIFECYCLES = {"active", "legacy"}


def _unique(values: list[str], label: str) -> None:
    duplicates = sorted({value for value in values if values.count(value) > 1})
    if duplicates:
        raise CatalogError(f"Повторяющиеся {label}: {', '.join(duplicates)}")


def _safe_sops_path(root: Path, raw_path: str) -> Path:
    relative = Path(raw_path)
    if relative.is_absolute() or ".." in relative.parts:
        raise CatalogError(f"sops_path должен быть относительным: {raw_path}")
    resolved = (root / relative).resolve()
    vault_root = (root / "vault").resolve()
    if not resolved.is_relative_to(vault_root):
        raise CatalogError(f"sops_path должен находиться в vault/: {raw_path}")
    if resolved.suffix != ".json":
        raise CatalogError(f"SOPS-контейнер должен иметь расширение .json: {raw_path}")
    return resolved


def _boolean(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise CatalogError("Catalog boolean field has an invalid type")
    return value


def load_catalog(root: Path) -> Catalog:
    root = root.resolve()
    with (root / "catalog.toml").open("rb") as stream:
        raw = tomllib.load(stream)

    if not isinstance(raw, dict) or type(raw.get("schema_version")) is not int or raw.get("schema_version") != 1:
        raise CatalogError("Поддерживается только schema_version = 1")

    for collection in ("projects", "secrets", "mirrors"):
        if not isinstance(raw.get(collection, []), list) or not all(isinstance(item, dict) for item in raw.get(collection, [])):
            raise CatalogError("Catalog section must be a list of tables")
    for item in raw.get("projects", []):
        if not isinstance(item.get("id"), str) or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", item["id"]) is None or not isinstance(item.get("status"), str):
            raise CatalogError("Invalid project metadata")
    projects = tuple(
        Project(
            id=item["id"],
            status=item["status"],
            note=item.get("note", ""),
        )
        for item in raw.get("projects", [])
    )
    _unique([item.id for item in projects], "идентификаторы проектов")
    project_ids = {item.id for item in projects}

    secrets: list[Secret] = []
    for item in raw.get("secrets", []):
        for field in ("id", "project", "owner", "sops_path"):
            if not isinstance(item.get(field), str) or not item[field]:
                raise CatalogError("Invalid secret metadata")
        if type(item.get("rotation_days")) is not int or item["rotation_days"] <= 0:
            raise CatalogError("rotation_days must be a positive integer")
        if not isinstance(item.get("consumers", []), list) or not all(isinstance(value, str) for value in item.get("consumers", [])):
            raise CatalogError("consumers must be a string list")
        if not isinstance(item.get("replicas", []), list):
            raise CatalogError("replicas must be a list")
        for replica in item.get("replicas", []):
            if not isinstance(replica, dict) or not all(isinstance(replica.get(field), str) and replica[field] for field in ("uri", "purpose")):
                raise CatalogError("Invalid replica metadata")
        project = item["project"]
        if project not in project_ids:
            raise CatalogError(
                f"Секрет {item['id']!r} ссылается на неизвестный проект {project!r}"
            )
        payload_format = item.get("payload_format", "binary")
        if payload_format not in SUPPORTED_PAYLOAD_FORMATS:
            raise CatalogError(
                f"Секрет {item['id']!r} использует неизвестный payload_format"
            )
        lifecycle = item.get("lifecycle", "active")
        if lifecycle not in SUPPORTED_LIFECYCLES:
            raise CatalogError(
                f"Секрет {item['id']!r} использует неизвестный lifecycle"
            )
        replicas: list[Replica] = []
        for replica in item.get("replicas", []):
            uri = replica["uri"]
            scheme = urlparse(uri).scheme
            if scheme not in SUPPORTED_REPLICA_SCHEMES:
                raise CatalogError(
                    f"Реплика {uri!r} использует неподдерживаемую схему {scheme!r}"
                )
            replicas.append(
                Replica(
                    uri=uri,
                    purpose=replica["purpose"],
                    writable=_boolean(replica.get("writable", False), "writable"),
                )
            )
        secrets.append(
            Secret(
                id=item["id"],
                project=project,
                lifecycle=lifecycle,
                owner=item["owner"],
                sops_path=_safe_sops_path(root, item["sops_path"]),
                payload_format=payload_format,
                rotation_days=int(item["rotation_days"]),
                consumers=tuple(item.get("consumers", [])),
                replicas=tuple(replicas),
            )
        )
    _unique([item.id for item in secrets], "идентификаторы секретов")
    _unique([str(item.sops_path) for item in secrets], "пути SOPS-контейнеров")

    for item in raw.get("mirrors", []):
        if not all(isinstance(item.get(field), str) and item[field] for field in ("id", "adapter")):
            raise CatalogError("Invalid mirror metadata")
        for key, value in item.items():
            if key != "required" and not isinstance(value, str):
                raise CatalogError("Mirror options must be strings")
    mirrors = tuple(
        Mirror(
            id=item["id"],
            adapter=item["adapter"],
            required=_boolean(item.get("required", False), "required"),
            options={
                key: value
                for key, value in item.items()
                if key not in {"id", "adapter", "required"}
            },
        )
        for item in raw.get("mirrors", [])
    )
    _unique([item.id for item in mirrors], "идентификаторы зеркал")
    return Catalog(root=root, projects=projects, secrets=tuple(secrets), mirrors=mirrors)
