# Deploy OS

Deploy OS связывает исходники приложения, операционные профили и зашифрованные
секреты стабильным project_id. Репозиторий поставляет общие команды и единый
навык `$deploy-os` для первого запуска, восстановления и текущих операций. Инвентарь, хосты, профили и vault оператора находятся в
отдельном приватном Git-репозитории.

Требования: Python 3.11+, Git, POSIX shell. Для секретов нужны
[SOPS](https://getsops.io/docs/) и [age](https://age-encryption.org/).
Проверки CI выполняются на Linux; доступ к рабочим хостам в suite отсутствует.

## Быстрый локальный пример

```sh
git clone https://github.com/z0rgoyok/deploy-os.git
cd deploy-os
export DEPLOY_OS_ROOT="$PWD/examples/state"
bin/vps-ops --source "$PWD/examples/application" plan
bin/secretsctl list
```

`.ops-project` ищется внутри Git-границы текущего приложения. В примере оба
каталога находятся в checkout toolkit; в рабочей системе приложение и private
state имеют собственные репозитории. Корень состояния всегда выбирается явно.

## Установка по проверенному commit

```sh
bin/install-deploy-os install \
  --prefix "$HOME/.local/share/deploy-os-toolkit" \
  --expected-origin https://github.com/z0rgoyok/deploy-os.git \
  --commit FULL_40_CHARACTER_COMMIT
```

Prefix содержит releases, current, install.json и изолированный home с командами
и навыками. Добавить `home` внутри prefix, затем `.local/bin` в PATH. Подключение навыков
через `bin/bootstrap-deploy-os` к своему HOME выполняется по явному решению;
скрипт останавливается при существующих путях. Он также поддерживает
`DEPLOY_OS_INSTALL_HOME` для отдельной приёмочной среды.

Первый запуск и восстановление доступны через `$deploy-os`: навык сначала
ищет существующее авторитетное состояние и затем выбирает соответствующий
сценарий. Новая isolated установка подключает один skill. При обновлении
старого prefix используется installer из нового проверенного checkout:
[порядок миграции](docs/releasing.md#переход-с-двух-навыков).

Перед операциями задать `DEPLOY_OS_ROOT=/path/to/private-state` и прочитать
инструкции этого состояния. `secretsctl --root DIR` задаёт корень для одного
вызова. Без привязки команды операций останавливаются.

[Операционный контракт](docs/architecture.md),
[секретный интерфейс](.codex/skills/deploy-os/references/secrets.md),
[создание состояния](.codex/skills/deploy-os/references/create-state.md),
[выпуск и обновление](docs/releasing.md).

Лицензия: [Apache License 2.0](LICENSE).
