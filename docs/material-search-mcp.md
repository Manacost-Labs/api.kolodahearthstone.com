# Быстрый поиск материалов: REST и MCP

Основная модель — `perplexity/pplx-embed-v1-0.6b` через OpenRouter, 1024 измерения,
`provider.sort=latency`. На момент выбора OpenRouter показывал P50 0,13 с,
против 0,24 с у `openai/text-embedding-3-small` и 0,28 с у `voyageai/voyage-4-lite`
и `baai/bge-m3`. Эти показатели провайдеров не являются замером нашего API.
Источники: [Perplexity](https://openrouter.ai/perplexity/pplx-embed-v1-0.6b),
[OpenAI](https://openrouter.ai/openai/text-embedding-3-small),
[Voyage](https://openrouter.ai/voyageai/voyage-4-lite),
[BGE](https://openrouter.ai/baai/bge-m3).

## Контракт интеграции

`GET /v1/search?q=предсмертный%20хрип&kind=card&limit=10`

Параметры: `q` (2–240 символов), повторяемый `kind`
(`card`, `hero`, `minion`, `archetype`, `source`), `limit` (1–50),
`mode` (`hybrid` или `lexical`). `items` содержат стабильный `id`, `title`, `text`,
`image_url`, `url`, `source_id`, `updated_at` и `metadata`.
`GET /v1/materials/{material_id}` получает актуальный материал по возвращённому ID.
Для ID с двоеточиями применяйте URL encoding. Отсутствующий материал возвращает 404.
Схемы REST опубликованы в `/openapi.json`.

`meta.mode` сообщает фактический способ поиска. `meta.fallback_reason` показывает
отключённый или временно недоступный semantic-поиск. При сбое OpenRouter, лимите
расходов, отсутствии индекса или его возрасте более суток остаётся поиск по
названиям. При недоступном PostgreSQL REST возвращает 503. Винрейты модель не
рассчитывает. Качество русскоязычных запросов нужно оценить на реальных примерах.

## MCP

Адрес: `https://api.kolodahearthstone.com/v1/mcp/`.
Transport: Streamable HTTP, stateless, JSON responses, официальный Python MCP SDK.
Инструменты `search(query, kinds?, limit?)` и `fetch(id)` возвращают структурированные
данные с текстом, изображениями и ссылками. `search` возвращает `results` и `meta`.
Чтение публичное; имеющиеся API-токены и их лимиты также поддерживаются.
`Origin` проверяется по `HS_CORS_ALLOWED_ORIGINS`. Запросы вне браузера обычно
не содержат Origin. MCP использует стандартную инициализацию и согласование
protocolVersion; клиент передаёт Accept: application/json, text/event-stream.

## Скорость и индекс

Материалы берутся исключительно из публичного `hub.unified_search`, с описаниями
constructed cards, доступными описаниями/notes BG и героев. Внутренние raw records,
учётные данные и произвольные коллекции не индексируются. Это не поиск по полным
текстам внешних статей: источник представлен записью каталога.

Векторы считаются заранее и сохраняются отдельным атомарным snapshot
`$HS_DATA_DIR/search/materials.npz`. В памяти выполняется векторизованный поиск;
для существующего каталога не требуется pgvector или изменение схем PostgreSQL.
Индекс ограничен 100 000 материалов. Результаты всегда загружаются из текущего
каталога, чтобы удалённые записи и старые изображения не отдавались из snapshot.
Точное название или ID не требует OpenRouter. Остальные новые запросы ограничены
1,5 с; одинаковые одновременные запросы объединяются. Кэш векторов: 1 час,
до 1024 запросов на worker. Максимум 4 одновременных и 60 новых embedding-запросов
в минуту на worker; при ошибке действует 30-секундный circuit breaker.
Фактическая скорость всего поиска включает PostgreSQL и сеть.

## Включение после выкладки

По умолчанию `HS_SEARCH_ENABLED=false`: REST/MCP работают в lexical-режиме.
Для semantic-поиска нужен уже используемый сервисом `HS_OPENROUTER_API_KEY` и
`HS_GRAPHQL_DATABASE_URL`. Секреты передаются только через существующее окружение
сервиса; не помещайте значения ключей в команды или логи.

1. Собрать и выкатить обновлённый API с новыми зависимостями.
2. В окружении API задать `HS_SEARCH_ENABLED=true`.
3. Собрать индекс из окружения сервиса:

   ```sh
   docker compose -f /srv/hs-data-api/docker-compose.yml run --rm api python -m app.build_material_index
   ```

4. Установить приложенные systemd service/timer для обновления каждые 6 часов.
5. Проверить REST `meta.mode=hybrid`, MCP initialize/tools/list/search/fetch,
   реальные задержки новых и повторных запросов, выдачу русских и английских названий.

Неизменившиеся описания используют прежние векторы. Обновление с ошибкой или пустой
каталог сохраняет прежний индекс. Одновременные rebuild блокируются file lock.
Стоимость модели на дату выбора: $0,004 за миллион входных токенов; актуальная
цена и задержка могут меняться. Автоматическое переключение embedding-модели
отсутствует: разные пространства векторов требуют полного переиндексирования.

Откат: `HS_SEARCH_ENABLED=false`, отключить timer. REST/MCP продолжат lexical-поиск.
