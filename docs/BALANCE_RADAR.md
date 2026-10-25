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
| GET | `/v1/constructed/balance-radar/history` | см. ниже | Прошлые балансные патчи с точными правками карт. |
| GET | `/v1/constructed/balance-radar/patterns` | — | Закономерности Blizzard и калибровка. |
| GET | `/v1/constructed/balance-radar/backtest` | — | Бэктест на прошлых патчах: precision/recall. |
| GET | `/v1/constructed/balance-radar/confidence-history` | `format`, `ref`, `side=nerfs\|buffs`, `runs` (2–120, по умолчанию 30) | `{data: {format, series: [{side, ref, points: [{runId, generatedAt, confidence}]}]}, meta}` для графика уверенности. |

Поле `meta` устроено как в остальных `/v1/*`: `source_id = "balance_radar"`,
`fetched_at = generatedAt`, `count` (число кандидатов или прогонов) и `stale`.
`stale` становится `true`, если прогону больше 36 часов. Ответы кэшируются
через `ETag` и `Cache-Control: public, max-age=300`, ревизия меняется с каждым
сохранённым прогоном. GraphQL этот срез не отдаёт: локальные датасеты
HSGuru/радаров в проекте публикуются только через REST.

Полные примеры реальных ответов (прогон от 2026-10-25):
[Стандарт](examples/balance-radar-standard.json),
[Вольный](examples/balance-radar-wild.json),
[история](examples/balance-radar-history.json),
[закономерности](examples/balance-radar-patterns.json),
[бэктест](examples/balance-radar-backtest.json),
[история уверенности](examples/balance-radar-confidence-history.json).

## Что добавлено в schemaVersion 2

Все изменения добавочные: поля v1 сохранены, у старых прогонов новых полей нет
(или они пустые).

**Больше кандидатов.** Модель отдаёт 15–25 кандидатов на нерф и 15–25 на бафф
(не меньше 10 confidence). Списки отсортированы по `confidence` и содержат
карты, архетипы и классы. Пулы для отбора стали шире:

- архетипы HSGuru: ранги all, legend, top_legend и diamond_4to1 в окне банов
  (`stats.ranks`); в пул попадают архетипы с ≥1000 игр на all или ≥500 на
  Легенде — до 60 по числу игр плюс по 25 с наибольшим перекосом;
- карты HSReplay: Legend, Diamond 4-1 и Diamond; 60 кандидатов на нерф и 50 на
  бафф, не больше 12 на класс; в баффы попадают и непопулярные эпики и
  легендарки нового набора.

**Новые поля `RadarCandidate`:**

| Поле | Тип | Описание |
| --- | --- | --- |
| `keyNumbers` | string[] | 2–5 цифр, на которых основан вывод, со сравнением с порогом или калибровкой. |
| `predictedChanges` | `{change, field, before, after, probability}[]` | 1–3 варианта конкретной правки. `field` — `cost \| attack \| health \| durability \| armor \| text \| other`; `before`/`after` — числа или `null`; `probability` 0–100, сумма не больше 100. `suggestedChange` — самый вероятный вариант. |
| `metaImpact` | string \| null | Ожидаемый эффект на мету. |
| `quotes` | `{text, postId, platform, kind, url, author, score, createdAt, official, authorRole}[]` | До 3 дословных цитат из сообщества. Код проверяет, что текст действительно есть в посте; придуманные цитаты отбрасываются. |
| `developerSignal` | string \| null | Что говорили разработчики или официальный аккаунт. |
| `calibration` | object \| null | `{level, band, historicalRate, samples, hits, examples[], withPatchIn14Days, features: {winrate, popularity, source}}` — как часто Blizzard меняла сущность с такими цифрами (см. ниже). |
| `trend` | `{status: new\|up\|down\|stable, previousConfidence, delta}` | Изменение уверенности с прошлого прогона (±5 и больше — `up`/`down`). |
| `confidenceHistory` | `{runId, generatedAt, confidence}[]` | Уверенность в последних 30 прогонах, от старых к новым, включая текущий. |

У `stats` появились поля:

- `ranks` (архетипы) — `{rank: {winrate, popularity, games}}`;
- `mulligan`. У архетипа это `{period, rank, games, fetchedAt, keyCards[],
  weakCards[]}`, у карты — `[{archetype, mulliganImpact, drawnImpact,
  keptImpact, drawnCount}]`. Значения — сдвиг винрейта колоды в п.п. по данным
  HSGuru `/v1/hsguru/mulligan`;
- `core` (карты Стандарта) — архетипы HSReplay Legend, где карта стоит в ≥50%
  колод: `[{archetype, cardShare, winrate, popularity}]`.

Верхний уровень `RadarResult` — новые поля:

- `timing` — `{today, lastExpansion, lastExpansionDate, daysSinceExpansion,
  lastContentDrop, lastBalancePatchDate, daysSinceLastBalancePatch,
  balancePatchSinceExpansion, medianDaysToFirstPatchAfterExpansion,
  medianDaysBetweenBalancePatches, nextPatchWithin7Days, nextPatchWithin14Days,
  basis, outlook}`. Вероятности — эмпирические, со сглаживанием Лапласа;
  `outlook` пишет модель;
- `changesSincePrevious` — `{previousRunId, previousGeneratedAt, added[],
  removed[], risen[], fallen[]}`; у элементов `{side, ref, kind, name,
  confidence, previousConfidence?, delta?}`; `risen` и `fallen` — до 10 самых
  больших сдвигов;
- `counts` — `{nerfs: {total, card, archetype, class}, buffs: {...}}`;
- `history.calibration` — `{samples, patches, nerfOverall, buffOverall}`;
- `history.backtest` — сводка бэктеста.

**Заявления разработчиков.** Помечаются посты с флером Blizzard на Reddit
(флер поста или автора: Blizzard, Game Designer, Developer, Community
Manager), посты @PlayHearthstone и аккаунтов из `RADAR_X_DEV_ACCOUNTS`.
Отдельный поиск `flair:Blizzard` по r/hearthstone добавляет один запрос за
прогон. Такие посты передаются модели отдельным блоком
`developerStatements` с повышенным весом; ответы саппорта («known issue»)
отфильтровываются. У постов в `topPosts` и `quotes` есть `authorRole`:
`official`, `developer` или `null`.

### Русский текст, переводы постов и редактор

**Только русские названия.**

- Архетипы называются так же, как на HearthPulse: словарь сайта лежит в
  `app/balance_radar/data/archetype_labels_ru.json` (экспорт админ-таблицы
  поверх `fallbackTranslations.ts`). Сопоставление тоже как на сайте: точное
  имя, иначе самый длинный вложенный ключ плюс непокрытые слова; если такие
  слова — название карты, они заменяются её ruRU-названием.
- Архетипы, которых нет в словаре, один раз переводит Claude по ruRU-названиям
  карт. Переводы хранятся в `<data>/balance-radar-names.json`.
- Карты называются по ruRU-локализации HearthstoneJSON.
- У архетипа `name` теперь русский, `nameEn` — имя HSGuru, `id` и `ref` по-прежнему
  английские. Модель получает глоссарий «English = Русский» и запрет на
  английские названия, скобки с ними и транслитерацию.

**Чистка текстов.** Все тексты для читателя проходят детерминированную
чистку:

- названия компаний и сервисов (Blizzard, HSReplay, HSGuru, Reddit и другие
  слова из `ALLOWED_LATIN`) не участвуют в замене имён, даже если есть карта с
  таким же английским названием: заклинание мага Blizzard не превращает
  компанию в «Снежную бурю». Если модель сама называет компанию «Снежной бурей»
  как субъект высказывания («сообщила», «отметила», «может» и т. п.), чистка
  возвращает «Blizzard»;

- английские имена заменяются русскими: кандидаты, прецеденты и архетипы из
  глоссария прогона, а также все коллекционные карты (ruRU) и игровые термины
  (Rush → Натиск, Mulligan → муллиган);
- транслитерации названий («Блазинг Инвокейшн») заменяются русским
  названием: кириллическая фраза сравнивается с фонетической транслитерацией
  английского имени (сходство не меньше 0,8, для одного слова не меньше 0,9),
  а фрагменты настоящих русских названий защищены от замены;
- контекст истории (обзор и сводки патчей с английскими названиями) переводится
  на русские имена до отправки модели, прецеденты передаются с ruRU-именами
  карт;
- английские дубли в скобках убираются;
- ранги переводятся: Алмаз, Легенда (никакого «Даймонд»);
- служебные идентификаторы удаляются: пути через точку (`history.patterns`),
  camelCase и snake_case. Версии патчей вроде 36.2.2 не трогаются.

Чистятся `summary`, `headline`, `verdict`, `reasons`, `keyNumbers`,
`metaImpact`, `developerSignal`, `social.summary`, `predictedChanges.change` и
`timing.outlook`. Что пришлось чистить и какие латинские слова остались,
записывается в `usage.textIssues`.

**Разметка упоминаний.**

- У кандидата `textEntities[]` — `{field, start, end, text, ref, kind, id,
  dbfId}`. `field` — `headline`, `verdict`, `reasons.N`, `keyNumbers.N` или
  `social.summary`.
- На верхнем уровне — `summaryEntities[]` без `field`.
- Склонённые формы тоже находятся («Ритуала призыва» → `card:BE_085`).
- `start` и `end` — индексы кодовых точек строки, по ним сайт подставляет
  ссылки и подписи.

**Краткость.** `summary` — 2–3 предложения, `verdict` — 1–2, `headline` — до
10 слов, `reasons` — 2–3 пункта до 12 слов, `keyNumbers` — до 3 строк.

**Переводы постов.**

- У каждого поста в `topPosts` (теперь до 3 на кандидата) есть `titleRu` и
  `textRu` — полный перевод заголовка и всего текста — и `text`, полный
  оригинал до 4000 символов. Ссылка, автор, сабреддит, score, комментарии и
  дата — как раньше.
- У цитат (`quotes[]`) есть `textRu`, а `text` остаётся дословным оригиналом.
- Переводит Claude пакетами по 25 постов с глоссарием прогона. Кэш —
  `<data>/cache/balance-radar/translations.json` по id и хэшу текста, так что
  повторный прогон платит только за новые посты. Расход — в
  `usage.translation`.

**Editor Team** (контракт — `docs/RADAR_EDITOR_CONTRACT.md` в ManacostTeam).

- **Запрос.** `POST {RADAR_EDITOR_URL}/v2/edit`: режим `ANALYSIS`, профиль
  `news`, задание (только русские названия по глоссарию, ранги, без служебных
  слов, короче, один абзац) и глоссарий текста как `source_snapshots`
  (`radar-glossary` с sha256).
- **Что редактируется.** `summary` и вердикты `RADAR_EDITOR_MAX_VERDICTS`
  самых уверенных кандидатов (по умолчанию 3). Причины и переводы постов
  через редактор не идут: контракт не рекомендует гонять короткие строки, а
  полный цикл стоит около $0,25 за текст.
- **Нагрузка.** Предпроверка `GET /health` (5 с), до 4 запросов параллельно,
  600 с на запрос, 15 минут на весь этап.
- **Когда правка принимается.** Только при `accepted && checks_complete &&
  fact_check_passed` и непустом тексте; иначе остаётся текст радара. Редактор
  никогда не блокирует прогон.
- **Что сохраняется.** У кандидата `editor` {applied, status,
  rejectionReasons, promptVersion, seconds, requestId?} и `original.verdict`;
  на верхнем уровне `editor` (отчёт этапа) и `original.summary`.
- **Сеть.** Контейнер подключён к docker-сети `editorteam-clients`,
  `RADAR_EDITOR_URL` по умолчанию `http://editorteam:8080`. Чтобы выключить,
  задайте на хосте пустой `RADAR_EDITOR_URL=`.

### Калибровка уверенности

По каждому балансному патчу со статистикой берутся все архетипы HSReplay
Legend за неделю до патча и помечается, задел ли их патч (изменённая карта
стоит в ≥50% колод архетипа). Так получилась таблица «винрейт × популярность →
доля нерфов/баффов» (`patterns.calibration`). На 2026-10-25 в ней 389
наблюдений по 9 патчам. Примеры:

| Винрейт и популярность | Нерф в следующем патче |
| --- | --- |
| <50%, любая популярность | 2,8% (9 из 321) |
| 50–52% | 16% (5 из 31) |
| 52–54% | 22% (6 из 27) |
| 52–54% при популярности ≥8% | 45% (5 из 11) |
| любая, популярность ≥8% | 26% (9 из 34) |

Кандидату достаётся ячейка таблицы по его признакам: для архетипа — HSGuru
Legend, для карты — её core-архетип HSReplay. Если в ячейке меньше 4
наблюдений, берётся строка только по винрейту, затем только по популярности,
затем общая частота. Модель получает эту долю как справку о прошлых решениях
Blizzard, но ранжирует по совокупности сигналов: бэктест показал, что жёсткий
якорь на калибровку ухудшает прогноз (см. ниже). Вероятность патча в
ближайшие 7 и 14 дней считается по срокам прошлых патчей; `withPatchIn14Days`
— доля из таблицы, умноженная на вероятность патча за 14 дней.

### Бэктест

`python -m app.balance_radar backtest [--llm]` берёт каждый прошлый балансный
патч, для которого есть локальная статистика (сейчас 8, от 35.2.2 до 37.0.0),
собирает входы только из данных, датированных до патча, и сравнивает прогноз
с реальными правками. «Попадание» — архетип, ключевая карта которого
изменилась; на уровне карт — сама изменённая карта.

Результаты 2026-10-25: 8 патчей, 22 понерфленных и 11 забаффанных архетипов,
352 архетипо-недели для вероятностных метрик.

| Предсказатель | Нерфы P@5 / R@5 | Нерфы P@10 / R@10 | Баффы R@10 | Карты-нерфы R@10 | Brier (нерф) | Brier skill |
| --- | --- | --- | --- | --- | --- | --- |
| Статистика (калибровка, leave-one-patch-out) | 0,275 / 0,50 | 0,163 / 0,59 | 0,18 | — | 0,0548 | 0,064 |
| Claude, калибровка как справка (**боевой промпт**) | 0,275 / 0,50 | 0,213 / 0,59 | 0,455 | 0,29 | **0,0531** | **0,094** |
| Claude, без калибровки у архетипов (промпт v2, повтор) | 0,25 / 0,455 | 0,228 / 0,59 | 0,36 | 0,29 | 0,0561 | 0,042 |
| Claude, жёсткий якорь на калибровку | 0,237 / 0,41 | 0,208 / 0,50 | 0,18 | 0,00 | 0,0535 | 0,088 |
| Claude, промпт v2, первый запуск | 0,325 / 0,59 | 0,27 / 0,77 | 0,455 | 0,44 | — | — |

Надёжность боевого варианта (нерфы):

| Названная вероятность | Прогнозов | Средняя названная | Сбылось |
| --- | --- | --- | --- |
| 0–10% | 298 | 0,3% | 3,4% |
| 10–25% | 33 | 13,8% | 15,2% |
| 25–50% | 11 | 33,3% | 18,2% |
| 50–75% | 10 | 57,1% | 50,0% |

Что из этого следует:

- Между запусками Claude точность колеблется на ±0,04–0,05 P@5 (2 попадания
  из 22), поэтому таблица сравнивает варианты честно, а не по одному удачному
  прогону.
- На ранжировании нерфов архетипов Claude и статистика сравнимы. Claude
  заметно лучше на баффах (R@10 0,455 против 0,18) и умеет называть
  конкретные карты. Его вероятности откалиброваны лучше всех (Brier skill
  0,094): когда он называет 50–75%, нерф случался в половине случаев.
- Жёсткий якорь на калибровку сделал модель копией статистики и ухудшил
  ранжирование. Поэтому в боевом промпте калибровка — справка, а не якорь.

Базовая частота нерфа архетипа — 5,7%, так что precision@5 около 0,25–0,3 —
это в 4–5 раз лучше случайного выбора. Claude мог видеть часть этих
патч-ноутов при обучении, поэтому его строки — оценка сверху; у
статистического предсказателя утечки нет.

Базовая частота нерфа архетипа — 5,7%, так что precision@5 около 0,3 — это
примерно в пять раз лучше случайного выбора. Баффы по статистике архетипов
почти не угадываются: Blizzard баффает отдельные непопулярные карты. Claude мог
видеть часть этих патч-ноутов при обучении, поэтому его строка — оценка
сверху. У статистического предсказателя такой утечки нет. Статистический
бэктест пересчитывается бесплатно при каждой пересборке истории.
LLM-бэктест стоит около $0,25–0,27 за 8 патчей. Он запускается автоматически
после пересборки истории, если история изменилась (`RADAR_BACKTEST_LLM=0`
выключает), и вручную через `backtest --llm`. Ответ
`/backtest`: `{data: {generatedAt, historyGeneratedAt, window, statistical,
model}, meta}`. У каждого предсказателя есть `{patches, nerf, buff,
nerfCards?, buffCards?, probability, perPatch[]}`. Метрики — по `@3`, `@5`,
`@10` (`precision`, `recall`, `hits`, `predicted`, `actual`). `probability`
— это `{nerf, buff}` с полями `{brier, brierSkill, baseRate, predictions,
reliability: [{band, predictions, meanPredicted, observedRate}]}`. Brier
считается по всем архетипам недели: архетипы, которых нет в списке, получают
вероятность 0.

**`RadarResult.accuracy`** — то же для страницы:

- `text` — готовая фраза по-русски;
- `basis` (`model` или `statistical`) и `caveat`;
- `statistical` и `model` — {patches, nerf@5, nerf@10, buff@5, buff@10,
  nerfCards@10, nerfBrier, nerfBrierSkill, nerfReliability};
- `url`.

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

## История патчей и закономерности Blizzard

Прогноз радара опирается на прошлые решения Blizzard. Датасет истории
(`<data>/balance-radar-history.json`) собирается так:

- **Правки карт.** Сравниваются соседние сборки клиента в HearthstoneJSON:
  `cost`, `attack`, `health`, `durability`, `armor` и текст каждой
  коллекционной карты. Разметка текста и служебные хвосты (`&`, `_`, `~…`)
  отбрасываются, перенос прочности оружия между полями не считается правкой.
  Сборка относится к патчу с таким же номером (последняя часть версии,
  например `36.6.3.253932`). Покрытие начинается с самой ранней сборки
  HearthstoneJSON (сейчас 28.2, декабрь 2023).
- **Патчи.** Даты, названия, ссылки и русский комментарий разработчиков берутся
  из таблицы `hearthstone_patches`. Это тот же источник `/api/patches`, из
  которого страница /standard/patches на HearthPulse берёт патчи (wiki.gg +
  playhearthstone.com + hs-manacost.ru).
- **Направление правки.** Нерф или бафф определяется по числам: стоимость
  выросла или статы упали — нерф, наоборот — бафф. Текстовые и смешанные правки
  размечает Claude пакетами по 120: `nerf`, `buff`, `rework` (механику изменили
  без явного усиления или ослабления) или `neutral` (поменялась только
  формулировка). Метки неизменённых правок переиспользуются, поэтому новый патч
  стоит нескольких меток, а не повторной разметки всей истории.
- **Тайминг.** Выход набора — это первая сборка, где появились его карты; с
  ним сравнивается `setAgeDays` каждой правки. «Выход контента» — это патч
  минимум с 3 новыми картами: `expansion` для патчей `N.0.0`, иначе мини-набор
  или новые карты.
- **Статистика до и после патча.** Ключевой архетип правки — архетип HSReplay
  Legend, в котором карта стоит не меньше чем в 50% игр его колод. Для него
  берутся средние винрейт и популярность за 7 дней до и 7 дней после патча.
  Локальные ряды есть с 2026-04-23 (`coverage.statsFrom`), у более ранних
  правок `preStats` равно `null`.
- **Закономерности.** Цифры считает код (`patterns`): число балансных патчей
  по годам; интервалы между ними; дни от выхода дополнения или мини-набора до
  первого балансного патча; типы правок и типичные сдвиги стоимости, атаки и
  здоровья; классы и редкости; возраст набора; пороги винрейта и популярности
  перед нерфами и баффами; эффект правки; сильные популярные колоды, которые
  патч не тронул (`sparedArchetypes`). Обзор на русском
  (`narrative.summary`, `narrative.patterns[]`) пишет Claude по уже
  посчитанным числам.

Датасет пересобирается в начале прогона радара, если его нет, ему больше
недели или в каталоге появился новый патч. Вручную: `python -m app.balance_radar
history [--force] [--no-label]`. Если пересборка не удалась, остаётся прежний
файл.

### Как история попадает в прогноз

Для каждого кандидата из пулов pipeline подбирает до трёх прошлых правок.
Признаки сходства: та же карта (`same_card`), тот же архетип (`same_archetype`),
тот же класс (`same_class`), винрейт ключевого архетипа в пределах ±3 п.п.
(`similar_winrate`), популярность в пределах 0,5–2× (`similar_popularity`),
новая карта (`new_set_card`). Подобранные правки вместе со сводкой
закономерностей передаются Claude. Модель сверяет кандидата с порогами и
сроками, называет похожие случаи в `verdict` и перечисляет их id в
`precedent_ids`. Неизвестные id отбрасываются. В ответ кандидата попадают
процитированные моделью прецеденты (`citedByModel=true`), остальные места до
трёх добиваются ближайшими совпадениями.

### `RadarCandidate.precedents[]`

| Поле | Тип | Описание |
| --- | --- | --- |
| `id` | string | `precedent:<версия патча>:<cardId>` |
| `patch`, `date` | string | Версия и дата патча. |
| `cardId`, `dbfId`, `name`, `nameEn`, `class` | — | Изменённая карта (`name` по-русски). |
| `direction` | `nerf \| buff \| rework \| neutral` | Итог правки. |
| `changes` | `{field, before, after}[]` | Числовые поля: `cost`, `attack`, `health`, `durability`, `armor`. |
| `textBefore`, `textAfter` | string \| null | Текст до и после, если он менялся. |
| `summary` | string \| null | Что изменилось, по-русски (для размеченных моделью правок). |
| `setAgeDays` | int \| null | Сколько дней набору карты было на момент патча (`null` — набор старше покрытия). |
| `preStats`, `postStats` | object \| null | `{archetype, winrate, popularity}` ключевого архетипа за неделю до и после патча. |
| `url` | string \| null | Официальные патч-ноуты (или wiki.gg, hs-manacost.ru). |
| `similarity` | string[] | Почему правка похожа на кандидата. |
| `citedByModel` | bool | Модель сослалась на этот случай. |

### `RadarResult.history`

`{generatedAt, coverage, balancePatches, summary, patterns: [{title, detail}],
historyUrl, patternsUrl}` — краткая выжимка для страницы. У прогонов, сделанных
до появления истории, поле равно `null`.

### `GET /v1/constructed/balance-radar/history`

Параметры:

- `card` — `cardId`, `dbfId` или часть названия на любом языке;
- `direction` — `nerf | buff | rework | neutral | mixed`;
- `class` — например `WARLOCK`;
- `since` — дата `YYYY-MM-DD`;
- `limit` — от 1 до 100, по умолчанию 20; `offset`.

Ответ: `{data: {generatedAt, coverage, contentDrops[], patches[]}, meta}`.
Патчи идут от новых к старым, `meta.count` — число патчей после фильтров.
Каждый патч содержит:

- `version`, `displayVersion`, `build`, `date`, `title`, `titleRu`;
- ссылки `officialUrl`, `wikiUrl`, `manacostUrl`;
- `devComment` — комментарий разработчиков по-русски, до 900 символов;
- `summary` и `targetedArchetypes` от Claude;
- `counts`, `lastContentDrop`, `daysSinceContentDrop`;
- `metaBefore` — топ-10 архетипов HSReplay Legend за неделю до патча;
- `changes[]`: `id`, `cardId`, `dbfId`, `name`, `nameEn`, `class`, `rarity`,
  `set`, `type`, `direction`, `labelledDirection`, `fields[]`, `textBefore`,
  `textAfter`, `summary`, `setReleasedAt`, `setAgeDays`, `archetypes[]`.
  Каждый элемент `archetypes[]` — это `{archetype, archetypeId, class,
  cardShare, before, after, deckListsAt}`.

### `GET /v1/constructed/balance-radar/patterns`

Ответ: `{data: {generatedAt, coverage, patterns, narrative, model}, meta}`.
`patterns` содержит посчитанные числа (пороги — в виде квантилей `{n, min,
p25, median, p75, max}`), `narrative` — обзор на русском. Пока датасет не
собран, оба эндпоинта отвечают `503`.

## Запуск и расписание

- Таймер `hs-data-api-docker-balance-radar.timer` срабатывает в 03:10 и 15:10
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
| `RADAR_EDITOR_URL` | Адрес Editor Team; в compose по умолчанию `http://editorteam:8080`, пусто — без редактора. |
| `RADAR_EDITOR_MAX_VERDICTS` | Сколько вердиктов на формат гонять через редактор (по умолчанию 3, плюс `summary`). |
| `RADAR_BACKTEST_LLM` | `0` выключает автоматический LLM-бэктест (около $0,3) после пересборки истории. |
| `RADAR_X_DEV_ACCOUNTS` | Необязательно: аккаунты разработчиков в X через запятую; их посты идут в `developerStatements`. |

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

Оценка полного прогона (оба формата) по прогонам 2026-10-25 (v3: русские
тексты, переводы, редактор):

| Статья | Запросы / токены | Цена |
| --- | --- | --- |
| RedditAPIs | ~24 чтения (с поиском `flair:Blizzard`) | ~$0.036 |
| GetXAPI | ~7 страниц поиска | ~$0.007 |
| Claude, анализ Стандарта | ~190K вход + ~17K выход | ~$0.60 |
| Claude, анализ Вольного | ~150K вход + ~14K выход | ~$0.48 |
| Claude, переводы постов | 50–70 постов; первый прогон $0.51, с кэшем $0.21 | ~$0.21 |
| Editor Team, `summary` + 3 вердикта на формат | 8 текстов, 37 вызовов модели | ~$1.73 |
| **Итого с редактором** | | **~$3.1** |
| **Итого без редактора** | | **~$1.34** |

Два прогона в день стоят около $6.2 в день с редактором и около $2.7 без него.
Редактор принял 5 правок из 22 при 10 вердиктах на формат ($4.77 за прогон) и
1 из 8 при 3 вердиктах ($1.73). В основном правки отклонялись как
`no_measurable_improvement`: короткие русские тексты радара он почти не
улучшает. `RADAR_EDITOR_MAX_VERDICTS=0` оставляет только `summary` (около
$0.4 за прогон), пустой `RADAR_EDITOR_URL=` выключает редактор совсем.
Анализ одного формата занимает около 2,5–3 минут. История
патчей собирается отдельно. Первая полная сборка стоит около $0.68: 4 пакета
разметки (~96K вход + ~31K выход, $0.51) и обзор (~50K вход + ~7K выход,
$0.18). Потом при каждом новом патче размечаются только новые правки и
переписывается обзор, около $0.2. Сборки HearthstoneJSON скачиваются один
раз (~300 МБ) и хранятся в `<data>/cache/balance-radar/hsjson` в сжатом
виде. Бесплатные
вызовы `/account/me` в конце прогона сохраняют балансы обоих провайдеров в
сводку CLI.
