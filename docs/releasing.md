# Выпуск и обновление

Public toolkit — канонический источник общих инструментов. Изменение частной
инфраструктуры может показать потребность в общем механизме; агент оформляет
её как отдельное изменение toolkit с синтетическими входами.

## Подготовка выпуска

1. Создать ветку от актуального public main.
2. Перенести только явно выбранную общую логику, убрать реальные metadata и
   рабочие defaults. Добавить сценарии поведения и документацию.
3. Для новых файлов явно обновить public-files.txt. Проверить каждый новый
   путь и весь diff. Public allowlist задаёт точную границу tracked tree.
4. До отправки любой ветки или PR выполнить локальные проверки metadata
   приватного владельца по его manifest. Это обязательный барьер до раскрытия.
   Выполнить unittest, check-public-tree, git diff --check. Создать commit,
   затем проверить `check-public-tree.py --revision FULL_COMMIT --history`.
5. Открыть PR. Независимый review проверяет точный commit, CI проверяет код и
   полную доступную историю. Публиковать только проверенную редакцию.

Публикуются main и проверенные PR-ветки. Tags пока запрещены: gate
останавливается при tag refs, поскольку tag metadata требует отдельного audit.

Первый выпуск создаётся в новом репозитории с новой историей. Экспорт .git,
зашифрованных vault/recovery и состояния оператора в public запрещён.
Safety gate дополняет review. Он использует allowlist, проверку типов файлов,
ключей, токенов, локальных путей, адресов и URL-hosts; неизвестные публичные
источники добавляются после review. Полноту выявления произвольных секретов
эвристический сканер не гарантирует.

## Приёмка и update

Получить reviewed полный commit и точный origin. Вызвать install в отдельном
prefix, проверить commands/skills, root isolation, synthetic plan и SOPS
roundtrip. В приватном журнале сохранить origin, revision и результат проверок.

```sh
bin/install-deploy-os update --prefix "$PREFIX" \
  --expected-origin "$ORIGIN" --commit "$REVIEWED_COMMIT"
bin/install-deploy-os rollback --prefix "$PREFIX" --expected-origin "$ORIGIN"
```

Update останавливается при dirty checkout, несовпадении origin/receipt или
коллизии путей. install.json хранит current и previous. Activation использует журнал activation.json. При прерывании запустить
`bin/install-deploy-os recover --prefix "$PREFIX" --expected-origin "$ORIGIN"`;
команда возвращает current к committed install.json и проверяет staging paths.
После неясного исхода проверить receipt, current и фактический HEAD до повтора.
Переключение live-потребителя проверяется по его private runbook.

## Сохранение ресурсов

Временная clone-подготовка удаляется автоматически. Prefix сохраняет версии
для rollback. После принятия выпуска можно удалить более старые releases,
которые отсутствуют в current/previous receipt, предварительно проверив
чистоту и отсутствие ссылок на них. Удаление private backups и runtime не
входит в обслуживание toolkit.
