from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from .base import AdapterError


def _read_host_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = os.path.expandvars(os.path.expanduser(value))
    return values


@dataclass(frozen=True)
class SSHConnection:
    command: tuple[str, ...]
    is_root: bool


def connection_for(repository_root: Path, host_alias: str) -> SSHConnection:
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", host_alias) is None:
        raise AdapterError("Invalid host alias; use kebab-case")
    deploy_root = repository_root.resolve()
    host_env = deploy_root / "hosts" / f"{host_alias}.env"
    if not host_env.is_file():
        raise AdapterError(f"Локальная конфигурация хоста {host_alias!r} отсутствует")
    values = _read_host_env(host_env)
    host = values.get("SSH_HOST")
    user = values.get("SSH_USER")
    if not host or not user:
        raise AdapterError(f"Конфигурация {host_env.name} не задаёт SSH_HOST/SSH_USER")
    command = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    _append_transport_options(command, values, deploy_root)
    key_path = values.get("SSH_KEY_PATH")
    if key_path:
        command.extend(["-i", key_path])
    jump_host_file = values.get("SSH_JUMP_HOST_FILE")
    if jump_host_file:
        jump_path = Path(jump_host_file)
        if not jump_path.is_absolute():
            jump_path = deploy_root / jump_path
        if not jump_path.is_file():
            raise AdapterError(
                f"Конфигурация промежуточного SSH-хоста отсутствует: {jump_path}"
            )
        jump_values = _read_host_env(jump_path)
        jump_host = jump_values.get("SSH_HOST")
        jump_user = jump_values.get("SSH_USER")
        if not jump_host or not jump_user:
            raise AdapterError(
                f"Конфигурация {jump_path.name} не задаёт SSH_HOST/SSH_USER"
            )
        proxy_command = ["ssh"]
        jump_key_path = jump_values.get("SSH_KEY_PATH")
        if jump_key_path:
            proxy_command.extend(["-i", jump_key_path])
        _append_transport_options(proxy_command, jump_values, deploy_root)
        # Bastion сохраняет доступ к серверной реплике, когда прямой маршрут
        # через тот же VPN возвращает трафик на исходный хост.
        proxy_command.extend(
            [
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                "-W",
                "%h:%p",
                f"{jump_user}@{jump_host}",
            ]
        )
        command.extend(["-o", f"ProxyCommand={shlex.join(proxy_command)}"])
    command.append(f"{user}@{host}")
    return SSHConnection(command=tuple(command), is_root=user == "root")


def _append_transport_options(
    command: list[str], values: dict[str, str], deploy_root: Path
) -> None:
    control_dir = values.get("SSH_CONTROL_DIR")
    control_persist = values.get("SSH_CONTROL_PERSIST")
    if control_dir or control_persist:
        resolved_control_dir = Path(
            control_dir or str(Path.home() / ".ssh" / "controlmasters")
        ).expanduser()
        host_id = values.get("HOST") or values.get("SSH_HOST")
        if not host_id:
            raise AdapterError("SSH control master требует HOST или SSH_HOST")
        command.extend(
            [
                "-o",
                "ControlMaster=auto",
                "-o",
                f"ControlPersist={control_persist or '8h'}",
                "-o",
                f"ControlPath={resolved_control_dir / f'{host_id}.sock'}",
            ]
        )

    known_hosts = values.get("SSH_KNOWN_HOSTS_FILE")
    if not known_hosts:
        return
    known_hosts_path = Path(known_hosts).expanduser()
    if not known_hosts_path.is_absolute():
        known_hosts_path = (deploy_root / known_hosts_path).resolve()
        try:
            known_hosts_path.relative_to(deploy_root)
        except ValueError as exc:
            raise AdapterError(
                "SSH_KNOWN_HOSTS_FILE за пределами Deploy OS должен быть абсолютным"
            ) from exc
    if not known_hosts_path.is_file():
        raise AdapterError(f"SSH known_hosts отсутствует: {known_hosts_path}")
    command.extend(
        [
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            f"UserKnownHostsFile={known_hosts_path}",
        ]
    )
