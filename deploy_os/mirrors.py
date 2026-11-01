from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .model import Catalog, Mirror


class MirrorError(RuntimeError):
    """MirrorError сообщает об отказе резервирования без содержимого базы."""


@dataclass(frozen=True)
class MirrorStatus:
    id: str
    required: bool
    healthy: bool
    detail: str


def _mirror(catalog: Catalog, mirror_id: str) -> Mirror:
    for item in catalog.mirrors:
        if item.id == mirror_id:
            return item
    raise MirrorError(f"Зеркало {mirror_id!r} отсутствует в каталоге")


def _git_stdout(root: Path, *arguments: str) -> str | None:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _check_git_mirror(catalog: Catalog, mirror: Mirror, head: str) -> MirrorStatus:
    remote = str(mirror.options.get("remote", ""))
    branch = str(mirror.options.get("branch", ""))
    if not remote or not branch:
        return MirrorStatus(mirror.id, mirror.required, False, "remote/branch не заданы")
    remote_url = _git_stdout(catalog.root, "remote", "get-url", remote)
    if not remote_url:
        return MirrorStatus(mirror.id, mirror.required, False, "remote отсутствует")
    advertised = _git_stdout(
        catalog.root,
        "ls-remote",
        "--exit-code",
        remote,
        f"refs/heads/{branch}",
    )
    remote_head = advertised.split(maxsplit=1)[0] if advertised else ""
    if remote_head != head:
        return MirrorStatus(
            mirror.id,
            mirror.required,
            False,
            "remote branch не содержит текущий commit",
        )

    visibility = str(mirror.options.get("visibility", ""))
    if visibility:
        if visibility not in {"private", "public"}:
            return MirrorStatus(
                mirror.id, mirror.required, False, "visibility имеет неверное значение"
            )
        if str(mirror.options.get("provider", "")) != "github":
            return MirrorStatus(
                mirror.id, mirror.required, False, "provider-check не поддерживается"
            )
        gh = shutil.which("gh")
        if gh is None:
            return MirrorStatus(mirror.id, mirror.required, False, "gh недоступен")
        checked = subprocess.run(
            [gh, "repo", "view", remote_url, "--json", "isPrivate", "--jq", ".isPrivate"],
            cwd=catalog.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        expected = "true" if visibility == "private" else "false"
        if checked.returncode != 0 or checked.stdout.strip() != expected:
            return MirrorStatus(
                mirror.id, mirror.required, False, "visibility не подтверждена"
            )
    return MirrorStatus(mirror.id, mirror.required, True, "current")


def _bundle_target(mirror: Mirror) -> Path | None:
    raw_target = str(mirror.options.get("target", ""))
    target = Path(os.path.expandvars(os.path.expanduser(raw_target)))
    if not raw_target or "$" in str(target) or not target.is_absolute():
        return None
    return target


def _check_bundle_mirror(catalog: Catalog, mirror: Mirror, head: str) -> MirrorStatus:
    branch = str(mirror.options.get("branch", "main"))
    target = _bundle_target(mirror)
    if target is None or not target.is_file():
        return MirrorStatus(mirror.id, mirror.required, False, "bundle отсутствует")
    verified = subprocess.run(
        ["git", "bundle", "verify", str(target)],
        cwd=catalog.root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if verified.returncode != 0:
        return MirrorStatus(mirror.id, mirror.required, False, "bundle повреждён")
    advertised = _git_stdout(catalog.root, "bundle", "list-heads", str(target)) or ""
    bundle_heads = {
        reference: commit
        for line in advertised.splitlines()
        if len((parts := line.split(maxsplit=1))) == 2
        for commit, reference in [parts]
    }
    if bundle_heads.get(f"refs/heads/{branch}") != head:
        return MirrorStatus(
            mirror.id, mirror.required, False, "bundle не содержит текущий commit"
        )
    return MirrorStatus(mirror.id, mirror.required, True, "current")


def check_mirrors(catalog: Catalog) -> tuple[MirrorStatus, ...]:
    head = _git_stdout(catalog.root, "rev-parse", "HEAD")
    if not head:
        raise MirrorError("Не удалось определить текущий Git commit")
    statuses: list[MirrorStatus] = []
    for mirror in catalog.mirrors:
        if mirror.adapter == "git":
            statuses.append(_check_git_mirror(catalog, mirror, head))
        elif mirror.adapter == "git-bundle":
            statuses.append(_check_bundle_mirror(catalog, mirror, head))
        else:
            statuses.append(
                MirrorStatus(
                    mirror.id, mirror.required, False, "неподдерживаемый adapter"
                )
            )
    return tuple(statuses)


def create_git_bundle(catalog: Catalog, mirror_id: str) -> Path:
    mirror = _mirror(catalog, mirror_id)
    if mirror.adapter != "git-bundle":
        raise MirrorError(f"Зеркало {mirror_id!r} не поддерживает создание bundle")
    target = _bundle_target(mirror)
    if target is None:
        raise MirrorError("Путь git-bundle содержит неопределённую переменную окружения")

    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=normal"],
        cwd=catalog.root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if status.returncode != 0 or status.stdout:
        raise MirrorError("Полная копия создаётся только из чистого committed-состояния")

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".new",
        dir=target.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.unlink()
    try:
        with tempfile.TemporaryDirectory(prefix=".bundle-repository.", dir=target.parent) as source_dir:
            clone = Path(source_dir) / "mirror.git"
            copied = subprocess.run(
                ["git", "clone", "--mirror", "--no-hardlinks", str(catalog.root), str(clone)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )
            if copied.returncode != 0:
                raise MirrorError("Git не смог подготовить независимую bundle-копию")
            branch = str(mirror.options.get("branch", "main"))
            head = _git_stdout(catalog.root, "rev-parse", "HEAD")
            if head is None:
                raise MirrorError("Текущий commit недоступен")
            updated = subprocess.run(
                ["git", "--git-dir", str(clone), "update-ref", f"refs/heads/{branch}", head],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )
            if updated.returncode != 0:
                raise MirrorError("Git не смог закрепить текущий commit в bundle-копии")
            created = subprocess.run(
                ["git", "--git-dir", str(clone), "bundle", "create", str(temporary), "--all"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
            )
            if created.returncode != 0:
                raise MirrorError("Git не смог создать полную bundle-копию")
        verified = subprocess.run(
            ["git", "bundle", "verify", str(temporary)],
            cwd=catalog.root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            check=False,
        )
        if verified.returncode != 0:
            raise MirrorError("Проверка созданной bundle-копии не прошла")
        temporary.replace(target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return target
