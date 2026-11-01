#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path


PROJECT_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
TOOLKIT_ROOT = Path(__file__).resolve().parents[4]
sys.dont_write_bytecode = True
sys.path.insert(0, str(TOOLKIT_ROOT))
from deploy_os.context import state_root


def git_root(start: Path) -> Path | None:
    completed = subprocess.run(
        ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip()).resolve()


def find_marker(start: Path, boundary: Path | None) -> Path | None:
    if boundary is None:
        return None
    current = start.resolve()
    while True:
        candidate = current / ".ops-project"
        if candidate.is_file():
            return candidate
        if boundary is not None and current == boundary:
            return None
        parent = current.parent
        if parent == current:
            return None
        current = parent


def read_marker(marker: Path | None) -> tuple[str | None, str | None, list[str]]:
    if marker is None:
        return None, None, []
    fields: dict[str, str] = {}
    errors: list[str] = []
    for raw_line in marker.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            errors.append("marker contains a line without '='")
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key in fields:
            errors.append(f"marker contains duplicate {key}")
            continue
        fields[key] = value.strip()
    project_id = fields.get("project")
    contract_version = fields.get("contract_version")
    if project_id is None:
        errors.append("marker does not define project")
    elif not PROJECT_ID.fullmatch(project_id):
        errors.append("project must use kebab-case")
    if contract_version != "1":
        errors.append("unsupported contract_version")
    return project_id, contract_version, errors


def catalog_context(root: Path, project_id: str | None, repo_name: str) -> dict[str, object]:
    catalog_path = root / "catalog.toml"
    result: dict[str, object] = {
        "available": catalog_path.is_file(),
        "project_registered": False,
        "secret_count": 0,
        "workspace_replica_count": 0,
    }
    if not catalog_path.is_file() or project_id is None:
        return result
    with catalog_path.open("rb") as stream:
        catalog = tomllib.load(stream)
    result["project_registered"] = any(
        item.get("id") == project_id for item in catalog.get("projects", [])
    )
    secrets = [
        item for item in catalog.get("secrets", []) if item.get("project") == project_id
    ]
    result["secret_count"] = len(secrets)
    prefix = f"workspace-file://{repo_name}/"
    result["workspace_replica_count"] = sum(
        1
        for secret in secrets
        for replica in secret.get("replicas", [])
        if str(replica.get("uri", "")).startswith(prefix)
    )
    return result


def resolve_context(start: Path, explicit_project: str | None = None) -> dict[str, object]:
    start = start.resolve()
    repository_root = git_root(start)
    marker = find_marker(start, repository_root)
    source_root = marker.parent if marker is not None else repository_root
    project_id, contract_version, marker_errors = read_marker(marker)
    if explicit_project is not None:
        if not PROJECT_ID.fullmatch(explicit_project):
            marker_errors.append("explicit project must use kebab-case")
        elif project_id is not None and project_id != explicit_project:
            marker_errors.append("explicit project does not match marker")
        elif project_id is None:
            project_id = explicit_project

    deploy_os_root = state_root()
    control_plane = repository_root == deploy_os_root
    repo_name = repository_root.name if repository_root is not None else start.name

    ops_registry = False
    profile_available = False
    if project_id is not None and deploy_os_root.is_dir():
        projects_doc = deploy_os_root / "docs" / "projects.md"
        if projects_doc.is_file():
            ops_registry = f"`{project_id}`" in projects_doc.read_text(encoding="utf-8")
        profile_available = (deploy_os_root / "projects" / project_id).is_dir()

    catalog = catalog_context(deploy_os_root, project_id, repo_name)
    if control_plane:
        status = "control-plane"
    elif marker is None and explicit_project is None:
        status = "unregistered"
    elif marker_errors:
        status = "invalid-marker"
    elif ops_registry or profile_available or catalog["project_registered"]:
        status = "connected"
    else:
        status = "marker-only"

    return {
        "status": status,
        "start": str(start),
        "source_root": str(source_root) if source_root is not None else None,
        "repository_name": repo_name,
        "marker": str(marker) if marker is not None else None,
        "project_id": project_id,
        "contract_version": contract_version,
        "marker_errors": marker_errors,
        "deploy_os_root": str(deploy_os_root),
        "deploy_os_available": (deploy_os_root / "AGENTS.md").is_file(),
        "ops_registry": ops_registry,
        "deploy_profile": profile_available,
        "secrets": catalog,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default=os.getcwd())
    parser.add_argument("--project")
    parser.add_argument(
        "--field",
        choices=("status", "source_root", "project_id", "deploy_os_root"),
    )
    arguments = parser.parse_args()

    try:
        payload = resolve_context(Path(arguments.start), arguments.project)
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 2
    errors = payload["marker_errors"]
    if errors:
        print("; ".join(str(item) for item in errors), file=sys.stderr)
        return 2
    if arguments.field:
        value = payload[arguments.field]
        if value is None:
            print(f"{arguments.field} is unavailable", file=sys.stderr)
            return 2
        print(value)
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
