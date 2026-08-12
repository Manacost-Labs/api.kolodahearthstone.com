# Roadmap стабильности HS Data API

> **Исторический snapshot.** Документ отражает состояние на указанную ниже
> дату и не является текущим runbook. Репозиторий и production уже сведены в
> каноническую структуру; актуальные инструкции находятся в
> [README](README.md), [архитектуре](ARCHITECTURE.md) и
> [deployment guide](../DEPLOY.md).

Состояние зафиксировано 2026-08-07. Приоритет — сначала устранить расхождение
исходников и production, затем повышать надёжность без массового рефакторинга.

## P0 — перед следующим deployment

1. Свести `/srv/projects/data/hs-data-api-strategy7` и `/srv/hs-data-api` в один
   reviewable diff. Не копировать каталоги целиком: в source есть более новые
   commits, а в runtime — незавершённая provider-chain работа.
2. Перенести и проверить единую цепочку Scrape.do -> Firecrawl key pool ->
   Scrapfly. Для каждого перехода нужны unit tests порядка вызовов, exhaustion,
   timeout и sanitization URL/секретов.
3. Применить thin entrypoint для HSGuru streamer decks: он больше не должен
   напрямую обращаться к Firecrawl и обходить общий fallback/quality path.
4. Сделать staging rebuild, затем проверить `/health`, `/ops/health`, один
   дешёвый source refresh и сохранение последнего хорошего dataset при отказе.

## P1 — следующий стабилизационный цикл

1. Добавить отсутствующий regression test для `hsreplay_card_periods` и
   разобрать exit code 2 одноимённой scheduled-задачи.
2. Разделить monitor semantics и реальные сбои: stale freshness-check должен
   отображаться как degraded/alert, а не как неразличимый crash сервиса.
3. Перевести deprecated FastAPI startup/shutdown handlers на lifespan.
4. Добавить единый health summary по scheduled jobs: последний успех, возраст
   dataset, использованный provider и безопасная причина последней ошибки.
5. Добавить canary без реальных premium-запросов для provider pools и budget
   thresholds, чтобы исчерпание credits обнаруживалось до массового отказа.
6. Держать dependency gate зелёным: минимальная безопасная версия `requests`
   зафиксирована явно, а у fingerprint helper транзитивный `adm-zip` закреплён
   через npm override. Пересматривать override при обновлении Apify-пакетов.

## P2 — упрощение структуры

1. Разделить разросшийся плоский `app/` по доменам: `providers`, `pipelines`,
   `storage`, `ops`. Делать по одному модулю с compatibility imports и тестами.
2. Удалить legacy host units после подтверждения, что все задачи работают через
   Docker; до этого хранить обе версии рядом и проверять их одним тестом.
3. Перенести актуальные документы из `plans/` в `docs/`, архивировать устаревшие
   решения и оставить один актуальный roadmap.
4. Ввести CI gates поэтапно: tests/source-catalog/actionlint, затем чистый Ruff
   baseline, type checking и security scanners без массового auto-fix.

## Definition of done для каждого шага

- минимальный diff и тест изменённого поведения;
- `make check` проходит;
- secrets и реальные upstream-запросы не попадают в тесты/логи;
- deployment выполняется отдельно после review;
- rollback и проверка production описаны до перезапуска.
