# Владение состоянием

Общий toolkit и приватное состояние имеют независимые Git-истории. Выбор
`DEPLOY_OS_ROOT` направляет инструменты к одному авторитетному состоянию.

| Владелец | Содержимое |
| --- | --- |
| Toolkit | vps-ops, resolver, secretsctl, installer, единый skill deploy-os, общие тесты |
| Прикладной репозиторий | Исходники, quality gate, .ops-project |
| Private state | Инвентарь, hosts, profiles, catalog, vault, recovery, runbook |

`vps-ops` находит marker до Git-границы, проверяет contract_version=1 и ID,
запускает executable `state/projects/<id>/operations.sh`. Профиль получает
VPS_OPS_ROOT=state, VPS_OPS_SOURCE_DIR=source и VPS_OPS_PROJECT=id.
Toolkit сам не определяет команды deploy чужого проекта.

`secretsctl` читает catalog.toml выбранного состояния и использует state как
cwd SOPS. Ключи выбирает стандартная конфигурация SOPS оператора. Каталог
определяет разрешённые реплики; writable нужен до sync. В этом выпуске
поддержаны file, workspace-file и ssh-file. SSH использует приватную host
конфигурацию и BatchMode. Транспортные проверки требуют отдельной live-приёмки.

SOPS-копии читаются в память; импорт и sync сначала предлагают dry-run.
После записи выполняется readback. Runtime-файл считается репликой.
Существующий file сохраняет mode/owner, новый создаётся с 0600. SSH заменяет
существующий файл с сохранением metadata; один секрет изменяется последовательно.

Installer принимает ожидаемый origin и полный commit. Изолированный prefix
хранит активный и предыдущий выпуск с receipt. Обновление проверяет origin,
revision, чистоту и public safety; rollback переключает проверенную предыдущую
копию. State и глобальная установка остаются под управлением оператора.

Первый запуск и восстановление входят в deploy-os skill. Отсутствие привязки к
state требует найти существующий authority; решение создать отдельную систему
задаёт оператор. Исторические releases могут содержать creator skill.
Installer выбирает набор ссылок по целевому commit: update удаляет только свою
точную legacy-ссылку, rollback восстанавливает её для старого commit. Чужой
путь вызывает безопасный отказ до activation. Recover согласует topology
с committed receipt после прерывания.
