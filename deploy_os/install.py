"""Pinned, isolated installation with an explicit origin and rollback receipt."""
from __future__ import annotations

import argparse
import fcntl
import functools
import stat
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .public_safety import check_snapshot, git, revision_files, SafetyError


class InstallError(ValueError):
    pass


def locked(function):
    @functools.wraps(function)
    def wrapper(prefix: Path, *args, **kwargs):
        prefix = prefix.resolve()
        prefix.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(prefix / ".operation.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise InstallError("Invalid installation lock")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise InstallError("Installation busy; wait for the current operation") from error
            return function(prefix, *args, **kwargs)
        finally:
            os.close(descriptor)
    return wrapper


def command(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if result.returncode:
        raise InstallError("Installation command failed; inspect repository availability and selected revision before retrying")
    return result.stdout.strip()


def full_revision(value: object) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise InstallError("Receipt/revision must contain a full commit; verify the installation receipt")
    return value


def receipt_for(prefix: Path, origin: str) -> dict:
    path = prefix / "install.json"
    if path.is_symlink():
        raise InstallError("Receipt must be a regular file")
    try:
        receipt = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise InstallError("Installation receipt unavailable; verify prefix before continuing") from error
    if not isinstance(receipt, dict) or receipt.get("schema_version") != 1 or receipt.get("origin") != origin:
        raise InstallError("Receipt origin/schema mismatch; verify the expected origin")
    full_revision(receipt.get("current"))
    if receipt.get("previous") is not None:
        full_revision(receipt["previous"])
    return receipt


def verify_release(path: Path, revision: str, origin: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise InstallError("Release checkout unavailable; verify the installation receipt")
    if command("git", "rev-parse", "HEAD", cwd=path) != revision:
        raise InstallError("Checkout revision mismatch; preserve and inspect local changes")
    if command("git", "remote", "get-url", "origin", cwd=path) != origin:
        raise InstallError("Checkout origin mismatch; verify the expected repository")
    if command("git", "status", "--porcelain", "--untracked-files=all", "--ignored", cwd=path):
        raise InstallError("Checkout is dirty; preserve local changes before update")
    snapshot = revision_files(path, revision)
    required = {"bin/vps-ops", "bin/secretsctl", ".codex/skills/deploy-os/SKILL.md", ".codex/skills/deploy-os-creator/SKILL.md"}
    if not required.issubset(snapshot):
        raise InstallError("Incomplete toolkit release; verify the selected revision")
    failures = check_snapshot(snapshot)
    if failures:
        raise InstallError("Release safety check failed; run check-public-tree on the selected revision")


def links(prefix: Path) -> dict[Path, Path]:
    current = prefix / "current"
    home = prefix / "home"
    return {
        home / ".codex/skills/deploy-os": current / ".codex/skills/deploy-os",
        home / ".codex/skills/deploy-os-creator": current / ".codex/skills/deploy-os-creator",
        home / ".local/bin/vps-ops": current / "bin/vps-ops",
        home / ".local/bin/secretsctl": current / "bin/secretsctl",
    }


def preflight(prefix: Path) -> None:
    for target, source in links(prefix).items():
        parent = target.parent
        while parent != prefix:
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                raise InstallError("Installation parent collision; choose an isolated prefix")
            parent = parent.parent
        if target.is_symlink():
            if os.readlink(target) == str(source):
                continue
        elif not target.exists():
            continue
        raise InstallError("Installation target collision; choose an isolated prefix")
    for name in ("releases",):
        target = prefix / name
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise InstallError("Installation directory collision; choose an isolated prefix")


def activate(prefix: Path, receipt: dict, old_receipt: dict | None) -> None:
    current = prefix / "current"
    temporary = prefix / ".current-new"
    data = prefix / ".install-new.json"
    transaction = prefix / "activation.json"
    for path in (temporary, data, transaction):
        if path.exists() or path.is_symlink():
            raise InstallError("Activation pending; run recover before retrying")
    # The journal precedes every link mutation. install.json is the committed
    # authority; recover aligns current with it after interruption.
    transaction.write_text(json.dumps({"schema_version": 1, "old": old_receipt, "new": receipt}) + "\n")
    temporary.symlink_to(prefix / "releases" / receipt["current"], target_is_directory=True)
    for target, source in links(prefix).items():
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.is_symlink():
            target.symlink_to(source)
    data.write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    temporary.replace(current)
    data.replace(prefix / "install.json")
    transaction.unlink()


@locked
def recover(prefix: Path, origin: str) -> dict | None:
    prefix = prefix.resolve()
    preflight(prefix)
    journal = prefix / "activation.json"
    if journal.is_symlink():
        raise InstallError("Invalid activation journal")
    try:
        transaction = json.loads(journal.read_text())
        if not isinstance(transaction, dict) or transaction.get("schema_version") != 1:
            raise ValueError
        old, new = transaction["old"], transaction["new"]
        if not isinstance(new, dict) or (old is not None and not isinstance(old, dict)):
            raise ValueError
        for value in (old, new):
            if value is not None:
                if value.get("schema_version") != 1 or value.get("origin") != origin:
                    raise ValueError
                revision = full_revision(value.get("current"))
                if value.get("previous") is not None:
                    full_revision(value["previous"])
                verify_release(prefix / "releases" / revision, revision, origin)
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise InstallError("Invalid activation journal; inspect the installation before recovery") from error
    receipt_path = prefix / "install.json"
    committed = receipt_for(prefix, origin) if receipt_path.exists() else None
    if committed not in (old, new):
        raise InstallError("Receipt differs from activation journal; inspect before recovery")
    current = prefix / "current"
    if current.exists() and not current.is_symlink():
        raise InstallError("Current path collision; inspect before recovery")
    if current.is_symlink() and current.resolve() not in {
        prefix / "releases" / value["current"] for value in (old, new) if value is not None
    }:
        raise InstallError("Current link is outside activation journal; inspect before recovery")
    temporary = prefix / ".current-new"
    data = prefix / ".install-new.json"
    # Staging artifacts are task-owned only when they match journal content.
    if temporary.is_symlink():
        if temporary.resolve() != prefix / "releases" / new["current"]:
            raise InstallError("Activation staging link mismatch")
        temporary.unlink()
    elif temporary.exists():
        raise InstallError("Activation staging path collision")
    if data.exists() or data.is_symlink():
        if data.is_symlink() or json.loads(data.read_text()) != new:
            raise InstallError("Activation staging receipt mismatch")
        data.unlink()
    if committed is None:
        current.unlink(missing_ok=True)
        for target, source in links(prefix).items():
            if target.is_symlink() and os.readlink(target) == str(source):
                target.unlink()
    else:
        temporary.symlink_to(prefix / "releases" / committed["current"], target_is_directory=True)
        temporary.replace(current)
    journal.unlink()
    return committed


@locked
def install(prefix: Path, origin: str, revision: str, updating: bool) -> dict:
    prefix = prefix.resolve()
    revision = full_revision(revision)
    prefix.mkdir(parents=True, exist_ok=True)
    preflight(prefix)
    for name in ("activation.json", ".current-new", ".install-new.json"):
        if (prefix / name).exists() or (prefix / name).is_symlink():
            raise InstallError("Activation pending; run recover before retrying")
    previous = None
    old_receipt = None
    receipt_path = prefix / "install.json"
    if receipt_path.exists() or receipt_path.is_symlink():
        receipt = receipt_for(prefix, origin)
        old_receipt = receipt
        verify_release(prefix / "releases" / receipt["current"], receipt["current"], origin)
        current = prefix / "current"
        if not current.is_symlink() or current.resolve() != prefix / "releases" / receipt["current"]:
            raise InstallError("Current link differs from receipt; inspect prior installation outcome")
        if receipt["current"] == revision:
            return receipt
        if not updating:
            raise InstallError("Prefix already installed; use update with an explicit revision")
        previous = receipt["current"]
    elif updating or (prefix / "current").exists() or (prefix / "current").is_symlink():
        raise InstallError("Update requires an intact receipt and current link")
    releases = prefix / "releases"
    releases.mkdir(exist_ok=True)
    target = releases / revision
    if target.exists() or target.is_symlink():
        verify_release(target, revision, origin)
    else:
        with tempfile.TemporaryDirectory(prefix=".prepare-", dir=prefix) as directory:
            prepared = Path(directory) / "checkout"
            command("git", "clone", "--no-local", "--no-checkout", "--", origin, str(prepared))
            command("git", "checkout", "--detach", revision, cwd=prepared)
            verify_release(prepared, revision, origin)
            # Audit all fetched history with the trusted scanner.
            for commit in git(prepared, "rev-list", "--all").decode().splitlines():
                if check_snapshot(revision_files(prepared, commit)):
                    raise InstallError("Repository history safety check failed; review the release source")
            prepared.rename(target)
    receipt = {"schema_version": 1, "origin": origin, "current": revision, "previous": previous}
    activate(prefix, receipt, old_receipt)
    return receipt


@locked
def rollback(prefix: Path, origin: str) -> dict:
    prefix = prefix.resolve()
    preflight(prefix)
    if (prefix / "activation.json").exists():
        raise InstallError("Activation pending; run recover before retrying")
    receipt = receipt_for(prefix, origin)
    previous = receipt.get("previous")
    if not previous:
        raise InstallError("No previous revision recorded; inspect the release receipt")
    for revision in (receipt["current"], previous):
        verify_release(prefix / "releases" / revision, revision, origin)
    current = prefix / "current"
    if not current.is_symlink() or current.resolve() != prefix / "releases" / receipt["current"]:
        raise InstallError("Current link differs from receipt; inspect prior installation outcome")
    result = {**receipt, "current": previous, "previous": receipt["current"]}
    activate(prefix, result, receipt)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "update", "rollback", "recover"))
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--expected-origin", required=True)
    parser.add_argument("--commit")
    args = parser.parse_args(argv)
    if args.action in {"install", "update"} and not args.commit:
        parser.error("install/update requires --commit FULL_COMMIT")
    try:
        prefix = args.prefix.expanduser().resolve()
        if args.action == "recover":
            receipt = recover(prefix, args.expected_origin)
        elif args.action == "rollback":
            receipt = rollback(prefix, args.expected_origin)
        else:
            receipt = install(prefix, args.expected_origin, args.commit, args.action == "update")
        print(f"{args.action}: {receipt['current'] if receipt else 'empty'}; isolated prefix ready")
        return 0
    except (InstallError, SafetyError, OSError, ValueError):
        # Git or filesystem diagnostics may contain credentials in a URL.
        print("Installation failed; verify origin, full commit, clean checkout, receipt and target collisions; use recover for a pending activation", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
