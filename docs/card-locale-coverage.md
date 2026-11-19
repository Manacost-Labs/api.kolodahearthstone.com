# Отчёт покрытия каталога «локаль × режим»

Скрипт `panel/scripts/card_locale_coverage.py` сравнивает id из HearthstoneJSON
(эталон — `enUS/cards.json`) с таблицей `hearthstone_card_localizations` по
локалям `ruRU`, `enUS`, `plPL`, `esES`. Только чтение, ничего не пишет.
Эпик: api#92 (E24), срез api#98.

## Что значит «покрыта»

Карта покрыта в локали, если в таблице есть строка `(card_id, locale)` с
непустым `name`. Если имя пустое в самом HSJ этой локали (например, у
служебных эффектов), карта пропуском не считается.

## Группы

Группы не исключают друг друга. Каждый id попадает хотя бы в одну; последняя
группа «Прочие» — остаток. Правила по полям HSJ:

| Группа | Правило |
| --- | --- |
| Стандарт, Вольный | id из БД `constructed_format_cards` (`in_format=1`, `format_slug`); в HSJ формата нет |
| Классика | `set == VANILLA` и `collectible` |
| Твист, Арена | `n/a`: признака пула нет ни в HSJ, ни в БД |
| BG: герои / силы героев | `set == BATTLEGROUNDS` и `type` `HERO` / `HERO_POWER` |
| BG: существа | `BATTLEGROUNDS`, `MINION`, не `isBattlegroundsBuddy` |
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
| Прочие | всё, что не попало выше |

Группы, которым нужны id из БД (Стандарт, Вольный, Монетки, Тавернщики),
выводятся как `n/a`, если наборы не переданы (`--db` или `--db-sets-file`).

## Запуск

```bash
# прод, после создания таблицы (из panel/current, под пользователем сервиса)
python scripts/card_locale_coverage.py --db

# без БД: дамп строк таблицы и/или локальные снимки HSJ (<locale>.json)
python scripts/card_locale_coverage.py --rows-file rows.json \
  --db-sets-file sets.json --hsj-dir /path/to/hsj --json
```

- `--rows-file`: JSON-список `{card_id, locale, name}`. Без `--db` и
  `--rows-file` набор строк пуст (все 0%): проверка классификации.
- `--db-sets-file`: JSON `{standard, wild, coins, bartenders: [card_id, …]}`.
- `--examples N`: сколько непокрытых id показывать в ячейке (по умолчанию 5).
- `--json`: машинный вывод вместо Markdown-таблицы.
- Без `--hsj-dir` HSJ скачивается (4 файла, бесплатно).
