# Changelog

## Unreleased

- Панель, статистика (E23 #109): у Полей сражений и Арены те же приёмы, что у
  меты. «Герои»: «Топ-10 по выбору» (портрет, тир, выбор, среднее место, дата
  данных) и пузырьковый график «Выбор × среднее место» (цвет — тир, размер —
  игры). «Существа»: «Топ-12 по популярности» карточками с картой, уровнем
  таверны чипом и счётчиками уровней; пока картинка грузится или её нет, видны
  название и уровень, пустых квадратов нет. «Арена → Классы»: столбцы винрейта
  классов вокруг линии 50 % с теми же `ClassIcon`, что в мете (на телефоне —
  строки). Фильтр «Уровень таверны» у существ — список «Все / 1…7» (целое
  с малым диапазоном и без значения по умолчанию в `lib/stats-view.ts` теперь
  список). Только панель, API и `Thumb` не менялись.
- Панель, статистика (E23 #107, доводка): иконки классов не грузились на проде —
  nginx отдаёт панели только `/`, `/api/panel` и `/_next/`, а файлы лежали в
  `public/class-icons/` (404). Иконки перенесены в `panel-next/assets/class-icons/`
  и импортируются (`components/class-icons.ts`), Next отдаёт их из
  `/_next/static/media/`. `tests/stats-browser.mjs` проверяет, что иконки
  загружаются и лежат под `/_next/static/`. nginx не менялся.
- Панель, статистика (E23 #107): над таблицей меты Стандарта и Вольного и каталога
  архетипов HSGuru — «Топ-10 по доле» (иконка класса, русское название и
  английское второй строкой, доля, винрейт, игры, дата данных) и пузырьковый
  график «Доля × винрейт» (цвет — класс, размер — игры, подсказка с архетипом,
  долей, винрейтом и играми, табличная альтернатива для скринридеров). Графики
  вендорены из ValyroUI (`scatter-chart`, `bar-chart`, общие `charts/*`), иконки
  классов — арт HearthPulse в `panel-next/assets/class-icons/`. Русские названия
  берутся из `app/balance_radar/data/archetype_labels_ru.json` (копия в
  `lib/vendor/`, `scripts/sync-models.mjs`), остальные остаются английскими.
  Колонка «Архетип» в таблицах (мета, архетипы HSGuru, колоды) и в деталях —
  иконка класса, русское название и английское второй строкой.
  Класс — из `class_name`, запасной путь — окончание названия. Клиентский JS
  страницы: +11,4 КБ gzip (287,3 → 298,7 КБ), CSS +2,8 КБ gzip.
- Панель, язык карт (E23 #145, доводка #111): закрытие карточки больше не
  перезаписывает сохранённый язык (ссылка с `?lang=es` его не меняет);
  строка «Не удалось загрузить перевод…» исчезает, когда перевод загрузился
  при возврате на язык; неизвестный `?lang=xx` при открытой карточке даёт
  русский, а не сохранённый язык; отменённый запрос больше не выбрасывает
  из пула более новый запрос той же карты (карта по-прежнему запрашивается
  один раз). Только панель, API не менялся.
- Панель, статистика (E23 #105): фильтры — списки с русскими подписями
  («Формат», «Ранг», «Период» с текущим патчем: «24 часа», «Патч 37.0.2»,
  «Легенда», «Алмаз 4–1», «Стандарт»), без кнопки «Применить». Поля «Записей» и
  «Смещение» убраны: на сервере листаются только «Колоды» и «Патчи» (там PHP
  применяет `offset`, а строк больше лимита) — Valyro `Pagination` по 25 строк
  («1–25 из 812», страница в `page`); остальные модули берут всё одним ответом,
  таблица листает на клиенте, а если строк больше лимита, подпись честная:
  «Показаны первые N из T». Фильтр сбрасывает страницу, «Сбросить» виден только
  при отличии от умолчаний. Проценты везде одного вида `58,5 %`
  (строки `"7.98%"` разобраны, доли меньше 1 % — `0,45 %`), числа с неразрывными
  пробелами. KPI из `summary` («Данные на», «Игр в выборке», счётчик), у старого
  PHP считаются на панели; при частичном ответе (страница или первые строки)
  счётчик — полное `meta.total`, а суммы по строкам («Игр в выборке», «Со
  сборками», «Показано») не показываются, чтобы карточки не менялись при
  листании; выбранные фильтры не повторяются. «Статистика карты»
  без названия показывает поле поиска и подсказку. Только `panel-next`, API и
  PHP не менялись; `tests/stats-browser.mjs` обходит все 13 модулей.
- Панель, таблицы статистики (срез S3c эпика E23 #106): подробности строки
  открываются в боковой панели справа (Valyro `Drawer`, вендорен из локального
  релиза кита) вместо модального окна: фокус уходит внутрь, Escape и крестик
  закрывают, фокус возвращается на строку; на 390 панель на всю ширину. В
  таблице нет последней колонки «Детали»: подробности открывает кнопка-название
  в первой колонке или клик по строке. Первая колонка закреплена при
  горизонтальной прокрутке, числа выровнены вправо с `tabular-nums`, длинные
  ID переносятся и не наезжают на соседние колонки. Пропсы `DataTable` и
  `RecordDetails` не менялись («Источники» используют ту же панель). Только
  панель, API не менялся.
- Панель, навигация статистики (E23 #103): в меню «Статистика» с пятью
  группами (Стандарт и Вольный, Поля сражений, Арена, Карты, Патчи), а
  «Реестр источников» и «Провайдеры» перенесены в «Мониторинг» к «Источникам».
  Внутри группы модули переключаются вкладками (Valyro `Tabs`) вместо
  плоского списка из 13 пунктов; `/?action=analytics` без `stats` открывает
  «Мету», старые ссылки `stats=<key>` работают, хлебные крошки и подсветка
  совпадают с меню. Модуль реестра вне карты `lib/stats-sections.ts` попадает
  в «Прочее». Ярлык Ctrl+K «Статистика: требуют внимания» ведёт на реестр.
  Реестр источников: длинные id переносятся, описание — до двух строк, на
  1280 и 1440 колонки не обрезаны. Только панель, API не менялся.
- Панель, статистика (срез S5a эпика E23, #108): у существ Полей сражений
  `image_url` — абсолютный адрес из каталога (`uploads/framed`, затем
  `uploads/cards`) и только если файл есть, иначе пустая строка; у героев BG
  портрет есть и в режиме `duos`; строки `arena` и `arena_cards` получили
  `class_slug` — имя иконки класса HeartPulse (11 классов, неизвестный — пустая
  строка). Поля только добавлены. Тест `panel/tests/analytics_bg_images_test.php`.
  Исправлено (#147): «Герои» в режиме `duos` больше не шлют пустой `q=` (API
  отвечал 422, панель — 502); картинки «Карт Арены» берутся из render-адреса
  HearthstoneJSON (адрес из источника отдавал 404).
- Панель, статистика (срез S3b эпика E23): ответ модулей пополнен — `options`
  со списками значений (периоды меты из API с текущим патчем и подсказкой
  `option_hints.period`), конечные наборы `meta.min_games`,
  `archetypes.rank_range` и `archetypes.game_type` стали `enum`, неизвестный
  период меты заменяется на `past_day` вместо ошибки. KPI вместо повторов
  фильтров: «Данные на», «Игр в выборке» и число записей, у каждого `key` и
  `type`. Строки меты, архетипов и героев Полей сражений получили числа
  `share_pct` / `winrate_pct` и идут по убыванию доли; в строках меты есть
  `class_name` из каталога HSGuru. Старые поля не менялись. Тест
  `panel/tests/analytics_model_test.php`, снимок реестра перегенерирован.
- Источник `hsreplay_assemble`: в русских текстах карт убран служебный маркер
  переноса `[d]` внутри слов (10 карт, например `TIME_024`, `TIME_001`,
  `END_016`); слово склеивается, серия неразрывных пробелов после маркера
  не оставляет разрыва («становится», а не «ста новится»). Refs #136.
- Источник `hsreplay_assemble` и REST `GET /v1/hsreplay/assemble` /
  `GET /v1/hsreplay/assemble/cards`: механика «Собирает» (Assemble, 13 карт
  Reign of the Black Empire). Один запрос HSReplay `assemble_choices_by_middle`
  на формат (Стандарт, Вольный; Scrape.do без рендера, 1 кредит) даёт для каждой
  карты базы с `offer_rate`, двух доноров и выбор их половин (эффект или
  условие): «берут» и винрейт. Собранный текст варианта строится из базы карт
  без сети (`app/assemble_pools.py`, ru и en; `\&` не разделитель, ведущие
  ключевые слова отбрасываются). Снимок формата публикуется после пяти проверок,
  иначе остаётся прошлый (`fetch_error` с причиной). Таймер
  `hs-data-api-docker-refresh-hsreplay-assemble.timer` каждые 6 часов, не больше
  6 кредитов в сутки UTC (`HS_ASSEMBLE_SCRAPE_DO_DAILY_CREDITS`). Ответы не
  кэшируются по ETag. `CardBase` получил `texts_ru`. Документация:
  `docs/HSREPLAY_ASSEMBLE.md`. Refs #136.
- Загрузчик рендеров карт: оборванный ответ CDN (`IncompleteRead` и другие
  `http.client.HTTPException`) считается ошибкой апстрима, как таймаут или 5xx,
  и больше не роняет весь прогон; id повторяется в следующем. Refs #131.
- Отчёт покрытия `card_locale_coverage.py` (E24, срез #123): опция
  `--renders-dir` добавляет таблицу «Рендеры: локаль × режим» (есть,
  ожидается, 404 у HSJ, ещё не скачано, %) по индексам загрузчика рендеров;
  в `--json` — блок `renders`. Файлы картинок не читаются. Без опции вывод
  не изменился.
- Каталог карт на 4 языках (E24, срез рендеров): при `?locale=` блок
  `localized` в `/api/v1/card-catalog`, `/{id}` и `/dbf/{dbf}` получает
  `render_url` и `render_media_verified`. Скачанный рендер — ссылка на наш
  `uploads/card-renders/{locale}/512x/{id}.webp?v=<хеш>` и `true`; ещё не
  скачанный — ссылка HSJ той же локали (для MINION, SPELL, WEAPON, HERO,
  HERO_POWER, LOCATION) и `false`; нет рендера (404 HSJ, ENCHANTMENT, PET) —
  `null`. Источник — индекс загрузчика `var/card-renders/{locale}.index.json`;
  нет или битый индекс — как «не скачан». Без `?locale=` ответы, `images.card`
  и `API_VERSION` не меняются. `Last-Modified` учитывает время индекса.
- История карт: синк связанных карт (`sync_constructed_related_cards.py`) больше
  не перезаписывает непустые `local_image_url` и `local_crop_image_url`; поле
  принадлежит зеркалу картинок. Раньше два писателя меняли путь каждый день
  (`constructed-related` ↔ `card-history-media`, ~79 карт в сутки), и каждая
  смена была ложной версией. Триггеры и хэш версии не менялись, записанные
  версии не удалялись (#100).
- Панель, «Статистика карты»: страница открывается без экрана ошибки
  (подсказка «Введите название карты»), кнопка «Статистика» в карточке
  записи передаёт карту в поле `card_name`. Модуль, который ответил ошибкой
  4xx/5xx, показывает её текст внутри своей страницы, меню статистики на месте.
  Смоук (`npm run test:http`) открывает все 13 модулей на снимке реестра
  `panel-next/tests/fixtures/analytics-registry.json`; `panel-check` падает,
  если PHP-реестр и снимок разошлись. Новый `panel/tests/analytics_cli.php`
  (`--registry`, `--smoke`, `--module`) — только чтение, не деплоится.

- Фан-колоды `concept-v8` (api#129): кандидат сравнивается со всем каталогом HSGuru
  (списки от 45 игр) по формату и классу, а не с топ-350. Ладдерные списки
  отсекаются раньше любых бонусов и независимо от концепт-слов и пакетов
  (`ladder_member_gate` ≥ 0.55, `ladder_title_gate`, `generic_ladder_gate` без
  условия по сходству). В строках появились `kind` (`brew` / `off_meta`),
  `ladder_sim`, `content_sim`, `twin_sim`, `content_archetype`; новые коды причин
  `unique_list`, `far_from_ladder`. Оценка — сумма весов 1.0, без потолка 1.00.
  Новые env: `HS_FUN_DECK_LADDER_SHARE_PCT`, `HS_FUN_DECK_LADDER_MEMBER_SIM`,
  `HS_FUN_DECK_TWIN_SIM`; `HS_FUN_DECK_MIN_SCORE` по умолчанию 0.35 (убрать
  `0.55` из серверного env), `HS_FUN_DECK_MAX_META_SIMILARITY` и
  `HS_FUN_DECK_GENERIC_NAME_MAX_SIMILARITY` удалены. `compute_fun_decks` —
  чистое вычисление без записи; `scripts/fun-decks-eval.py` — пробный прогон
  только на чтение. После выкладки нужен полный `refresh-fun-decks` без
  `--format`: история `concept-v7` отбрасывается.
  Пакет — фан-сигнал, только если он редок в формате
  (`HS_FUN_DECK_SPICY_PACKAGE_MAX_SHARE_PCT`, 3%); архетип ладдерный и по
  популярности ≥ 1% в мета-источниках того же формата и класса; член своего
  архетипа не «уникальный список»; свежий отказ вычёркивает колоду из истории.
  Слово названия, подкреплённое метовым пакетом (Yogg в Standard), не концепт;
  общие названия проверяются без приставок XL/HL/рун DK (XL Blood DK, XL Elemental Shaman). Нейтральные пакеты выдаются как `meta_pkg:<пакет>`
  (`pkg:<пакет>` — только острые, их показывает сайт).
- Панель, инспектор карты: текст карты с форматированием — «Боевой клич»
  жирным, курсив, переносы строк; теги, `[x]`, `$`, `#` и `_` не видны,
  разбор в `panel-next/lib/card-text.ts`, без `dangerouslySetInnerHTML`.
- Панель, «История изменений»: открывается на последнем настоящем изменении
  карты, а не на паре версий, отличающихся служебными ссылками на картинки;
  переключатель «Только изменения данных» (по умолчанию включён) скрывает
  служебные версии и поля (`local_*`, `source_hash`). Картинка «После» берётся
  из архива `image_url` версии, а не из `local_image_url`, который в момент
  изменения ещё указывал на старый файл (BE_036: «После» показывала 9 маны
  вместо 10). Картинки сравнения грузятся с высоким приоритетом, до загрузки —
  скелетон. Только панель, API не менялся.
- Панель `panel-next`: Next.js 16.4.3 → 16.4.6. С 2026-11-17 сборка на 16.4.3
  останавливалась на `SecurityFatalError` (GHSA-mfr8-8qww-v76h,
  GHSA-vc7v-qv4w-wrv8, обе moderate, утечка кэша `use cache` между root
  params), из-за чего не проходили `npm run check` и деплой панели.
- Каталог карт на 4 языках (E24, фаза 2): загрузчик
  `panel/scripts/sync_card_renders.py` и таймер `kolodahs-card-renders` (раз в час)
  скачивают рендеры HearthstoneJSON для `ruRU`, `enUS`, `plPL`, `esES` в WebP q85
  (`uploads/card-renders/{locale}/512x/{id}.webp`) и ведут манифест в
  `var/card-renders/` (`{locale}.index.json`, `state`, `last-run.json`). Сначала
  коллекционные карты, 3 запроса/с, до 6000 запросов и 40 минут за прогон, стоп при свободном месте
  меньше 20 ГБ, при SIGTERM индекс и state дописываются; WebP `method=4`
  (в 13 раз быстрее `method=6`, файл на 3,5% больше). API пока не меняется (поле — api#122). Юниты ставятся вручную,
  ранбук — `docs/card-renders.md`.
- Каталог карт на 4 языках (E24, срез данных): новая таблица
  `hearthstone_card_localizations` (`panel/sql/card_localizations.sql`) хранит
  для каждого id HearthstoneJSON полный объект карты в локалях `ruRU`, `enUS`,
  `plPL`, `esES` (в том числе токены и карты всех режимов) и зеркало Postgres
  `platform/sql/021_card_localizations.sql`. Её пишет суточный
  `sync_full_card_catalog.py` в одной транзакции с `hearthstone_card_catalog`;
  охрана публикации не пускает неполную локаль или набор id/dbf, отличающийся от
  enUS, и пишет только изменившиеся строки. Новые флаги `--hsj-pl`, `--hsj-es`
  и блок `localizations` в JSON-выводе. API не меняется. Порядок выкладки:
  `docs/full-card-catalog.md`, раздел «Publication and rollback».
- `panel/scripts`: в git перенесены прод-версии скриптов от 2026-11-04 (ручной
  патч прод-релиза `20261104T091515Z-auto-patch-final`), чтобы `deploy-panel.sh`
  не откатил их: `card_image_versions.py` (новый; изменённая картинка
  публикуется в `uploads/card-history-media/<sha256>.png`, старые версии не
  перезаписываются), хеш версии карты без локальных зеркал картинок и служебных
  полей, `install_card_history.py --upgrade-triggers --trigger-backup`, правки
  синков картинок и каталога конструкта, BG pool patch. Тесты приведены к
  прод-поведению. Поведение прода не меняется.
- `GET /api/v1/card-catalog`, `/card-catalog/{card_id}` и
  `/card-catalog/dbf/{dbf}` принимают `?locale=ruRU|enUS|plPL|esES` (регистр
  важен). С параметром у карты появляется блок `localized` (`locale`,
  `requested_locale`, `fallback`, `name`, `text`, `flavor`, `collection_text`,
  `how_to_earn`, `how_to_earn_golden`, `targeting_arrow_text`) из таблицы
  `hearthstone_card_localizations`. Нет строки локали или самой таблицы —
  тексты enUS, `fallback: true`, 200. Неизвестное значение — 400
  `invalid_parameter`. `q` ищет и по имени выбранной локали, `Last-Modified`
  учитывает строку локали. Без параметра ответ не меняется.
- Новый скрипт `panel/scripts/card_locale_coverage.py`: отчёт покрытия каталога
  карт «локаль × режим» (ruRU, enUS, plPL, esES) по таблице
  `hearthstone_card_localizations`, только чтение; Markdown и `--json`,
  режим «из файла». Карта покрыта, если строка есть и 7 полей `payload`
  совпадают с HSJ своей локали; «нет строки» и «устарела» считаются отдельно; карты BG с классическим
  `set` определяются по префиксу id; `--db` при ошибке даёт понятное сообщение.
  Описание: `docs/card-locale-coverage.md`.
- `GET /api/bg/heroes` со срезами `mmr`, отличными от `TOP_50_PERCENT`
  (читаются с HSReplay по запросу): сбой чтения больше не отвечает `500`.
  Последний удачный ответ каждого среза хранится на диске; при сбое он отдаётся
  с `stale: true` и своим `fetched_at`, без копии — `503` с `detail` и
  `Retry-After`. Копия моложе 3 часов отвечает без запроса к HSReplay, после
  сбоя срез не запрашивается 10 минут. Новое поле `stale` в ответах этих
  срезов; они идут с `Cache-Control: no-store`.
- Контракт ответа HSReplay для списка героев BG требует в первой строке только
  `hero_dbf_id` и `pick_rate`: в узком срезе у героя с малым числом игр
  статистика мест равна `null`, и такой герой бывает первым (Top 1%,
  2026-11-16: 16 из 90). Раньше исправный ответ отклонялся как `PARSE_FAIL` на
  всех каналах. Список, где статистики мест нет ни у одного героя, отклоняется
  при разборе.
- Трикеты Полей сражений (`hsreplay_battlegrounds_trinkets*`): если HSReplay
  явно отдаёт `null` вместо средней позиции и/или распределения мест (малая
  выборка), трикет больше не отбрасывается. Он остаётся в ответе с
  `avg_placement: null`, `placement_distribution: null`, `games: null` и
  объяснением в `field_availability` (`upstream_explicit_null`). Отсутствующие
  ключи и неверные значения по-прежнему отклоняются проверкой контракта.
- `GET /v1/battlegrounds/heroes/compositions?mmr=TOP_50_PERCENT`: композиции
  всех героев одним ответом из уже хранимого `hsreplay_battlegrounds_hero_details`
  (`composition_id`, `name`, `num_games`, `avg_placement`,
  `placement_distribution`) и `tribes` — ключи рас HearthstoneJSON по типу
  композиции из `hsreplay_battlegrounds_compositions`. Без составов досок,
  tavern-up и hero-power; срез, которого нет в хранилище, отвечает 422.
- `GET /v1/arena/cards?fields=…`: компактные строки
  `hsreplay_arena_cards_advanced` с выбранными полями (по умолчанию `id`,
  `score`, `tier`, `cardClass`, `win_rate`), фильтром `class` и пагинацией.
  `meta.game_type` (`underground`/`normal`) и `meta.time_range` называют срез.
- `GET /v1/hsreplay/mulligan/decks`: в строке новое поле `deck_list` — карты
  колоды парами `[dbfId, count]` (аддитивно; `null`, если списка нет).
- `GET /api/bg/heroes` и `GET /v1/battlegrounds/heroes` (solo, `TOP_50_PERCENT`):
  числа списка (`tier`, `pick_rate`, `pick_rate_value`, `avg_placement`,
  `placement_distribution`, `best_composition_id`) берутся из ежедневного
  `hsreplay_battlegrounds_heroes`, когда он новее пакета деталей и относится к
  тому же срезу. `fetched_at` теперь дата чисел списка; новые поля
  `list_fetched_at`, `details_fetched_at`, `list_source_id` (в v1 — в `meta`).
  Существующие поля строк и их типы не меняются. ETag этих путей учитывает оба
  набора.
- `hsguru_brawl`: опубликованный снимок без одной-двух второстепенных страниц
  из 35 больше не считается сбоем источника — сохранённое состояние `ok`,
  запуск по-прежнему `partial`, пропуски перечислены в `missing_slices`
  статуса. Любой неполный или неопубликованный запуск получает
  `failure_reason_code`, поэтому диагностика больше не показывает
  `unclassified_failure`.
- Диагностика здоровья: источник в подтверждённом ожидании публикации upstream
  (`vicious_syndicate_radars`, `upstream_publication_pending`) вынесен в новый
  список `upstream_pending_sources` и не влияет на `freshness_ok`/`degraded`.
  Другие состояния этого источника учитываются как раньше.
- `GET /v1/sources`: источник без dataset-файла
  (`hsreplay_battlegrounds_compositions_screenshot`) получает свежесть по своему
  статусу, а не `freshness_state: missing`.
- `GET /v1/hsguru/archetypes`: каталог текущего патча HSGuru
  (`period=patch_37.0.2`, номер берётся из последнего прогона матрицы, без
  правки кода) рядом с текущим окном и Bans. Новое поле
  `meta.current_patch_period` (аддитивно; `null`, если отдельного каталога
  патча нет), патч входит в `meta.available_periods`, прошлый патч отвечает 422.
  Ответы без `period` и с `black_empire_ban` не меняются. Джоба сборок
  дополнительно собирает all-rank сборки патча и добирает недостающие, сначала
  беря уже собранные сборки окна `black_empire` (без платных страниц), затем
  `past_30_days` и реплеи; формат без среза патча не собирается.
- Fun decks (`hsguru_fun_decks`, `DETECTOR_VERSION` `concept-v7`): a stricter,
  explainable selection. A published deck now needs `games` and `win_rate`, at
  least 30 games (`HS_FUN_DECK_MIN_GAMES`), and a 95% Wilson win-rate interval
  whose upper bound is not below 45% (`HS_FUN_DECK_MIN_CI_HIGH`): lists that
  are certainly losing are dropped (9 of the 46 published decks on
  2026-10-29), and `meme_low_winrate` now only marks decks that passed. Lists
  of one format and class with a card Jaccard of 0.6 or more
  (`HS_FUN_DECK_DEDUPE_JACCARD`) collapse into the better sampled one.
  Publication takes one deck per class first (per format), then fills by
  `fun_score` under the same class and XL/HL limits. Bare
  "<Aggro|Dragon|Dude|Secret|…> <class>" titles without a card package that
  sit close to a meta core are gated by `generic_ladder_gate`
  (`HS_FUN_DECK_GENERIC_NAME_MAX_SIMILARITY`, 0.3). Service prefixes ("STD",
  "WLD", "Wild") are removed from `title`. Rows gain `win_rate_ci_low`,
  `win_rate_ci_high`, `variants` and `source_title`; existing fields keep
  their meaning; a remembered deck is evicted from the history as soon as its
  fresh candidate of the same format and code fails the sample/win-rate gate,
  even if that candidate stopped being fun in the same run (it is counted
  once, in `rejected`; a `--format standard` run never evicts a Wild row);
  `variants` of the other format is kept (not reset) by a `--format
standard`/`wild` run, and decks sharing a title across classes or formats
  are never merged by the title dedupe. `structured.stats` adds
  `rejected_by_gate`, `quality_rejected`, `near_duplicates_absorbed`,
  `history_dropped` and `published_by_class`. The previous version's history
  is not carried over, so run `refresh-fun-decks` without `--format` after
  deploy. `docs/DATA_CATALOG.md` documents the new fields and every `reasons`
  code; `docs/SOURCES.md` has the new source description.

- Deploy: `scripts/deploy-server.sh` times each phase (fetch/pull,
  platform + migrations, image build, restart, health wait, units +
  exporter), prints `==> фаза …: N с` lines and a final total, and writes one
  summary line to journald under the tag `hs-data-api-deploy`
  (`commit`, `old`, `result=ok|rollback|fail`, `exit`, `failed_at`, every
  phase) on success, rollback and failure alike
  (`journalctl -t hs-data-api-deploy`). `--dry-run` is unchanged and writes
  no journal line. The health wait polls every 2 s within a 150 s _time_ budget
  (`HEALTH_TIMEOUT`, default `HEALTH_RETRIES x HEALTH_DELAY` = 75 x 2; was
  5 s x 30): the same budget and the same criterion (HTTP 200 from
  `/v1/health`), a started API is noticed up to 3 s sooner, and a hanging
  endpoint cannot stretch the wait beyond the budget (each request timeout
  and pause is capped by the remaining time). `rollback_api` and exit codes are
  unchanged. New `tests/test_deploy_server_timings.py` runs the whole script
  against stubbed docker/curl/systemctl/logger and covers the health-failure
  and exporter-failure rollbacks.

- Docker: the image no longer installs the Debian `npm` package (about 350
  `node-*` dependencies, 452 -> 102 apt packages). `npm ci` for
  `scripts/fingerprint-node` runs in a separate `node:18-bookworm-slim` build
  stage and only `node_modules` is copied into the final image; the runtime
  still uses the Debian `nodejs` 18.x and `node` alone (`npm` is not in the
  image). A cold image build drops from 4 min 22 s to about 2 min. The
  `docker-build` CI job now runs the built image: `node generate.mjs` and
  `fingerprint_profile._load_fingerprint_from_node()` must return a profile,
  and `npm` must be absent.

- CI: in `check-python` the `php-cli` apt install now runs in the
  background while pip installs the Python packages (the two no longer run
  one after another), and an image that already ships `php` skips apt. No
  change to the tool chain, tests or `make check`.

- CI: the `pytest` job of `tests.yml` is split into two parallel jobs,
  `check-python` (`make check-python`, pytest only) and `check-tools`
  (`make check-tools`: panel, panel-next, platform, docs, SDK, actionlint).
  `make check` runs both targets in the same order and with the same
  commands as before; each job installs only the tool chain it uses
  (`check-python`: php; `check-tools`: node, dotnet, imagemagick, php,
  ripgrep, actionlint). actionlint 1.7.12 comes from the official release
  binary with a pinned SHA-256 instead of `setup-go` + `go install`; npm and
  pip caches cover the lock files. `tests` gets `concurrency`: a new push to a
  pull request cancels that PR's previous run, runs on `main` are never
  cancelled. The required status check named `pytest` no longer exists; the
  branch has no required checks configured today. Details in
  `CONTRIBUTING.md`.

- Panel UX pass 1 (`panel-next`, interface only, no contract changes): the
  sidebar is grouped into Каталог / Мониторинг / Управление and the fifteen
  catalogue categories unfold only on catalogue pages; breadcrumbs follow the
  same groups. An overdue next parser run reads «просрочен на 24 дня» in the
  warning colour instead of «24 дня назад». Catalogue empty results say what
  did not match and offer a reset. The card editor groups its fields, marks
  required ones, names the card and keeps a sticky action row with delete
  separated as an outlined danger button. Wiki translations gain a count,
  a search icon, an empty state, a sticky save row and error-styled failures.
  The error page shows the log digest. Table row actions align with the first
  line of text. Tests: fast-check properties and literal boundaries for
  `formatSchedule` (dev dependency `fast-check` 4.10.2) and the browser
  scenario `panel-next/tests/ux-browser.mjs`. Details in
  `panel/docs/ADMIN_UI.md`.

- Speed: `/v1/health`, `/v1/system/health` and `/demo/overview` (the panel's
  analytics overview) no longer rebuild their diagnostics in the request
  (1.6–6.6 s and 3.4 s on production). A process snapshot is reused for 15 s,
  then served at once while one background build refreshes it; only a snapshot
  older than 10 minutes is rebuilt in the request. Snapshots are built at
  startup off the request path. New header `X-Health-Snapshot-Age`; response
  bodies are unchanged. The Next panel's PHP adapter releases the session file
  lock for read-only requests, so parallel reads of one admin (layout session
  and page data, card history) no longer queue behind each other or behind a
  slow request. Measurements and remaining causes: `docs/PERFORMANCE.md`.

- CI: two PHP tests no longer fail on main. The Battlegrounds override test
  loads `resolve_bg_base_card_id()` from `scan_cards.php` with the import
  upsert that now calls it, and the media-index limit check no longer depends
  on the directory order of the runner's filesystem (the same commit passed on
  2026-10-26 and failed on its rerun). Tests only; no runtime change.
- Blizzard leaderboards: a final read of a past season is dated by the
  season's end instead of the day the backfill read it, so
  `/v1/leaderboards/player/history` points are chronological across seasons
  (Norwis EU Standard: season 145 was shown on 26.10.2026 next to season 156,
  now on 30.11.2025). The end is the day before the next season's start where
  Blizzard dates seasons (Standard, Wild), else the last day the season was
  read live. Additive SQLite migration `user_version` 2: `lb_snapshots.as_of_day`
  and `lb_boards.season_end` filled for stored rows; `day` (the read day, key of
  `/v1/leaderboards?day=`) is unchanged, rollback SQL in docs. Player points
  gain `read_day`; one point per season day, a final replacing the live read
  of its day and keeping `final: true`; `points` ordered by season, then day.
  Seasons gain `season_end`; the snapshot gains `as_of_day`.

- Blizzard leaderboards keep player history durably: a live run waits up to
  20 minutes for a history run holding the lock instead of skipping its
  six-hour slot, and boards without a snapshot for the current UTC day are
  read first, so a run cut by its time budget no longer always drops Arena,
  Underground Arena and Twist. A day whose rows equal the previous day
  (Arena and Twist often) shares that day's rows (`lb_snapshots.entries_id`,
  `rows_hash`; additive SQLite migration `user_version` 1, rollback SQL in
  docs) instead of copying them; nothing deletes past days or seasons. The
  player name search now uses its index. New REST
  `/v1/leaderboards/player/history?name&mode&region&from&to` and GraphQL
  `leaderboardPlayerHistory`: one mode of a player by day across seasons
  with per-season summaries. `/v1/leaderboards/player` seasons gain
  `peak_rating`, `peak_rating_day`, `final_rank`, `final_rating` and
  `first_day`; `/v1/leaderboards/coverage` gains `live_days[].missing_days`
  and `shared_snapshots`. Existing fields are unchanged. See
  docs/BLIZZARD_LEADERBOARDS.md.

- HSGuru deck catalogs: the `/replays` render now waits for
  `#replays_table` instead of the LiveView root, which is already in the dead
  render; about 15% of replay renders came back without the table, were
  billed 5 credits and rejected. A catalog page spends at most two billed
  Scrape.do responses (one paid retry); unbilled 502s keep the 30 and 120 s
  retries. The 25.10 run that used super proxies (16:01–18:35 UTC) started
  before #66 was deployed and ran the old ladder; the current code sends no
  super proxy for deck catalogs. See docs/SCRAPE_PROVIDERS.md.

- New pipeline `hsreplay_card_choices`: what players pick from Discover,
  Adapt and Dredge cards. HSReplay's `single_card_choices_by_winrate_v2`
  gives, per offered option, how often it is taken when offered
  (`popularity`, the column sums to more than 100%) and the winrate after
  taking it; it is read for the 50 most played choice cards of Standard and
  10 of Wild at Legend and Bronze-Gold, weekly and once a day after a patch
  (about 120 plain Scrape.do requests a week, at most 5 parallel, caps of 40
  per run and 60 per UTC day). The full option pool of every choice card comes
  from the card base: Discover text is parsed into type, minion type, school,
  keyword, rarity, cost and class rules (pools that depend on the game are
  reported as such instead of guessed), Choose One options, the ten
  adaptations and Excavate treasures by tier (HSReplay answers 204 for Choose
  One and Excavate). Options get `low_sample` and `undervalued` flags. REST
  `/v1/hsreplay/card-choices`, `/card-choices/cards`, `/card-choices/coverage`;
  GraphQL `hsreplayCardChoices`; SQLite `hsreplay_card_choice_*` mirrored to
  PostgreSQL (migration 020); timer
  `hs-data-api-docker-refresh-hsreplay-card-choices` daily at 04:35. See
  docs/HSREPLAY_CARD_CHOICES.md.

- New pipeline `blizzard_leaderboards` collects the official Blizzard
  leaderboards from the keyless community API: Standard, Wild, Battlegrounds
  solo and duos, Arena, Underground Arena and Twist for EU, US and AP, the
  live season in full every six hours and every past season the API still
  serves (490 boards) once through an hourly backfill. One snapshot per board
  and UTC day keeps the rank and rating of every player by day in a separate
  SQLite file. Requests are paced to two a second with two in flight,
  gzip-compressed, retried with backoff; Blizzard's burst 403/429 pauses
  every request (30 s doubling to 5 min) and halves the pace. New REST
  `/v1/leaderboards`, `/stats`, `/players`, `/player`, `/seasons`,
  `/coverage` and GraphQL `leaderboard`, `leaderboardStats`,
  `leaderboardPlayers`, `leaderboardPlayer`, `leaderboardSeasons`. See
  docs/BLIZZARD_LEADERBOARDS.md.
- HSGuru coverage: Tavern Brawl and esports. `hsguru_brawl` reads the current
  Brawl meta (7 ranks × 3 coin options and 11 opponent classes, from 10
  games) and Brawl decks every three hours. `hsguru_esports_tournaments`
  collects /esports upcoming tournaments and every official tour stop
  (2019–2026) plus recently started community tournaments with stage
  standings, player lineups (deck codes) and lineup archetype popularity;
  finished tournaments are reused from the previous snapshot.
  `hsguru_esports_points` holds HSEsports points of every season for
  Standard and Battlegrounds; `hsguru_esports_legacy` the Masters Tour
  archive (weekly). Plain Scrape.do pages, at most 5 at once, as `detail`
  jobs of the shared HSGuru budget. REST `/v1/hsguru/brawl/*`,
  `/v1/hsguru/esports/*` (OpenAPI descriptions and examples) and GraphQL
  `hsguruBrawl*`/`hsguruEsports*`. A page that fails validation gets one
  rendered retry (5 credits); super proxies are not used. Docs:
  `docs/HSGURU_BRAWL_ESPORTS.md`, `docs/API.md`, `docs/GRAPHQL_API.md`,
  `docs/SCRAPE_PROVIDERS.md`; the HSGuru coverage audit is in
  `docs/DATA_CATALOG.md`.

- HSGuru matchup matrices for every public slice: new pipeline
  `hsguru_matchups` reads HSGuru `/matchups` for Standard and Wild, seven
  ranks and every advertised period (Bans window, expansion, patch, rolling
  windows) with HSGuru's lowest sample filters (1/1) and keeps winrate and
  games of every cell, so any threshold is applied on request. One API with
  slice parameters, `GET /v1/hsguru/matchups` (+ `/slices`) and GraphQL
  `hsguruMatchups`, instead of one source per combination. Short windows are
  re-read hourly, wide ones every 6-48 hours (plain Scrape.do, five pages in
  parallel, ~800 credits a day). HSGuru publishes no coin split for matchups,
  so only `any_player` exists. See docs/HSGURU_MATCHUPS.md.
- HSGuru streamers (`hsguru_streamers`, every five minutes): every
  `/streamer-decks` row in all formats, ranks and windows (one walk from the
  top down to the last complete walk; the first run goes back 120 days), who
  is live on `/streaming-now` with the deck played on the current broadcast,
  and the Twitch channel profile (avatar, YouTube, X and other links, past
  broadcasts, so a deck links to the moment of its VOD). Plain Scrape.do, at
  most five pages in parallel, ~3 credits a run. REST `/v1/hsguru/streamers`,
  `/streamers/{streamer}`, `/streamers/{streamer}/decks`, `/streamer-decks`,
  `/streaming-now`, `/streamers/coverage`; GraphQL `hsguruStreamers`,
  `hsguruStreamer`, `hsguruStreamerDecks`, `hsguruStreamingNow` from the
  PostgreSQL mirror (migration 018). See docs/HSGURU_STREAMERS.md.
- HSGuru mulligan no longer grows to gigabytes during a run (5.2 GiB RSS in
  production). The parse cache keyed by page HTML kept about 130 pages
  (~230 MB) alive; the parsed result now travels with the page from
  validation to storage and the HTML is released right after parsing. The
  rest was glibc arena fragmentation from 40 Scrape.do threads: the unit runs
  the container with `MALLOC_ARENA_MAX=2` and a run calls `malloc_trim(0)`
  every 200 pages. Local benchmark with 3,000 distinct pages: peak RSS
  1,262 → 465 MB, flat at 320–340 MB, throughput unchanged.
- Balance Radar: the company Blizzard is never renamed after the Mage spell
  «Снежная буря» (brand words stay out of the name pass; «Снежная буря» used
  as the actor of a statement becomes Blizzard again) (#63).
- HSGuru mulligan: class matchup pages that map two labels to one class keep
  the larger sample, and a failed write of one page marks only that target
  failed instead of ending the run (#62).

- HSGuru deck catalogs and archetype analysis ask Scrape.do for a plain page
  first (1 credit instead of 5): deck, archetype and card-stats pages are
  LiveView dead renders, and on 2026-10-25 plain and rendered pages gave the
  same builds, games, winrates, matchups and card rows. A rejected plain page
  gets one rendered retry; replay pages, whose table loads after the LiveView
  connects, get one rendered request. Super proxies, Decodo and the paid
  remote reserves are no longer used for these pages; a page that every route
  failed is retried after 30 and 120 s instead of climbing the paid ladder.
  Provider usage keeps recording `render` and the cost of every call.
- HSGuru replays are asked once per window (the catalog window, plus the
  whole release for Bans) and their builds and empty lookups are reused for
  three days, instead of asking every fallback window every night.
- Every HSGuru archetype catalog row without a build of its own window gets
  builds from wider windows (Bans: the whole release, then the rolling month)
  or real deck codes from its public replays, and says where they come from
  (`decks_period`, `decks_source`, `decks_fallback`).

- New pipeline `hsreplay_mulligan`: the HSReplay deck mulligan guide
  (mulligan v2, `/api/v1/mulligan/`) for the top decks of
  `list_decks_by_win_rate_v2` in Standard (10 most played + 2 best winrate per
  rank) and Wild (5 + 1) at Legend, Diamond 4-1, Diamond and Bronze-Gold: every
  deck × `all`/`first`/`coin` × `last_30_days`/`current_patch`/
  `current_expansion` (648 slices), each answer with `ALL` and the split by the
  eleven opponent classes from the premium session. Per card: keep %, opening
  hand, drawn and played winrate, turns held, turn played, plus `*_impact`
  versus the base winrate (the unit of `/v1/hsguru/mulligan`); `scope` says
  whether HSReplay used the deck itself or fell back to the archetype or class.
  Incremental TTL queue, snapshots only on change, plain Scrape.do with at most
  5 parallel requests, ~145 credits a UTC day by plan (caps: 60 a run, 160 a
  day). HTTP 202 (still computing) is deferred with backoff, 204 is an empty
  slice, not an error. Each run probes `/api/v1/account/`: a rejected or
  non-premium session skips premium slices, sends a Telegram alert, turns the
  source `partial` and makes the API answer premium slices with `503` and
  `premium_session` instead of an empty result. SQLite `hsreplay_mulligan_*`
  is mirrored to PostgreSQL `analytics.hsreplay_mulligan_*` (migration 019,
  view `hub.hsreplay_mulligan_latest`). REST `/v1/hsreplay/mulligan`,
  `/mulligan/decks`, `/mulligan/slices`, `/mulligan/history`,
  `/mulligan/coverage`, GraphQL `hsreplayMulligan`; timer
  `hs-data-api-docker-refresh-hsreplay-mulligan` every three hours at :50.
  Live run on 25 October: 72 decks listed, 72 slices with the opponent-class
  split (823 groups, 13,640 card rows) for 160 credits. Not shown on the site yet. See
  docs/HSREPLAY_MULLIGAN.md.

- Balance Radar: accurate, short, Russian-only texts. Archetypes use
  HearthPulse's own labels (the rest translated once by Claude and cached);
  cards use the ruRU localization. Every reader text is cleaned
  deterministically (English names, English brackets, rank slang, pipeline
  field names such as `history.patterns`). Mentions are marked up by `ref`
  (`textEntities`, `summaryEntities`). Every shown Reddit/X post and quote
  gets a full Russian translation (`titleRu`, `textRu`, cached). An optional
  Editor Team pass (`RADAR_EDITOR_URL`, docker network `editorteam-clients`)
  takes an edit only when it is accepted with all checks passed. Only real
  candidates are listed now (no quota). The backtest adds Brier score and a
  reliability table, and runs the Claude predictor automatically after a new
  patch; the result reaches every payload as `accuracy`.
- HSGuru mulligan parses a page with lxml into one tree, once: the provider
  cascade validates it twice and the run stores it, and all three now share
  one memoized result. BeautifulSoup built six trees per page (~140 ms of
  CPU), so the run was bound to one core at about 20 pages a minute in
  production. Measured on the real page path with the network mocked at 6 s:
  122 → 369 pages a minute at concurrency 40 (the network limit is 400),
  peak RSS 606 → 367 MB; output is identical on eight real HSGuru pages.

- Balance Radar v2 (schemaVersion 2, additive): 15–25 nerf and 15–25 buff
  candidates per format (cards, archetypes, classes) from wider pools (HSGuru
  all/legend/top_legend/diamond_4to1 in the Bans window, HSReplay Legend,
  Diamond 4-1 and Diamond). Confidence is anchored on a calibration of past
  patches (how often an archetype with this winrate/popularity was changed:
  389 archetype-weeks over 9 patches) and the odds of a patch within 7/14
  days. New inputs: HSGuru mulligan impacts, HSReplay core archetypes of each
  card, developer statements (Blizzard flair, official accounts). New fields:
  predicted changes with probabilities, meta impact, verified community
  quotes, key numbers, calibration, trend, confidence history, timing,
  changes since the previous run, counts. New `/backtest` (statistical
  nerf precision@5 0.28 / recall@10 0.59; Claude 0.33 / 0.77) and
  `/confidence-history` endpoints. About $1.14 per run for both formats.

- HSGuru deck catalogs fetch through Scrape.do first, 15 pages in parallel
  (`HS_HSGURU_DECK_CATALOG_CONCURRENCY`; the local FlareSolverr is the
  fallback, or goes first with `HS_HSGURU_DECK_CATALOG_LOCAL_FIRST=1`), with a
  90 s page timeout instead of 25 s. Every page asks for HSGuru's 50-game deck
  floor, so one pass verifies every archetype and the 200-target retry cap is
  gone; catalogs written under older rules are re-read in full. A live run
  took 28 minutes and 2,700 Scrape.do credits: Black Empire builds grew from
  458 to 2,634 (Standard) and 772 to 1,689 (Wild), and the Bans catalogs went
  from none to 1,826 and 840 builds. HSGuru lists no build below 50 games,
  also when logged in, so 88 of 243 Standard archetypes (0.3% of games) have
  none.
- One HSGuru deck-catalog run at a time: a flock makes a second run exit
  DEGRADED without writing, and the unit removes its named container when it
  stops. An orphaned container of the previous release had overwritten the
  Wild catalog of the next run. Death Knight builds that HSGuru names with
  their rune spread ("BUU Egg DK") now join their archetype ("Egg DK").

- HSGuru mulligan asks Scrape.do for a plain page first: HSGuru serves
  card-stats and archetype pages as LiveView dead renders, so a request
  without a browser has the full tables (8 s and 1 credit instead of 54 s
  and 5 credits). A rejected page still climbs the render and residential
  profiles; other HSGuru jobs keep their ladder. Concurrency defaults to 40
  and the timer fires hourly, so the whole plan loads in under an hour and
  costs about 8,800 credits; keeping it fresh about 1,300 a day.

- HSGuru mulligan reads pages through Scrape.do first, 25 in parallel
  (`HS_HSGURU_MULLIGAN_CONCURRENCY`, up to 50), so the shared server's
  FlareSolverr is only the fallback (`HS_HSGURU_MULLIGAN_TRANSPORT=scrape_do_only`
  turns it off, `flaresolverr` restores the old order). Blocking provider calls
  run in a dedicated thread pool instead of the default executor the API
  shares. Credit caps are off by default and stay available as safety limits.
  One run reads up to 10,000 pages in 150 minutes, so the whole plan
  (8,720 pages, about 51,000 credits) loads in about two hours instead of five
  days; keeping it fresh costs about 7,600 credits a day. Cumulative windows
  are re-read on 50 % growth at most every two days (was 30 % and one day) to
  keep re-reads of young windows affordable on the paid route.

- Balance Radar (`/v1/constructed/balance-radar`): nerf and buff candidates for
  Standard and Wild. A timer twice a day joins HSGuru archetypes by period
  (including the Bans window) and HSReplay card periods with Reddit
  (RedditAPIs) and X (GetXAPI) posts, and asks `claude-sonnet-5-5` for a
  structured verdict in Russian. Numbers and post links come from the data,
  never from the model. Runs and their cost are stored; the API keeps serving
  the last good run. About $0.86 per run for both formats.
- Balance Radar learns from Blizzard's past decisions: a history dataset of every
  card change since December 2023 (exact HearthstoneJSON build diffs, patch
  dates and Russian developer notes from `/api/patches`, HSReplay archetype
  winrate/popularity a week before and after since April 2026), computed
  patterns (thresholds, change types, timing after releases), a Claude-written
  overview, and up to three similar past cases per candidate (`precedents`).
  New endpoints `/v1/constructed/balance-radar/history` and `/patterns`.
- HSGuru coverage: matrix slices are requested with `min_games=10` instead of
  100 (same request count; Standard Bans ALL grows from 141 to about 226
  archetypes, Wild from 216 to about 628), add the Bronze–Platinum rank, and
  split On Coin / Going First for every rank instead of six. A run grows from
  423 to 603 Scrape.do pages (about 2,420 to 3,450 credits); its deadline grows
  to 150 minutes and the unit timeout to 160 minutes. `/v1/hsguru/meta` accepts
  `min_games` 10/25/50 and keeps its default of 100. The current archetype
  catalog lists archetypes from 10 games, and a Bans catalog
  (`/v1/hsguru/archetypes?period=black_empire_ban`) is built from the matrix
  slices with builds from its own deck catalogs. Deck catalogs ask for HSGuru's
  full 200-build page per archetype, re-read covered archetypes daily instead
  of keeping week-old builds, skip archetypes below HSGuru's 50-game deck
  floor, and the deck job's timeout grows to four hours. The paid archetype
  analysis keeps its 100-game Legend/past-week target set.
- New pipeline `hsguru_mulligan` collects the HSGuru mulligan guide
  (mulligan, drawn, not drawn and kept impact with counts per card) and class
  matchups for every archetype and catalog deck with enough games: six ranks ×
  eight periods including the Bans window, the coin split, opponent class for
  the most played archetypes, 8,289 pages on 25 October (about 1,200 a day by
  TTL). Before, only Legend past_week, without coin or decks, was collected.
  An incremental queue re-reads a page when its period TTL expires or a
  cumulative window grows by 30 %; it runs every three hours (200 pages, one
  hour) through the free local FlareSolverr. Scrape.do is a fallback capped at 300 credits per run and
  1,000 per UTC day inside the Meta-Lab HSGuru budget. SQLite `hsguru_*` is
  mirrored to PostgreSQL `analytics.hsguru_*` (migration 017). New REST
  `/v1/hsguru/mulligan`, `/class-matchups`, `/mulligan/slices`,
  `/mulligan/history`, `/mulligan/coverage` and GraphQL `hsguruMulligan`,
  `hsguruClassMatchups`. See docs/HSGURU_MULLIGAN.md.

- A HSGuru deck page whose cards all rendered but hold no usable deck (one
  "Highlander Warrior" card with a one-card code) now means "no builds" instead
  of failing the whole Wild all-rank catalog. Only cards without copy text count
  as an unrendered page.

- HSGuru meta matrix adds the "Bans" window (`period=black_empire_ban`) and
  the coin split back (`coin=on_coin` / `going_first`, HSGuru
  `player_has_coin=yes|no`) for the six ranks HearthPulse serves (ALL,
  Diamond, Diamond–Legend, Legend, Top 5K, Top Legend). A twelve-hour run grows
  from 164 to 422 Scrape.do pages (about 2,100 credits) and runs with
  concurrency 5. Coin and Bans slices may be empty except ALL over a full
  window.
- HSGuru all-rank deck catalogs no longer stall: a local-browser page whose deck
  cards cannot be read is rendered again through Scrape.do instead of failing
  the whole catalog (the Wild catalog had not refreshed since 21 October). A
  shared 20-archetype page that reaches its limit asks for each archetype on its
  own (10-game builds, up to 30 each), and the nightly exact retries cover up to
  200 build-less archetypes. The nightly job may run for up to two hours.

- Full HSReplay Battlegrounds minion statistics for every minion: new pipeline
  source `hsreplay_battlegrounds_minion_stats` reads the four analytics queries
  behind the HSReplay minion page (`battlegrounds_minion_list`, `_minion_position`,
  `_purchase_rates_by_turn`, `_minion_stats`) for six slices (TOP_50, TOP_20 and
  ALL × last 7 days and current patch; positions for TOP_50) once a
  day, validates each slice and keeps its previous snapshot on any failure. History
  is stored in SQLite `bg_minion_stat_*` and mirrored to PostgreSQL
  `analytics.bg_minion_stat_*` (migration 016). Header popularity and board
  positions now use HSReplay's own formulas, so 104551 shows 1.9 % / 0.2 %
  popularity and the site's position split instead of our earlier approximation.
  New REST `/v1/battlegrounds/minions/stats`, `/stats/slices`,
  `/{minion}/stats`, `/{minion}/stats/history` and GraphQL
  `battlegroundMinionStats`.
- HSGuru Scrape.do requests follow a measured ladder: datacenter render
  (5 credits) waiting for the LiveView root instead of a fixed delay, then the
  anonymous 10-credit residential profile with Scrape.do's free retries left
  on, then rendered residential (25). The 1-credit plain profile never passed
  HSGuru's Cloudflare (0/40) and is dropped. Cloudflare cookies are no longer
  forwarded and non-browser requests no longer carry the HSGuru session: both
  made Cloudflare block the residential profile, so pages that render missed
  went straight to 25 credits. Premium-filter paywall pages are rejected and
  escalate to the logged-in browser profile. A sticky Scrape.do session per
  worker cut median render time from 18.5 to 9.4 s in a 16-page benchmark.
- HSGuru meta matrix follows HSGuru's new period menu: seven rolling windows
  (`past_3_hours`, `past_6_hours`, `past_day`, `past_3_days`, `past_week`,
  `past_2_weeks`, `past_30_days`), the current patch and the «Власть Темной
  империи» expansion window `black_empire`, which is now the current-catalog
  period. The retired `violet_hold` and `most_wanted` windows are no longer
  scraped. 3/6-hour slices may be empty for every rank except ALL. The matrix
  grows from 126 to 162 Scrape.do pages per twelve-hour run.
- Constructed sync renames tokens and hero powers that were stored before release
  as `blizzard:<dbf>` to their HearthstoneJSON ID once it is published. Before,
  related-card sync kept updating the fallback row, so new-set tokens had no
  Russian names or local images in `related_cards_localized` (66 «Власть
  Темной империи» tokens on 2026-10-20).
- Public `/v1/*` responses no longer wait about a second for their ETag: the
  cache revision memoizes each dataset's `fetched_at` by file signature
  instead of parsing every published dataset (~260 MB) on each request inside
  the async middleware. `/v1/hsguru/meta` dropped from 1.1–2.8 s to tens of
  milliseconds; ETag values are unchanged. One revision now reads the
  parser-control state once instead of once per early source.
- `/v1/health` reuses its freshest public `fetched_at` for the same 15 s as
  its diagnostics: each monitoring probe resolved every public dataset (~6 s
  of CPU), which kept the API process near one full core.
- Next.js panel catalogue: artwork tiles with a loading shimmer, tier and pool
  badges and a golden marker; labelled filters, category pills, active-filter
  chips with a reset, and a filter sheet on phones.
- Next.js panel card inspector: opening a record writes `?card=<id>` to the
  URL, so it survives reloads, can be shared and closes with Back; links to
  cards beyond the current page resolve on the server. It shows curated text,
  mechanics, stats, badges, copyable IDs and the golden version; arrows flip
  through the page.
- Next.js panel tables use columns per category (heroes, skins, pets, coins,
  timewarped, Standard/Wild, libraries) with a pinned thumbnail and name.
- Ctrl+K in the Next.js panel searches cards of the current category as well
  as sections, with thumbnails and keyboard selection.

- Next.js panel design foundation: shared spacing, radius, type and z-index
  scales; separate warning and info colours; a darker light-theme accent so
  accent text passes 4.5:1; a "Как в системе" theme that follows the device.
- One formatting module for the Next.js panel: relative and exact UTC dates,
  Russian numbers and percents, status labels with tones, readable card types,
  tribes, scopes and statistics filters. Tables, record details, statistics,
  sources and tokens no longer print raw ISO dates, status codes or slugs.
- Next.js panel accessibility: deck tiles and catalogue cards announce tier,
  stats and price; the wide table area is a labelled region; icons replace
  text glyphs; empty results offer a reset.
- Inter now ships as a 206 KB woff2 subset instead of an 876 KB TTF, with its
  variable weight range declared.
- `npm run check` for the Next.js panel runs Prettier and Biome first.

- Kept manual Battlegrounds card edits across HearthstoneJSON imports. Edits
  are stored per field in `battlegrounds_card_overrides` with the upstream
  value they replaced, audited with their author, and released automatically
  when a newer game patch changes the field. Requires PostgreSQL migration
  `010_battlegrounds_card_overrides` before the panel deploy.
- Stopped constructed imports from silently removing cards: Blizzard
  pagination is validated, one run may remove at most `max(25, 5%)` of a
  format without `--allow-mass-removal`, and failed runs now keep their error
  row after the rollback.
- Hid legacy `catalog.jobs`, `catalog.options` and `catalog.migration` from
  GraphQL `collections` and `records`.
- Next.js panel: API token usage now reads `usage.request_count` instead of
  always showing 0; the editor no longer offers GIF uploads the backend
  rejects; deck tiles fit the window instead of requiring 1464 px; filled
  buttons keep 4.5:1 text contrast in the dark, tavern and arcane themes.
- Accepted graphql-core 3.3's wording for rejected mutations in the read-only
  GraphQL test.

- Added HSGuru Deck Radar beta: idempotent SQLite history over the published
  streamer-deck snapshot, candidate/confirmed exact-deck events, and the
  public `GET /v1/constructed/deck-radar` endpoint. Established catalog decks,
  matching archetype titles and ≥0.75-similar card variants are suppressed.
- Updated the Trigger.dev control-plane lockfile to patched `fast-uri` and
  `qs` releases after new upstream advisories began failing the CI audit.
- Added fail-closed freshness evidence for the four HSReplay daily meta
  datasets. Their public endpoints now require a verified `as_of` snapshot no
  older than 36 hours and return `503` instead of serving an unverified LKG;
  source diagnostics expose bounded `upstream_freshness` and
  `fresh_only_eligible` fields.
- Made Firestone legendary Arena pool rotations verifiable against the
  rarity-scoped upstream population, while retaining strict regression gates
  for truncated snapshots and every non-legendary Arena dataset.
- Added the authenticated database web panel, its sync jobs, tests, Nginx
  configuration and atomic release deployment to the canonical API repository.
- Added the PostgreSQL data platform, statistics normalizers, migrations and
  deployment contract to the same canonical repository.
- Moved the production panel to a domain-neutral runtime with persistent media
  and cache storage outside Git; the retired domain path is no longer used by
  active Nginx or systemd configuration.
- Excluded Python bytecode and test caches from immutable panel releases.
- Batched Wiki full-art page discovery at MediaWiki's 50-title limit, reducing
  Scrape.do requests for the same catalogue by roughly five times.
- Made Wiki full-art refresh validate existing local files by size and SHA-1
  before discovery, so Scrape.do is used only for missing or corrupt assets.
- Extended the full HSGuru archetype-analysis timeout to two hours so the
  checkpointed Scrape.do refresh can finish all rank/format targets.
- Restricted HSGuru archetype-analysis acquisition to Scrape.do; it no longer
  spends or probes Firecrawl, Bright Data or Scrapfly fallbacks.
- Made checkpoint retries combine independently successful matchup and card
  statistics components instead of requiring both requests to succeed in the
  same attempt.
- Accepted title-less structured API snapshots during last-known-good
  validation, so a temporary HSReplay premium-session failure no longer turns
  a valid Battlegrounds hero dataset into a hard parser error.
- Exposed Battlegrounds hero and detail row counts directly in parser status
  metadata after JSON refreshes.
- Calibrated the Standard Legend 24-hour card contract for its lower sample
  size while retaining field-fill and regression protection.
- Made the unified HSGuru matrix use the verified Scrape.do super-render
  profile before Firecrawl, and exposed logical/base/fresh/cached slice counts
  to parser monitoring.
- Made the four HSGuru deck catalogs refresh independently so one upstream
  failure no longer prevents the remaining format/rank datasets from updating.
- Allowed the unified HSGuru matrix to publish a clearly marked partial
  snapshot when one current-format catalog can be restored from the same
  patch's last-known-good data.
- Made a successful Battlegrounds hero-details JSON refresh update the
  compatible hero index too, removing its dependency on a separate premium
  HTML session for freshness.
- Панель, каталог и инспектор карты: переключатель «Язык карт» (русский,
  английский, польский, испанский). Меняются название, текст, flavor и рендер
  карты во всех 13 разделах, включая токены. Язык — параметр `?lang=en|pl|es`
  (для русского параметра нет), он сохраняется при фильтрах, страницах, видах,
  поиске и `?card=` и запоминается в браузере (`hs_card_lang`). Переводы
  грузятся из публичного `/api/v1/card-catalog/…?locale=` (не больше 6
  запросов одновременно, кэш в памяти вкладки); русский берётся из данных
  панели. Нет перевода — показан английский с пометкой; нет карты в каталоге
  или ошибка загрузки — данные панели, при ошибке есть кнопка «Повторить».
  Форма `|4(…)` в тексте учитывает язык (польские правила отличаются от
  русских). Новый браузерный сценарий `panel-next/tests/card-locale-browser.mjs`.
