---
name: deploy-os
description: Create, recover and operate an operator-owned private Deploy OS state repository with project context, deployment profiles, encrypted secrets, backup and rollback.
---

# Deploy OS

Общие инструменты установлены вместе с этим навыком. Фактическое состояние
оператора хранится в одном приватном Git-репозитории.

## Выбор сценария

- При явной просьбе создать новую систему, восстановить утраченное состояние
  или выполнить первый запуск прочитать [создание и восстановление](references/create-state.md).
  Сначала найти существующий авторитетный инвентарь, vault и резервные копии.
  Работа с найденным состоянием следует его владельцу и проверяемым копиям.
- Для текущих операций использовать выбранный private state по шагам ниже.
  Отсутствие `DEPLOY_OS_ROOT` само по себе не разрешает создание нового vault.

Локальная работа следует запросу оператора. Создание облачных ресурсов, перенос
секретов, публикация и изменения работающих систем требуют разрешения на
соответствующие системы в задаче. Новый state включает первого реального
потребителя, проверяемый restore, документацию и обязательные копии.

## Операции существующего состояния

1. Прочитать инструкции текущего проекта.
2. Получить явный `DEPLOY_OS_ROOT` оператора. Если он отсутствует, найти
   документированную привязку либо запросить путь; создание нового vault требует
   отдельного решения о новой системе.
3. Прочитать `AGENTS.md` выбранного состояния и выполнить
   `python3 -B <skill-dir>/scripts/resolve-context.py --start "$PWD"`.
4. Для подключённого проекта прочитать его профиль `projects/<id>/` и runbook.
   Новый проект подключать по [onboarding](references/onboarding.md).

Приложение владеет исходниками и quality gate. Маркер `.ops-project` содержит
`project=<kebab-case-id>` и `contract_version=1`; он связывает применимые
записи инвентаря, профиль и каталог секретов. `vps-ops` передаёт профильному
скрипту корень исходников без абсолютного пути в профиле.

Перед инфраструктурной записью снять состояние, создать проверенный backup и
определить rollback. Менять один слой за раз. После записи проверить сервис,
маршрут, данные и нужные реплики; обновить приватную документацию.

Для секретов прочитать [контракт](references/secrets.md). Значения передаются
между адаптерами и SOPS в памяти/stdin. Ответы содержат metadata и статусы.
Источник поставки прикладного кода принадлежит оператору; для внешнего проекта
используется его управляемый форк. Артефакт связан с commit и digest.

## Agent integrations and progress reporting

Use these rules when Deploy OS configures an agent, an MCP bundle, or a
progress-reporting service:

1. **Scope MCP per agent.** Keep a global server registry separate from each
   agent's explicit allowlist. An agent may use a server only when it appears in
   that agent's allowlist; an entry in the registry alone never grants access.
   Resolve the requested scope before starting a session and fail closed when a
   server, tool, or project is outside that scope.
2. **Apply changes only while idle.** Before changing an agent gateway or its
   service units, verify that the agent has no active run. Save a rollback copy,
   apply one layer at a time, restart only the owned service, and read back the
   effective configuration and health state. Never interrupt an active run to
   apply routine configuration.
3. **Migrate observer routes safely.** Treat a legacy observer route as live
   until the replacement has been installed, enabled, and read back. Make the
   migration idempotent, verify the new route against a real or synthetic event,
   then disable and remove the legacy route. Keep ownership and a rollback path
   explicit; do not delete an unverified route.
4. **Start reporting in dependency order.** Start and verify the observer first;
   start the progress-card service only after the observer is ready. The card
   must consume the observer's durable state, preserve message ownership, and
   avoid deleting or editing a card belonging to another run.
5. **Make first systemd installation repeatable.** A first installation must not
   fail because a referenced service unit has not been installed yet. Install
   units in dependency order, use explicit `After=`/`Requires=` relationships
   only for units that are present, run `systemd-analyze verify`, then enable,
   start, and read back each owned unit. A repeated installation must converge
   to the same state without duplicating timers, routes, or receipts.

Отдельно сообщать локальные проверки, публикацию, live smoke и невыполненную
приёмку. Обновление инструментария описано в [release guide](../../../docs/releasing.md).
