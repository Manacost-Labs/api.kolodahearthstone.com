# Web panel

Battlegrounds imports recognize HearthstoneJSON `ABERRATION` as `aberration`
(«Аберрация») in the API and panel filters. After deploying this change, run
the existing `kolodahs-sync@cards.service` job to repair previously imported
cards with a null creature type. The normal import updates existing rows even
when their source payload hash is unchanged; no schema migration is needed.
Regression check: `php panel/tests/battleground_creature_types_test.php`.

This directory is the source of the authenticated database panel served at
`https://api.kolodahearthstone.com/`. Only GitHub user `Zulut30` is allowed to
open it. The public REST compatibility surface is under `/api/v1`, while the
central GraphQL API is under `/v1/`.

Runtime-only files are deliberately excluded from Git:

- `config.php` contains the database connection settings;
- `uploads/` contains generated media;
- `var/` contains caches and synchronization state;
- GitHub OAuth and API-token credentials live below `/var/lib/koloda/`.

Production layout:

| Path | Purpose |
|---|---|
| `/srv/api-kolodahearthstone/panel/releases/<id>` | immutable code release |
| `/srv/api-kolodahearthstone/panel/current` | active release symlink |
| `/srv/api-kolodahearthstone/panel-data/uploads` | persistent media |
| `/srv/api-kolodahearthstone/panel-data/var` | persistent cache and job state |
| `/etc/api-kolodahearthstone/panel-config.php` | private database config |

Deploy from the repository root with `sudo scripts/deploy-panel.sh`. The script
creates a new release and switches `current` atomically. It never copies or
deletes the persistent data directories.

## Пул Полей сражений 36.6.1

`data/battleground-pool-36.6.1.json` фиксирует официальную ротацию от
22 сентября 2026: 35 удалений, 22 возвращённых существа таверны, новые
существа и пять игровых версий Dark Paradox. Три создаваемых Volumizer,
божества и карточка-заглушка Dark Paradox не становятся предложениями таверны.

HearthstoneJSON от 16 сентября уже содержит новые карты, но ещё старые флаги
пула. Импортер применяет поправку только к проверенному SHA-256 состава пула
и сохраняет источник поправки в истории изменений. Исходные карточки не
удаляются; `in_pool=0` оставляет их доступными в архиве. Золотые версии
сохраняют отдельные исходные флаги и связь с обычной картой.

Когда upstream полностью совпадает с поправкой, она автоматически перестаёт
применяться. Если пришёл другой, противоречащий список, импорт останавливается
до записи карточек с сообщением `review patch`: сверить очередной официальный
патч, обновить либо удалить временную поправку и повторить синхронизацию.
Не менять контрольную сумму без проверки нового состава.

Проверка: `php panel/tests/battleground_pool_patch_test.php`. После выкладки
запустить `kolodahs-sync@cards.service` и проверить 304 обычных существа в
актуальном пуле, включая 27 аберраций. Общий аудит изображений может отдельно
сообщать о внешних картинках золотых Dark Paradox; это не отменяет импорт.
