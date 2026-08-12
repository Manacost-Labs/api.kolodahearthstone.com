# Full art библиотек Полей сражений

## Назначение

Аксессуары (`library=trinket`) хранят два разных вида изображения:

- `image_url` — локализованная карта в рамке;
- `local_full_art_url` — квадратная исходная иллюстрация без рамки и текста.

Full art нельзя подменять `crop_image_url`: это поле оставлено для
совместимости с Blizzard API и может указывать на внешний производный файл.

## Источник и локальное хранение

Синхронизатор использует оригиналы HearthstoneJSON:

```text
https://art.hearthstonejson.com/v1/orig/{card_id}.png
```

Перед сохранением проверяются PNG-сигнатура, минимальный размер и квадратное
соотношение сторон. Файлы атомарно записываются в:

```text
/uploads/library-full-art/{card_id}.png
```

Публичные файлы отдаются `api.kolodahearthstone.com` с CORS и недельным браузерным
кэшем. Поэтому сайт и API не зависят от доступности HearthstoneJSON для каждого
пользовательского запроса.

## Синхронизация

Обычная синхронизация всех библиотек автоматически запускает импорт full art:

```bash
/srv/api-kolodahearthstone/panel/current/scripts/run_sync_job.sh libraries
```

Отдельный запуск:

```bash
/opt/wiki-hs-parser/.venv/bin/python \
  /srv/api-kolodahearthstone/panel/current/scripts/sync_library_full_art.py \
  --library trinket
```

По умолчанию существующие валидные локальные файлы не скачиваются повторно.
Для принудительного обновления используется `--refresh`. Перед изменениями
можно запустить `--dry-run`.

## API

`GET /api/v1/trinkets` и карточный endpoint возвращают локальный URL в
`images.full_art`, исходный URL в `images.full_art_source` и технические
метаданные в объекте `full_art`: источник, размеры, байты, SHA-1, MIME и дату
получения. Клиентам следует использовать `images.full_art`.

## Контроль качества

После синхронизации `audit_library_images.py` проверяет как изображения карт,
так и локальные full art аксессуаров. Задание завершается ошибкой, если хотя бы
один обязательный файл недоступен.
