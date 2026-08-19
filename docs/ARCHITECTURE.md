# Архитектура HS Data API

## Границы проекта

- GitHub-репозиторий: `Manacost-Labs/api.kolodahearthstone.com`.
- Локальный source checkout может находиться в любом рабочем каталоге; он не
  должен совпадать с production runtime.
- Production working copy: `/srv/hs-data-api`.
- Канонический GraphQL API: `api.kolodahearthstone.com/v1/graphql`.
- Канонический REST namespace: `api.kolodahearthstone.com/v1/*`.
- Старые REST-контракты временно доступны на новом домене для поэтапного
  отключения `api.hs-manacost.ru` и `db.kolodahs.ru`.
- Код сначала меняется и проверяется в каноническом репозитории. Production
  обновляется только штатным deployment; ручная правка runtime не считается
  исходником.
- Данные, кеши, cookies и credentials не входят в репозиторий и не должны
  попадать в Git, документацию или тестовые логи.

## Карта каталогов

| Путь | Содержимое | Правило |
| --- | --- | --- |
| `app/` | FastAPI, CLI, реестр источников, orchestration и доменные парсеры. | Основной production-код. |
| `app/routers/` | Версионированные HTTP endpoints и response models. | HTTP-слой не должен содержать scraping-логику. |
| `app/scrapers/` | Общие HTTP/browser backends, resilience и quality helpers. | Повторно используемая инфраструктура сбора. |
| `scripts/` | Установочные, миграционные и совместимые operational entrypoints. | Новую бизнес-логику держать в `app/`, а скрипты оставлять тонкими. |
| `systemd/` | Services и timers для Docker и legacy host install. | Каждой плановой задаче нужны service, timer и тест расписания. |
| `tests/` | Unit, contract и regression tests; `fixtures/` — обезличенные samples. | Реальную сеть и production secrets не использовать. |
| `docs/` | Архитектура, API, каталоги и runbooks. | `SOURCES.md` генерируется скриптом. |
| `wiki/` | Короткие связанные страницы Wiki и навигация. | Источник для repo-native и GitHub Wiki. |
| `config/` | Версионируемая конфигурация без секретов. | Credentials здесь запрещены. |
| `web/` | Встроенный web UI API-сервиса. | Не смешивать с WordPress plugin. |
| `wp-plugins/` | Исходники интеграции WordPress. | Отдельная поверхность сборки и проверки. |
| `lab/` | Экспериментальные backends и прототипы. | Не подключать к production без отдельного решения. |
| `plans/` | Исторические планы реализации. | Актуальные решения переносить в `docs/`. |
| `data/`, `.venv/`, `.codegraph/` | Runtime/local artifacts. | Игнорируются Git и не являются исходниками. |

## Основные потоки

```text
systemd timer / admin API / CLI
              |
              v
       app.cli / app.main
              |
              v
        app.fetcher planner
              |
       +------+-------+
       |              |
 API-first parser   shared page scrape
                      |
                      v
            app.firecrawl_backend
                      |
                      v
          parser -> quality gate
                      |
                      v
       regression gate -> storage/cache
                      |
                      v
             public REST dataset
```

Реестр `app.sources.SOURCES` — единственный источник истины для source IDs.
`scripts/generate-source-catalog.py` строит из него `docs/SOURCES.md`.
`app/source_contracts.py`, quality gate и regression gate не позволяют
неполным upstream-ответам молча заменить хороший dataset.

## Scrape providers

Общий page-scrape должен проходить через `app/firecrawl_backend.py`, а не
вызывать provider API из operational-скрипта. Это даёт одну точку ротации
ключа, sanitization ошибок и учёта credits. Общий cloud cascade начинается со
Scrape.do; доступность следующих слоёв определяется конфигурацией, allowlist и
budget guards. Специализированные adapters могут иметь более узкую policy.
Официальные JSON API остаются прямыми. Точный порядок и условия включения:
[SCRAPE_PROVIDERS.md](SCRAPE_PROVIDERS.md).

## Две подсистемы

Репозиторий обслуживает две независимые подсистемы. У них разные хранилища,
разные парсеры и разные расписания; общего кода почти нет, общий только домен
`api.kolodahearthstone.com`, который nginx делит по путям.

| | Платформа статистики | Каталог карт |
| --- | --- | --- |
| Что собирает | Мету, винрейты, архетипы, композиции | Сами карты: тексты, статы, картинки |
| Источники | HSReplay, HSGuru, Firestone, Vicious Syndicate | HearthstoneJSON, Blizzard API, hearthstone.wiki.gg |
| Хранилище | PostgreSQL `hs_data` (контейнер `hs-data-postgres-1`) | MySQL `generator` |
| Схема | `platform/sql/*.sql` | `panel/sql/schema.mysql.sql` |
| Код | `app/`, `orchestration/` | `panel/` |
| Запуск | Docker, контейнер `hs-data-api` | PHP-FPM за nginx |
| Расписание | `systemd/*.timer` | `panel/systemd/*.timer` + один cron |
| Отдаёт | `/v1/*`, `/v1/graphql` | корень домена, `/api/*`, `/uploads/*` |

Новый движок парсинга затрагивает обе, но по-разному: у платформы точка входа
одна (`app.fetcher` и реестр `app.sources.SOURCES`), у каталога карт единого
реестра нет — там два десятка самостоятельных скриптов, см. ниже.

## Каталог карт

### Поток данных

```text
cron (каждые 5 ч)            systemd timers (kolodahs-sync@*)
      |                                   |
      v                                   v
scan_cards.php                    sync_*.py по областям
 (HearthstoneJSON + Blizzard)     (вики-мета, скины, монеты, арты)
      |                                   |
      +----------------+------------------+
                       v
              MySQL `generator`
       карточные таблицы + журнал правок
                       |
      +----------------+----------------+
      v                                 v
 sync_battlegrounds_images.py    mirror_constructed_images.py
 backfill_constructed_images.py  (рендеры Стандарта и Вольного)
      |                                 |
      +----------------+----------------+
                       v
            uploads/ на диске (~8 ГБ)
                       |
                       v
        api/index.php -> локальная копия,
        внешний CDN только как запасной вариант
```

### Таблицы

Схема целиком: `panel/sql/schema.mysql.sql` (34 таблицы, 2 представления).
Группы:

- `battlegrounds_*` — карты и герои Полей сражений. `battlegrounds_card_changes`
  хранит полный payload до и после каждой правки: по нему строится диф патча.
- `constructed_cards` — общая таблица Стандарта и Вольного. Представления
  `constructed_standard_cards` и `constructed_wild_cards` фильтруют её через
  `constructed_format_cards`. **Журнала правок у constructed нет** — это
  известный пробел, из-за него «до/после» по Стандарту приходится собирать из
  внешних сборок HearthstoneJSON.
- `*_import_runs` — история запусков парсеров: что просмотрено, что изменено.
  Первое место, куда смотреть, когда данные разъехались.
- `cards`, `cards_ru`, `cards_de` и прочие — слой прошлой версии каталога.
  Код панели к ним не обращается; перед удалением стоит проверить внешних
  потребителей.

### Картинки

Все рендеры зеркалируются на диск, чтобы не зависеть от чужих CDN.
`api/index.php` отдаёт локальную копию, если колонка `local_*_url` заполнена, и
только иначе уходит на внешний адрес. К локальным ссылкам дописывается версия
из `updated_at`, поэтому у них не залипает кеш.

Определение устаревания различается по источнику, и это важно понимать перед
правкой:

- **CDN Blizzard** адресуется по sha256 содержимого файла. Смена картинки
  меняет адрес, поэтому устаревание ловится сравнением строк без обращения в
  сеть. Старые объекты не удаляются: сохранённый прежний URL остаётся вечной
  ссылкой на прежнюю версию картинки.
- **HearthstoneJSON** отдаёт рендеры Полей только по пути `latest`. Адрес до и
  после патча одинаковый, поэтому хранить его бесполезно — сравнение идёт по
  `ETag` и `Last-Modified`, а прежнюю версию можно сохранить только копией
  файла в момент замены.
- **hearthstone.wiki.gg** — небольшой community-хост, запросы к нему
  ограничены по частоте в `mirror_constructed_images.py`.

## Известные расхождения

Список того, что в реальности отличается от описанного выше. Держать его
актуальным важнее, чем красивым.

- **Две установки панели.** Кроме `/srv/api-kolodahearthstone/panel/current`
  жива старая `/var/www/koloda/data/www/db.kolodahs.ru`, и cron всё ещё
  делает `cd` в неё. Рабочий корень удерживается переменной
  `KOLODAHS_APP_ROOT`; без неё скрипты складывают картинки в каталог, который
  nginx не отдаёт. Пока старая установка жива, расхождение может повториться.
- **`/srv/hs-data-api` разъехался с origin.** Production working copy отстаёт
  от `origin/main` и содержит незакоммиченные правки. Правило «runtime не
  является исходником» соблюдается не полностью.
- **Старое имя репозитория вводит в заблуждение.** `Zulut30/hearthstone-parses`
  это не второй репозиторий, а прежний адрес этого же: GitHub держит редирект
  после переименования и переноса. Одинаковая история объясняется именно этим.
  Ссылаться везде на `Manacost-Labs/api.kolodahearthstone.com`, иначе легко
  принять редирект за отдельное зеркало.

## Проверка и deployment

```bash
make setup             # один раз
make provider-check    # быстрый gate provider-слоя
make check             # обязательный полный gate
make security          # отдельный анализ security-рисков
```

После успешных проверок diff проходит ручной review. Только после этого
обновляется `/srv/hs-data-api`, пересобирается Docker image и выполняются
health/smoke checks. Простое изменение файлов working copy не меняет уже
запущенный контейнер, потому что код запечён в image.
