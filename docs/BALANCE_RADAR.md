# Balance Radar: радар нерфов и баффов

Balance Radar отвечает на вопрос: какие карты, архетипы и классы Hearthstone
вероятнее всего понерфят или забаффают в ближайшем балансном патче. Отдельный
отчёт строится для Стандарта и для Вольного. Радар объединяет три вида данных:

1. **Статистику hs-data-api.** Архетипы и классы берутся из HSGuru meta matrix
   по периодам `past_day`, `past_3_days`, `past_week`, текущему патчу, окну
   дополнения и окну «Bans» (`*_ban`, игры после последнего бан- или
   балансного обновления). Карты берутся из HSReplay за периоды 1d, 3d, 7d,
   14d и patch на рангах Legend и Diamond 4-1.
2. **Соцсети.** Reddit через [RedditAPIs](https://docs.redditapis.com):
   r/hearthstone, r/CompetitiveHS и r/wildhearthstone (hot, top за неделю,
   поиск по балансным словам, комментарии самых обсуждаемых тредов). X через
   [GetXAPI](https://docs.getxapi.com): балансные посты (Top и Latest), посты
   @PlayHearthstone и известных авторов, упоминания главных кандидатов.
3. **Анализ Claude** (`claude-sonnet-5-5`, structured outputs). Модель выбирает
   кандидатов и пишет вердикт на русском. Все числа, ссылки, авторы и score
   постов pipeline берёт из исходных данных по `ref` и id поста, а не из ответа
   модели. Выдуманные модели `ref` и id постов отбрасываются и попадают в
   `usage.droppedModelItems`.

Радар запускается по таймеру, а не на каждый запрос. Каждый прогон сохраняется
в историю. API отдаёт последний успешный прогон, а если очередной анализ не
удался, продолжает отдавать предыдущий.

## Эндпоинты

| Метод | Путь | Параметры | Ответ |
| --- | --- | --- | --- |
| GET | `/v1/constructed/balance-radar` | `format=standard\|wild` (по умолчанию `standard`) | `{data: RadarResult, meta}`. Если успешного прогона ещё нет, вернётся `503`. |
| GET | `/v1/constructed/balance-radar/runs` | `format`, `status=ok\|failed\|skipped`, `limit` (1–200, по умолчанию 30), `offset` | История прогонов, новые первыми, без payload. |
| GET | `/v1/constructed/balance-radar/runs/{run_id}` | — | `{data: RadarResult \| null, run, meta}`. У `failed` и `skipped` поле `data` равно `null`. |

Поле `meta` устроено как в остальных `/v1/*`: `source_id = "balance_radar"`,
`fetched_at = generatedAt`, `count` (число кандидатов или прогонов) и `stale`.
`stale` становится `true`, если прогону больше 36 часов. Ответы кэшируются
через `ETag` и `Cache-Control: public, max-age=300`, ревизия меняется с каждым
сохранённым прогоном. GraphQL этот срез не отдаёт: локальные датасеты
HSGuru/радаров в проекте публикуются только через REST.

Полные примеры реальных ответов (прогон от 2026-10-25):
[Стандарт](examples/balance-radar-standard.json) и
[Вольный](examples/balance-radar-wild.json).

## Контракт `RadarResult` (schemaVersion 1)

Ключи payload записаны в camelCase. Pydantic-схема лежит в
`app/balance_radar/schema.py` (`RadarResult`), а тест сверяет с ней
JSON-примеры из `docs/examples/`.

| Поле | Тип | Описание |
| --- | --- | --- |
| `schemaVersion` | int | Версия контракта, сейчас `1`. |
| `runId` | string | Идентификатор прогона, например `20261025T101048Z-standard-ba1ec2`. |
| `format` | `standard` \| `wild` | Формат отчёта. |
| `generatedAt` | ISO 8601 | Время завершения анализа. |
| `summary` | string | Общий вывод на русском, 2–4 абзаца, без markdown-заголовков. |
| `nerfs`, `buffs` | `RadarCandidate[]` | До 10 кандидатов на каждой стороне (обычно до 8), от самого вероятного. Один `ref` не может стоять на обеих сторонах. |
| `patch` | object \| null | Последний патч из `/api/patches`: `version`, `title`, `titleRu`, `url`, `publishedAt`. |
| `statsPeriods` | object | Периоды HSGuru: `primary` (по нему считаются заголовочные цифры архетипов и классов: окно банов, если оно есть, иначе окно дополнения, иначе `past_week`), `ban`, `current`, `patch`, `used`, `available`. |
| `model` | string | Модель, которая фактически дала ответ. При server-side fallback она может отличаться от запрошенной. |
| `requestedModel` | string | Модель из `RADAR_ANALYSIS_MODEL`. |
| `sources` | `RadarSource[]` | Датасеты статистики (`kind=stats`, `fetchedAt`, `items`) и запросы к соцсетям (`kind=social`, `platform`, `state=ok\|failed\|aborted\|disabled`, `items`, `error`). |
| `socialWindow` | object | `from`, `to`, `posts`, `comments`, `postsSentToModel`. |
| `usage` | object | Расход прогона, см. раздел «Расходы». |

### `RadarCandidate`

| Поле | Тип | Описание |
| --- | --- | --- |
| `ref` | string | Стабильный ключ: `card:<cardId>`, `archetype:<format>:<name>` или `class:<format>:<CLASS>`. |
| `kind` | `card` \| `archetype` \| `class` | Тип сущности. |
| `id` | string | `cardId` (например `BE_085`), название архетипа HSGuru или enum класса. |
| `dbfId` | int \| null | Только у карт. |
| `name` | string | Название: у карт и классов по-русски, у архетипов как в HSGuru. |
| `nameEn` | string \| null | Английское название карты или класса. |
| `class` | string \| null | `WARLOCK`, `DEMONHUNTER`, …, `NEUTRAL`. |
| `className` | string \| null | Класс по-русски. |
| `imageUrl` | string \| null | Арт карты HearthstoneJSON 256x (только у карт). |
| `cost`, `rarity`, `set` | — | Только у карт. |
| `confidence` | int 0–100 | Уверенность модели: 80+ означает сильную статистику и согласие сообщества, 40–60 — заметный, но спорный сигнал. |
| `headline` | string | Вывод в одну строку. |
| `verdict` | string | Подробный вердикт, 3–6 предложений с цифрами. |
| `reasons` | string[] | 2–5 причин. |
| `suggestedChange` | string | Правдоподобное изменение, например «Стоимость 2 → 3». |
| `stats` | object | Статистические сигналы, см. ниже. |
| `social` | object | Сигналы соцсетей, см. ниже. |
| `dates` | object | Даты, см. ниже. |

**`stats`.** Поля `source` (`hsguru` для архетипов и классов, `hsreplay` для
карт), `rank` и `period` описывают заголовочный срез. Дальше идут:

- `winrate` и `popularity` в процентах. У карт это винрейт колод с картой и доля
  колод с картой. У архетипов и классов — винрейт и доля игр;
- `games` у архетипов и классов, `timesPlayed`, `playedWinrate`, `drawnWinrate`
  и `keepPercentage` у карт;
- `legendWinrate`, `legendPopularity` и `legendGames` у архетипов;
- `topArchetypes` у классов;
- `trend`. У архетипов: `winrateDelta` и `popularityDelta` (`past_day` минус
  `past_week`), `banWinrateDelta` и `banPopularityDelta` (окно банов минус всё
  дополнение). У карт: `popularityDelta` (1d минус 7d) и `playedWinrateDelta`
  (3d минус 14d). Поле `basis` описывает формулу словами;
- `periods`: все срезы ранг × период. У архетипов это `winrate`, `popularity`,
  `games`; у карт — `deckWinrate`, `deckPopularity`, `playedWinrate`,
  `timesPlayed` и другие.

«Винрейт при розыгрыше» (`playedWinrate`) систематически завышен у дорогих
карт. Поэтому пулы кандидатов ранжируются по винрейту колод, а модель получает
`playedWinrate` только как вспомогательный сигнал.

**`social`.**

- `mentions.total`, `mentions.reddit`, `mentions.x` — сколько постов и
  комментариев за окно называют сущность. Поиск идёт по русскому и английскому
  названию и вариантам вроде `Zoolock` / `Zoo Warlock` и `DH` / `Demon Hunter`;
  названия короче 4 символов не ищутся.
- `sentiment`: `negative | mixed | neutral | positive | no_data`.
- `stance`: `wants_nerf | wants_buff | divided | indifferent | no_data`.
- `summary` — пересказ обсуждений на русском.
- `topPosts` — до 5 постов. Сначала идут посты, которые процитировала модель
  (`citedByModel=true`), затем самые заплюсованные из упоминаний. У каждого
  поста есть `platform`, `kind` (`post | comment | tweet`), `id`, `url`,
  `author`, `score` (апвоуты Reddit или лайки X), `comments`, `title`,
  `excerpt` (до 280 символов), `community`, `createdAt` и `official`.

**`dates`.**

- `firstFlaggedAt` — с какого прогона кандидат непрерывно стоит на этой стороне
  радара.
- `previousConfidence` — уверенность в прошлом прогоне. Если в прошлом прогоне
  кандидата не было, `null`.
- `statsFetchedAt` — время датасета HSGuru или HSReplay.
- `socialWindowFrom` и `socialWindowTo` — окно сбора постов.

### Пример кандидата (сокращён)

```json
{
  "ref": "card:BE_085",
  "kind": "card",
  "id": "BE_085",
  "dbfId": 129793,
  "name": "Ритуал призыва",
  "nameEn": "Ritual of Summoning",
  "class": "WARLOCK",
  "className": "Чернокнижник",
  "imageUrl": "https://art.hearthstonejson.com/v1/256x/BE_085.png",
  "confidence": 75,
  "headline": "Ритуал призыва — самый частый и самый обсуждаемый элемент Zoolock",
  "verdict": "На Легенде у карты 55,9% винрейта при 18,8% популярности за 7 дней…",
  "reasons": ["Винрейт на Легенде 55,9% и популярность 18,8–27,2%…"],
  "suggestedChange": "Стоимость 2 → 3 или статы призываемых существ 3/5 → 3/4",
  "stats": {
    "source": "hsreplay", "rank": "legend", "period": "7d",
    "winrate": 55.9, "popularity": 18.82, "playedWinrate": 55.55, "timesPlayed": 64641,
    "trend": {"popularityDelta": 8.35, "playedWinrateDelta": -0.02,
              "basis": "popularity 1d − 7d, played winrate 3d − 14d, rank legend"},
    "periods": [{"rank": "legend", "period": "1d", "deckWinrate": 56.47, "deckPopularity": 27.17}]
  },
  "social": {
    "mentions": {"total": 7, "reddit": 6, "x": 1},
    "sentiment": "negative",
    "stance": "wants_nerf",
    "summary": "Популярный тред «Nerf Ritual of Summoning» набрал 352 очка и 253 комментария…",
    "topPosts": [{
      "platform": "reddit", "kind": "post", "id": "reddit:1xdju41",
      "url": "https://reddit.com/r/hearthstone/comments/1xdju41/nerf_ritual_of_summoning/",
      "author": "Sea_Particular_244", "score": 352, "comments": 253,
      "title": "Nerf Ritual of Summoning.", "excerpt": "They are not insta-nerfing Rituals…",
      "community": "r/hearthstone", "createdAt": "2026-10-23T17:06:53.000Z",
      "official": false, "citedByModel": true
    }]
  },
  "dates": {
    "firstFlaggedAt": "2026-10-25T10:12:15+00:00",
    "previousConfidence": null,
    "statsFetchedAt": "2026-10-25T06:01:39.545307+00:00",
    "socialWindowFrom": "2026-10-18T00:00:00+00:00",
    "socialWindowTo": "2026-10-25T10:10:48+00:00"
  }
}
```

## Запуск и расписание

- Таймер `hs-data-api-docker-balance-radar.timer` срабатывает в 02:10 и 14:10
  по Варшаве, после двенадцатичасового обновления HSGuru meta matrix. Он
  запускает `docker compose run --rm api python -m app.balance_radar run`.
- Коды выхода: `0` — всё успешно. `10` — один формат не удался или один из
  источников соцсетей был недоступен; для systemd это успешный выход. `1` —
  не удалось ничего.
- Если вход модели не изменился с прошлого успешного прогона, анализ
  пропускается (`status=skipped`) и Claude не вызывается. Ответы соцсетей
  кэшируются на диске на 3 часа (`RADAR_SOCIAL_CACHE_TTL_SECONDS`), поэтому
  повторный запуск после сбоя анализа не платит за них повторно.
- Ручной запуск: `python -m app.balance_radar run [--format standard] [--force]`.
  С `--dry-run --dump-dir DIR` вход модели сохраняется в файл без вызова Claude.
  С `--no-social` сбор соцсетей пропускается. `python -m app.balance_radar show
  --format wild` печатает последний результат, `python -m app.balance_radar
  runs` — историю.
- История лежит в `<data>/balance-radar.sqlite3` (таблица `runs`), хранение
  400 дней.

## Конфигурация

Ключи передаются в контейнер через `env_file`
`${HS_RADAR_ENV_FILE:-/home/debian/server/secrets/hs-data-api/radar.env}`
(`required: false`; файл принадлежит root, права 600). Ключи не попадают в
логи, кэш и ledger расходов.

| Переменная | Назначение |
| --- | --- |
| `REDDITAPIS_API_KEY` | Bearer-ключ RedditAPIs. Без него Reddit пропускается (`state=disabled`). |
| `GETXAPI_API_KEY` | Bearer-ключ GetXAPI. Без него X пропускается. |
| `ANTHROPIC_API_KEY` | Ключ Claude API. |
| `RADAR_ANALYSIS_MODEL` | Модель анализа, по умолчанию `claude-sonnet-5-5`. |
| `RADAR_X_ACCOUNTS` | Необязательно: список аккаунтов X через запятую вместо встроенного (PlayHearthstone, bmkibler, ThijsHS, TrumpSC, Kripparrian, HSReplayNet, ViciousSyndicat, ZalaeHS, RduHS). |
| `RADAR_SOCIAL_WINDOW_DAYS` | Окно постов, по умолчанию 7 дней (1–30). |
| `RADAR_SOCIAL_CACHE_TTL_SECONDS` | TTL дискового кэша соцсетей, по умолчанию 10800. |

Запрос к Claude идёт с adaptive thinking, `effort=high`, кэшированием
системного промпта и server-side fallback (`fallbacks: "default"`, beta
`server-side-fallback-2026-07-01`) на случай отказа классификатора.

## Расходы

Каждый HTTP-вызов пишется в `provider-usage.sqlite3` с провайдером
`redditapis`, `getxapi` или `anthropic` и `source_id = balance_radar`, поэтому
расходы видны в `/ops/provider-usage`. Блок `usage` в payload содержит:

- `analysis` — токены (`inputTokens`, `outputTokens`, `cacheReadInputTokens`,
  `cacheCreationInputTokens`), `costUsd`, `durationSeconds`, `fallbackUsed`;
- `social.redditapis` и `social.getxapi` — `requests`, `billableRequests`,
  `cacheHits`, `failedRequests`, `costUsd`;
- `socialCostUsd` и `totalCostUsd`. Сбор соцсетей общий для обоих форматов
  (`socialSharedAcrossFormats=true`), поэтому его стоимость входит в `usage`
  каждого формата.

Оценка полного прогона (оба формата) по прогонам 2026-10-25:

| Статья | Запросы / токены | Цена |
| --- | --- | --- |
| RedditAPIs | ~22 чтения ($0.0015) | ~$0.033 |
| GetXAPI | ~7 страниц поиска ($0.001) | ~$0.007 |
| Claude Стандарт | ~86K вход + ~11K выход | ~$0.29 |
| Claude Вольный | ~62K вход + ~10K выход (системный промпт из кэша) | ~$0.23 |
| **Итого за прогон** | | **~$0.56** |

Два прогона в день стоят около $1.1 в день и около $34 в месяц. Бесплатные
вызовы `/account/me` в конце прогона сохраняют балансы обоих провайдеров в
сводку CLI.
