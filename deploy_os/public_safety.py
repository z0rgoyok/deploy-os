"""Publication boundary; diagnostics contain rule, path and line only."""
from __future__ import annotations

import argparse
import ipaddress
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse


IGNORED = {".git", ".automate", "__pycache__", ".venv", "var"}
ALLOWED_URL_HOSTS = {
    "github.com", "www.apache.org", "apache.org", "getsops.io",
    "age-encryption.org", "docs.python.org", "example.com", "example.invalid",
}
PATTERNS = {
    "private-key": re.compile(r"-----BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "age-identity": re.compile("AGE-" + "SECRET-KEY-1"),
    "github-token": re.compile(r"\bgh[opusr]_[A-Za-z0-9_]{20,}\b"),
    "slack-token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
    "google-token": re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    "workstation-path": re.compile("/" + r"(?:Users|home)/[^\s'\"]+"),
    "credential-url": re.compile(r"https?://[^\s/@]+:[^\s/@]+@"),
}
IP = re.compile(r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])")
URL = re.compile(r"https?://[^\s<>\"')]+")
DOC_NETWORKS = tuple(ipaddress.ip_network(item) for item in (
    "192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "127.0.0.0/8",
))


class SafetyError(ValueError):
    pass


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode:
        raise SafetyError("git-operation: repository:0")
    return result.stdout


def manifest(data: bytes) -> set[str]:
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise SafetyError("invalid-allowlist: public-files.txt:0") from error
    paths = [line.strip() for line in lines if line.strip() and not line.startswith("#")]
    if len(paths) != len(set(paths)):
        raise SafetyError("duplicate-allowlist: public-files.txt:0")
    for value in paths:
        parts = PurePosixPath(value).parts
        if not parts or value.startswith("/") or ".." in parts or "\\" in value or any(x in value for x in "*?[]"):
            raise SafetyError("unsafe-allowlist: public-files.txt:0")
    return set(paths)


def content_violations(path: str, data: bytes) -> list[str]:
    failures = []
    pure = PurePosixPath(path)
    if any(part in {"vault", "recovery", "hosts"} for part in pure.parts):
        failures.append(f"private-state-path: {path}:0")
    if pure.name in {".env", ".sops.yaml", ".sops.yml"} or pure.suffix in {".pem", ".age", ".agekey", ".bundle", ".log"}:
        failures.append(f"private-file-type: {path}:0")
    if path in {"catalog.toml", "docs/inventory.md", "docs/projects.md"}:
        failures.append(f"live-registry: {path}:0")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return failures + [f"binary-file: {path}:0"]
    for number, line in enumerate(text.splitlines(), 1):
        for rule, pattern in PATTERNS.items():
            if pattern.search(line):
                failures.append(f"{rule}: {path}:{number}")
        for match in IP.finditer(line):
            try:
                address = ipaddress.ip_address(match.group())
            except ValueError:
                continue
            if not any(address in network for network in DOC_NETWORKS):
                failures.append(f"non-example-address: {path}:{number}")
        for match in URL.finditer(line):
            if urlparse(match.group()).hostname not in ALLOWED_URL_HOSTS:
                failures.append(f"unreviewed-url-host: {path}:{number}")
    return failures


def check_snapshot(files: dict[str, tuple[bytes, str]], allowed: set[str] | None = None) -> list[str]:
    if "public-files.txt" not in files:
        return ["missing-allowlist: public-files.txt:0"]
    expected = allowed if allowed is not None else manifest(files["public-files.txt"][0])
    failures = [f"unexpected-file: {path}:0" for path in sorted(files.keys() - expected)]
    failures += [f"missing-file: {path}:0" for path in sorted(expected - files.keys())]
    for path, (data, mode) in files.items():
        if mode not in {"100644", "100755"}:
            failures.append(f"non-regular-file: {path}:0")
        failures.extend(content_violations(path, data))
    return failures


def revision_files(root: Path, revision: str) -> dict[str, tuple[bytes, str]]:
    files = {}
    for entry in git(root, "ls-tree", "-r", "-z", revision).split(b"\0"):
        if not entry:
            continue
        metadata, encoded = entry.split(b"\t", 1)
        mode, kind, object_id = metadata.decode().split()
        path = encoded.decode("utf-8")
        data = git(root, "cat-file", "blob", object_id) if kind == "blob" else b""
        files[path] = (data, mode)
    return files


def working_files(root: Path) -> dict[str, tuple[bytes, str]]:
    files = {}
    def visit(folder: Path) -> None:
        for entry in folder.iterdir():
            if entry.name in IGNORED and entry.parent == root:
                continue
            if entry.name == "__pycache__" or entry.name == ".DS_Store":
                continue
            path = entry.relative_to(root).as_posix()
            if entry.is_symlink():
                files[path] = (b"", "120000")
            elif entry.is_dir():
                visit(entry)
            else:
                files[path] = (entry.read_bytes(), "100755" if entry.stat().st_mode & 0o111 else "100644")
    visit(root)
    return files


def staged_files(root: Path) -> dict[str, tuple[bytes, str]]:
    files = {}
    for entry in git(root, "ls-files", "--stage", "-z").split(b"\0"):
        if entry:
            metadata, encoded = entry.split(b"\t", 1)
            mode, object_id, stage = metadata.decode().split()
            if stage != "0":
                raise SafetyError("unmerged-index: repository:0")
            files[encoded.decode()] = (git(root, "cat-file", "blob", object_id), mode)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--revision", help="full immutable commit to check")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        failures = []
        if args.revision:
            if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
                raise SafetyError("invalid-full-revision: repository:0")
            failures += check_snapshot(revision_files(root, args.revision))
        else:
            failures += check_snapshot(working_files(root))
            if (root / ".git").exists():
                staged = staged_files(root)
                if staged:
                    failures += check_snapshot(staged)
        if args.history:
            revisions = git(root, "rev-list", "--all").decode().splitlines()
            if args.revision and args.revision not in revisions:
                revisions.append(args.revision)
            for revision in revisions:
                failures += check_snapshot(revision_files(root, revision))
                failures += content_violations("git-commit-metadata", git(root, "cat-file", "commit", revision))
            refs = git(root, "for-each-ref", "--format=%(refname)").decode()
            if any(line.startswith("refs/tags/") for line in refs.splitlines()):
                failures.append("unaudited-tag-reference: repository:0")
            # Ref content is checked without echoing a potentially sensitive name.
            if content_violations("git-refs", refs.encode()):
                failures.append("unsafe-git-reference: repository:0")
        if failures:
            for failure in sorted(set(failures)):
                print(failure, file=sys.stderr)
            return 1
    except (SafetyError, OSError, UnicodeError) as error:
        print(str(error) if isinstance(error, SafetyError) else "scan-unavailable: repository:0", file=sys.stderr)
        return 1
    print("public-tree: ok")
    return 0
