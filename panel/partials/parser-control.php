<?php
declare(strict_types=1);
?>
<section
    class="parser-workspace"
    data-parser-control
    data-endpoint="/parser-control.php"
    data-csrf="<?= h(panel_parser_control_csrf_token()) ?>"
>
    <header class="parser-hero">
        <div>
            <span class="eyebrow">Операционный центр</span>
            <h2>Парсеры и источники</h2>
            <p>Свежесть данных, расписание и ручные запуски в одном рабочем пространстве.</p>
        </div>
        <div class="parser-hero-actions">
            <span class="parser-updated" data-parser-updated>Получаем состояние…</span>
            <button class="button secondary" type="button" data-parser-refresh>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6v5h-5M4 18v-5h5M6.1 9a7 7 0 0 1 11.5-2.4L20 11M4 13l2.4 4.4A7 7 0 0 0 17.9 15"/></svg>
                Обновить
            </button>
        </div>
    </header>

    <div class="parser-alert" role="alert" data-parser-alert hidden>
        <span aria-hidden="true">!</span>
        <div><b data-parser-alert-title>Не удалось получить состояние</b><p data-parser-alert-message></p></div>
        <button type="button" data-parser-retry>Повторить</button>
    </div>

    <section class="parser-summary" aria-label="Состояние парсеров" data-parser-summary aria-busy="true">
        <article data-summary-card="active"><span>Сейчас</span><b>—</b><small>активных запусков</small></article>
        <article data-summary-card="health"><span>Свежие данные</span><b>—</b><small>последний сбор опубликован</small></article>
        <article data-summary-card="issues"><span>На резерве</span><b>—</b><small>данные доступны из последнего успеха</small></article>
        <article data-summary-card="next"><span>Следующий запуск</span><b>—</b><small>по расписанию</small></article>
    </section>

    <section class="panel parser-sources-panel">
        <div class="parser-panel-head">
            <div>
                <h3>Источники данных</h3>
                <p data-parser-source-count>Загрузка реестра…</p>
            </div>
            <div class="parser-view-actions">
                <button class="parser-density-button" type="button" data-parser-density aria-pressed="false">
                    Компактно
                </button>
                <details class="table-column-picker" data-column-picker data-table-target=".parser-source-table" data-storage-key="parser-sources">
                    <summary>Колонки</summary>
                    <div class="column-picker-menu" data-column-picker-menu></div>
                </details>
                <button class="button" type="button" data-run-section hidden>Запустить раздел</button>
            </div>
        </div>

        <div class="parser-toolbar">
            <label class="parser-search">
                <span class="visually-hidden">Найти источник</span>
                <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m20 20-4.3-4.3m2.3-5.2a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z"/></svg>
                <input type="search" placeholder="Название или ID источника" autocomplete="off" data-parser-search>
                <kbd aria-hidden="true">/</kbd>
            </label>
            <label>
                <span class="visually-hidden">Состояние источника</span>
                <select data-parser-status>
                    <option value="all">Все состояния</option>
                    <option value="fresh">Свежие данные</option>
                    <option value="fallback">Работают на резерве</option>
                    <option value="unavailable">Данных нет</option>
                    <option value="disabled">Отключены</option>
                </select>
            </label>
        </div>

        <nav class="parser-section-tabs" aria-label="Группы источников" data-parser-sections>
            <button type="button" class="active" data-section-id="all" aria-pressed="true">Все</button>
        </nav>

        <?php $tableNavigationTarget = '.parser-table-scroll'; $tableNavigationLabel = 'Источники данных'; require __DIR__ . '/table-navigation.php'; ?>
        <div class="parser-table-scroll" tabindex="0" aria-label="Таблица источников парсеров">
            <table class="parser-source-table">
                <thead><tr>
                    <th>Источник</th>
                    <th>Данные сейчас</th>
                    <th>Последняя попытка</th>
                    <th>Записей</th>
                    <th>Расписание</th>
                    <th>Следующий запуск</th>
                    <th><span class="visually-hidden">Действия</span></th>
                </tr></thead>
                <tbody data-parser-sources-body>
                    <?php for ($row = 0; $row < 6; $row++): ?>
                        <tr class="parser-skeleton-row" aria-hidden="true"><td colspan="7"><span></span></td></tr>
                    <?php endfor; ?>
                </tbody>
            </table>
        </div>
        <p class="parser-empty" data-parser-empty hidden>Источники с такими параметрами не найдены.</p>
    </section>

    <section class="panel parser-runs-panel">
        <div class="parser-panel-head">
            <div><h3>Последние запуски</h3><p>История выполнения и результат каждого сбора.</p></div>
            <span class="parser-live-indicator"><i aria-hidden="true"></i> Автообновление</span>
        </div>
        <div class="parser-runs-list" data-parser-runs aria-live="polite">
            <p class="parser-empty">Загрузка запусков…</p>
        </div>
    </section>

    <dialog class="parser-run-dialog" data-run-dialog aria-labelledby="parserRunDialogTitle">
        <form method="dialog" data-run-form>
            <header>
                <span class="eyebrow">Ручной запуск</span>
                <h2 id="parserRunDialogTitle" data-run-dialog-title>Запустить источник</h2>
                <p data-run-dialog-description>Новый запуск будет поставлен в очередь.</p>
            </header>
            <input type="hidden" data-run-source-id>
            <input type="hidden" data-run-section-id>
            <label>
                <span>Причина запуска</span>
                <input type="text" maxlength="500" value="Ручной запуск из панели" required data-run-reason>
            </label>
            <div class="parser-dialog-actions">
                <button class="button ghost" type="button" data-run-cancel>Отмена</button>
                <button class="button" type="submit" value="confirm" data-run-confirm>Поставить в очередь</button>
            </div>
            <p class="parser-dialog-status" data-run-status aria-live="polite"></p>
        </form>
    </dialog>
</section>
