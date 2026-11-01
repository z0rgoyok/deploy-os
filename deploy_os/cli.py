from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .adapters import AdapterError, adapter_for
from .catalog import CatalogError, load_catalog
from .leak_scan import scan_repository
from .mirrors import MirrorError, check_mirrors, create_git_bundle
from .model import Catalog, Secret
from .sops_store import SopsError, SopsStore
from .context import state_root


def _repository_root(explicit: str | None = None) -> Path:
    return state_root(explicit)


def _replica(secret: Secret, uri: str):
    for item in secret.replicas:
        if item.uri == uri:
            return item
    raise CatalogError(
        f"Реплика {uri!r} не зарегистрирована для секрета {secret.id!r}"
    )


def _validate(catalog: Catalog, allowed_missing: frozenset[str] = frozenset()) -> int:
    store = SopsStore(catalog.root)
    scan_repository(catalog.root)
    registered_ids = {item.id for item in catalog.secrets}
    unknown_allowed = sorted(allowed_missing - registered_ids)
    if unknown_allowed:
        raise CatalogError(
            "Исключения validate ссылаются на неизвестные секреты: "
            + ", ".join(unknown_allowed)
        )
    registered_paths = {item.sops_path.resolve() for item in catalog.secrets}
    actual_paths = {path.resolve() for path in (catalog.root / "vault").rglob("*.json")}
    unknown = sorted(actual_paths - registered_paths)
    if unknown:
        relative = ", ".join(str(path.relative_to(catalog.root)) for path in unknown)
        raise CatalogError(f"В vault обнаружены незарегистрированные файлы: {relative}")
    missing = {item.id for item in catalog.secrets if not store.probe(item)}
    required_missing = sorted(missing - allowed_missing)
    if required_missing:
        raise CatalogError(
            "Обязательные SOPS-копии отсутствуют: " + ", ".join(required_missing)
        )
    for secret in catalog.secrets:
        if secret.id not in missing:
            store.read(secret)
    summary = (
        f"проектов {len(catalog.projects)}, секретов {len(catalog.secrets)}, "
        f"зеркал {len(catalog.mirrors)}"
    )
    if missing:
        print(
            "Каталог корректен для bootstrap: "
            f"{summary}; временно отсутствуют {', '.join(sorted(missing))}."
        )
    else:
        print(f"Каталог корректен: {summary}.")
    return 0


def _list(catalog: Catalog) -> int:
    if not catalog.secrets:
        print("Каталог секретов пока пуст.")
        return 0
    for secret in catalog.secrets:
        print(
            f"{secret.id}\t{secret.project}\t{secret.lifecycle}\t"
            f"replicas={len(secret.replicas)}"
        )
    return 0


def _show(catalog: Catalog, secret_id: str) -> int:
    secret = catalog.secret(secret_id)
    print(f"id: {secret.id}")
    print(f"project: {secret.project}")
    print(f"lifecycle: {secret.lifecycle}")
    print(f"owner: {secret.owner}")
    print(f"sops_path: {secret.sops_path.relative_to(catalog.root)}")
    print(f"payload_format: {secret.payload_format}")
    print(f"rotation_days: {secret.rotation_days}")
    print("consumers: " + (", ".join(secret.consumers) or "—"))
    for replica in secret.replicas:
        mode = "writable" if replica.writable else "read-only"
        print(f"replica: {replica.uri} ({replica.purpose}, {mode})")
    return 0


def _import(catalog: Catalog, secret_id: str, uri: str, apply: bool, replace: bool) -> int:
    secret = catalog.secret(secret_id)
    _replica(secret, uri)
    store = SopsStore(catalog.root)
    if store.probe(secret) and not replace:
        raise CatalogError(
            f"SOPS-копия {secret.id!r} уже существует; для осознанной замены нужен --replace"
        )
    if not apply:
        print(f"Проверка: {uri} будет импортирован в {secret.id}; запись не выполнялась.")
        return 0
    adapter = adapter_for(catalog.root, uri)
    value = adapter.read(uri)
    store.write(secret, value)
    if store.read(secret) != value:
        raise SopsError(f"Проверка записанной SOPS-копии {secret.id!r} не прошла")
    print(f"Импортирован {secret.id}; значение не выводилось.")
    return 0


def _check(catalog: Catalog, secret_id: str) -> int:
    secret = catalog.secret(secret_id)
    canonical = SopsStore(catalog.root).read(secret)
    has_mismatch = False
    for replica in secret.replicas:
        adapter = adapter_for(catalog.root, replica.uri)
        try:
            current = adapter.read(replica.uri)
        except AdapterError:
            print(f"unavailable\t{replica.uri}")
            has_mismatch = True
            continue
        if current == canonical:
            print(f"match\t{replica.uri}")
        else:
            print(f"mismatch\t{replica.uri}")
            has_mismatch = True
    return 1 if has_mismatch else 0


def _sync(catalog: Catalog, secret_id: str, uri: str, apply: bool) -> int:
    secret = catalog.secret(secret_id)
    replica = _replica(secret, uri)
    if not replica.writable:
        raise CatalogError(f"Запись в реплику {uri!r} запрещена каталогом")
    if not apply:
        print(f"Проверка: {secret.id} будет доставлен в {uri}; запись не выполнялась.")
        return 0
    value = SopsStore(catalog.root).read(secret)
    adapter = adapter_for(catalog.root, uri)
    adapter.write(uri, value)
    if adapter.read(uri) != value:
        raise AdapterError(f"Проверка записанной реплики {uri!r} не прошла")
    print(f"Синхронизирован {secret.id} -> {uri}; значение не выводилось.")
    return 0


def _backup(catalog: Catalog, mirror_id: str, apply: bool) -> int:
    if not apply:
        print(f"Проверка: будет создано полное зеркало {mirror_id}; запись не выполнялась.")
        return 0
    target = create_git_bundle(catalog, mirror_id)
    print(f"Полное зеркало создано и проверено: {target}")
    return 0


def _check_mirrors(catalog: Catalog) -> int:
    has_required_failure = False
    for status in check_mirrors(catalog):
        requirement = "required" if status.required else "optional"
        health = "ok" if status.healthy else "failed"
        print(f"{requirement}-{health}\t{status.id}\t{status.detail}")
        if status.required and not status.healthy:
            has_required_failure = True
    return 1 if has_required_failure else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="secretsctl")
    parser.add_argument("--root", help="private state checkout; otherwise DEPLOY_OS_ROOT")
    subcommands = parser.add_subparsers(dest="command", required=True)
    validate = subcommands.add_parser("validate")
    validate.add_argument(
        "--allow-missing",
        action="append",
        default=[],
        metavar="SECRET_ID",
        help="разрешить отсутствие указанной SOPS-копии только для bootstrap",
    )
    subcommands.add_parser("check-mirrors")
    subcommands.add_parser("list")
    show = subcommands.add_parser("show")
    show.add_argument("secret_id")
    import_command = subcommands.add_parser("import")
    import_command.add_argument("secret_id")
    import_command.add_argument("--from", dest="source", required=True)
    import_command.add_argument("--apply", action="store_true")
    import_command.add_argument("--replace", action="store_true")
    check = subcommands.add_parser("check")
    check.add_argument("secret_id")
    sync = subcommands.add_parser("sync")
    sync.add_argument("secret_id")
    sync.add_argument("--to", required=True)
    sync.add_argument("--apply", action="store_true")
    backup = subcommands.add_parser("backup")
    backup.add_argument("mirror_id")
    backup.add_argument("--apply", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        catalog = load_catalog(_repository_root(arguments.root))
        if arguments.command == "validate":
            return _validate(catalog, frozenset(arguments.allow_missing))
        if arguments.command == "check-mirrors":
            return _check_mirrors(catalog)
        if arguments.command == "list":
            return _list(catalog)
        if arguments.command == "show":
            return _show(catalog, arguments.secret_id)
        if arguments.command == "import":
            return _import(
                catalog,
                arguments.secret_id,
                arguments.source,
                arguments.apply,
                arguments.replace,
            )
        if arguments.command == "check":
            return _check(catalog, arguments.secret_id)
        if arguments.command == "sync":
            return _sync(catalog, arguments.secret_id, arguments.to, arguments.apply)
        if arguments.command == "backup":
            return _backup(catalog, arguments.mirror_id, arguments.apply)
    except (AdapterError, CatalogError, MirrorError, OSError, SopsError, ValueError, KeyError, TypeError):
        print("Ошибка конфигурации или операции. Проверьте DEPLOY_OS_ROOT/--root, схему каталога, доступность SOPS и реплик; перед повторной записью выполните check.", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
