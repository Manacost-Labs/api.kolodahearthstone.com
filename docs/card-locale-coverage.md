# Отчёт покрытия каталога «локаль × режим»

Скрипт `panel/scripts/card_locale_coverage.py` сравнивает id из HearthstoneJSON
(эталон — `enUS/cards.json`) с таблицей `hearthstone_card_localizations` по
локалям `ruRU`, `enUS`, `plPL`, `esES`. Только чтение, ничего не пишет.
Эпик: api#92 (E24), срез api#98.

## Что значит «покрыта»

Эталон каждой локали — `cards.json` этой же локали (id берутся из `enUS`).
Карта покрыта в локали L, если в таблице есть строка `(card_id, L)` с
непустым `name` и семь полей из `payload` (`name`, `text`, `flavor`,
`collectionText`, `howToEarn`, `howToEarnGolden`, `targetingArrowText`)
совпадают с HSJ этой локали. Поле, которого нет (или оно пустое) в HSJ,
пропуском не считается. Строка обязательна, кроме карт, у которых в HSJ
пусты все семь полей (у них отсутствие строки пропуском не считается). Непокрытые карты делятся на «нет строки» (строки нет
или `name` пуст) и «устарела» (строка есть, но поле отличается от HSJ):
в JSON это `missing_*` и `stale_*`, в Markdown — отдельные подписи в примерах.

## Группы

Группы не исключают друг друга. Каждый id попадает хотя бы в одну; последняя
группа «Прочие» — остаток. Правила по полям HSJ:

| Группа | Правило |
| --- | --- |
| Стандарт, Вольный | id из БД `constructed_format_cards` (`in_format=1`, `format_slug`); в HSJ формата нет |
| Классика | `set == VANILLA` и `collectible` |
| Твист, Арена | `n/a`: признака пула нет ни в HSJ, ни в БД |
| BG, признак | `set == BATTLEGROUNDS` или id по префиксам `BG<n>_`, `BGS_`, `TB_BaconUps_`, `HM_BG_` (карты BG с «классическим» `set`: `BG_OG_044_G`, `BGS_039`, `TB_BaconUps_*`). `techLevel` и `battlegrounds*DbfId` для этого не годятся: они есть и у коллекционных карт конструкта |
| BG: герои / силы героев | признак BG и `type` `HERO` / `HERO_POWER` |
| BG: существа | признак BG, `MINION`, не `isBattlegroundsBuddy` |
| BG: напарники | `isBattlegroundsBuddy` или `type == BATTLEGROUND_HERO_BUDDY` |
| BG: заклинания, аксессуары, аномалии | `type` `BATTLEGROUND_SPELL`, `_TRINKET`, `_ANOMALY` |
| BG: квесты и награды | `BATTLEGROUND_QUEST_REWARD`, либо `BATTLEGROUNDS` + `SPELL` + `QUEST` в `mechanics` |
| BG: призы Ярмарки | есть `battlegroundsDarkmoonPrizeTurn` |
| BG: эффекты и служебные | остальные `set == BATTLEGROUNDS` |
| Наёмники | `set == LETTUCE`, `type == LETTUCE_ABILITY`, либо есть `mercenariesRole` / `mercenariesAbilityCooldown` |
| Потасовка | `set == TB` |
| Приключения | не `collectible` и (`set` ∈ `MISSIONS`, `TAVERNS_OF_TIME`, `TUTORIAL` или id по префиксам `Story`, `BOM`, `BOH`, `BOTA`, `BTA`, `DALA`, `ULDA`, `GILA`, `LOOTA`, `TRLA`, `DRGA`, `ICCA<n>`, `LOEA<n>`, `BRMA<n>`, `NAX<n>`, `KARA`, `Prologue`, `TUTR`, `TU<n>`) |
| Скины героев | `set == HERO_SKINS` |
| Питомцы | `type == PET` |
| Монетки, Тавернщики | id из БД `hearthstone_coins`, `battlegrounds_bartender_skins` |
| Прочие | всё, что не попало выше. Без `--db` сюда попадают коллекционные карты Стандарта и Вольного, у которых нет другой группы |

Группы, которым нужны id из БД (Стандарт, Вольный, Монетки, Тавернщики),
выводятся как `n/a`, если наборы не переданы (`--db` или `--db-sets-file`); отчёт
пишет об этом явную строку.

Итоговая строка «Все id HSJ» считает покрытие по каждой локали на всём
эталоне: именно она доказывает полноту.

`--db` при ошибке (нет драйвера, нет таблицы, нет доступа) пишет понятное
сообщение в stderr и выходит с кодом 2, без traceback.

## Запуск

```bash
# прод, после создания таблицы (из panel/current, под пользователем сервиса)
python scripts/card_locale_coverage.py --db

# без БД: дамп строк таблицы и/или локальные снимки HSJ (<locale>.json)
python scripts/card_locale_coverage.py --rows-file rows.json \
  --db-sets-file sets.json --hsj-dir /path/to/hsj --json
```

- `--rows-file`: JSON-список `{card_id, locale, name, payload}`. Без `--db` и
  `--rows-file` набор строк пуст (все 0%): проверка классификации.
- `--db-sets-file`: JSON `{standard, wild, coins, bartenders: [card_id, …]}`.
- `--examples N`: сколько непокрытых id показывать в ячейке (по умолчанию 5).
- `--json`: машинный вывод вместо Markdown-таблицы.
- `--hsj-ruRU|enUS|plPL|esES FILE`: путь к `cards.json` конкретной локали
  (приоритетнее `--hsj-dir`).
- Без `--hsj-dir` и этих опций HSJ скачивается (4 файла, бесплатно).
- `--renders-dir DIR`: добавляет отчёт покрытия рендеров (см. ниже). Без флага
  вывод не меняется ни на байт.

## Покрытие рендеров

Опция `--renders-dir` (на проде `/srv/api-kolodahearthstone/panel-data/var/card-renders`)
читает только `{locale}.index.json` загрузчика `sync_card_renders.py`
(формат в `docs/card-renders.md`). Файлы рендеров не читаются и не `stat`-ятся:
после S3-оффлоада это симлинки. Группы те же, что в текстовом отчёте, под
ним в Markdown добавляется таблица «Рендеры: локаль × режим», в `--json` — блок
`renders` с теми же числами.

Ячейка: `есть / ожидается, 404: N, ещё: M, P%`.

- **ожидается** — id группы, чей `type` (по эталону enUS) не входит в
  `skip_types` индекса этой локали (`ENCHANTMENT`, `PET` и т.д. рендера не имеют);
- **есть** — id в `renders`;
- **404 у HSJ** (`missing_hsj`) — ожидаемые id из `missing`: рендера у HSJ нет;
- **ещё не скачано** (`pending`) — остальные ожидаемые, их примеры
  показывает `--examples`;
- **%** = есть / (ожидается − 404 у HSJ), округление вниз: 100% только когда
  скачано всё, что у HSJ есть.

Нет индекса локали (или он повреждён) — в ячейке «нет данных», в JSON `null`, код
выхода 0. Прогон на проде, читать индексы нужно с `sudo -n`:

```bash
sudo -n python3 scripts/card_locale_coverage.py --db \
  --renders-dir /srv/api-kolodahearthstone/panel-data/var/card-renders
```
