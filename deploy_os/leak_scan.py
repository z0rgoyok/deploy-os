from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Iterable

from .model import CatalogError


PLAINTEXT_PATTERNS = (
    re.compile(r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"AGE-" + r"SECRET-KEY-1"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
)


def validate_encrypted_container(path: Path, content: bytes) -> None:
    try:
        document = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CatalogError(f"SOPS-контейнер не является корректным JSON: {path}") from error
    payload = document.get("payload")
    if not isinstance(payload, str) or not payload.startswith("ENC["):
        raise CatalogError(f"Открытый payload обнаружен в {path}")
    if not isinstance(document.get("sops"), dict):
        raise CatalogError(f"SOPS metadata отсутствует в {path}")


def _git_paths(root: Path, *arguments: str) -> tuple[Path, ...] | None:
    completed = subprocess.run(
        ["git", "ls-files", "-z", *arguments],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return tuple(
        Path(os.fsdecode(encoded))
        for encoded in completed.stdout.split(b"\0")
        if encoded
    )


def _candidate_documents(root: Path) -> Iterable[tuple[Path, bytes]]:
    tracked = _git_paths(root, "--cached")
    if tracked is None:
        for candidate in root.rglob("*"):
            if candidate.is_file():
                yield candidate.relative_to(root), candidate.read_bytes()
        return

    for relative in tracked:
        staged = subprocess.run(
            ["git", "show", f":{relative.as_posix()}"],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if staged.returncode == 0:
            yield relative, staged.stdout
        working = root / relative
        if working.is_file():
            yield relative, working.read_bytes()

    for relative in _git_paths(root, "--others", "--exclude-standard") or ():
        candidate = root / relative
        if candidate.is_file():
            yield relative, candidate.read_bytes()


def scan_repository(root: Path) -> None:
    violations: set[str] = set()
    for relative, content in _candidate_documents(root):
        if ".git" in relative.parts or "__pycache__" in relative.parts or "var" in relative.parts:
            continue
        if relative.parts and relative.parts[0] == "vault" and relative.name == "README.md":
            continue
        if relative.parts and relative.parts[0] == "vault":
            validate_encrypted_container(relative, content)
            continue
        if relative.name == ".env" or relative.suffix in {".agekey", ".pem"}:
            violations.add(str(relative))
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if any(pattern.search(text) for pattern in PLAINTEXT_PATTERNS):
            violations.add(str(relative))
    if violations:
        raise CatalogError(
            "Возможные открытые секреты вне vault/: " + ", ".join(sorted(violations))
        )
