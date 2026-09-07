<?php
declare(strict_types=1);

$statisticsModules = [
    'overview' => 'Обзор',
    'meta' => 'Мета',
    'hsguru_archetypes' => 'HSGuru архетипы',
    'constructed_cards' => 'Standard / Wild карты',
    'arena_cards' => 'Карты Арены',
    'archetypes' => 'Архетипы',
    'decks' => 'Колоды',
    'bg_heroes' => 'BG герои',
    'bg_minions' => 'BG существа',
    'arena' => 'Арена',
    'patches' => 'Патчи',
];
$statisticsModule = trim((string)($_GET['stats'] ?? 'overview'));
if (!isset($statisticsModules[$statisticsModule]) && $statisticsModule !== 'card') {
    $statisticsModule = 'overview';
}
$statisticsQuery = trim((string)($_GET['stats_q'] ?? ''));
$statisticsFormat = trim((string)($_GET['stats_format'] ?? 'standard'));
$statisticsRank = trim((string)($_GET['stats_rank'] ?? 'legend'));
$statisticsPeriod = trim((string)($_GET['stats_period'] ?? 'past_day'));
?>
<header class="workspace-page-head">
    <div><h1>Обзор и статистика</h1><p>Состояние данных и игровые показатели. Только чтение.</p></div>
    <a class="button secondary" href="/">Открыть каталог <span aria-hidden="true">↗</span></a>
</header>
<section
    class="panel analytics-hub"
    id="statistics"
    data-analytics-dashboard
    data-analytics-endpoint="/analytics.php"
    data-default-module="<?= h($statisticsModule) ?>"
>
    <div class="analytics-hub-head">
        <label class="analytics-module-select">
            <span>Раздел статистики</span>
            <select data-analytics-module-select>
                <?php foreach ($statisticsModules + ['card' => 'Поиск конкретной карты'] as $moduleKey => $moduleLabel): ?>
                    <option value="<?= h($moduleKey) ?>"<?= $statisticsModule === $moduleKey ? ' selected' : '' ?>><?= h($moduleLabel) ?></option>
                <?php endforeach; ?>
            </select>
        </label>
        <div class="analytics-view-actions">
            <button class="button" type="button" data-analytics-refresh>Обновить данные</button>
            <button class="table-density-toggle" type="button" data-table-density aria-pressed="false">Компактно</button>
            <details class="table-column-picker" data-column-picker data-table-target=".analytics-table" data-storage-key="analytics">
                <summary>Колонки</summary>
                <div class="column-picker-menu" data-column-picker-menu></div>
            </details>
        </div>
    </div>

    <form class="analytics-controls" data-analytics-controls>
        <label class="analytics-query-control">
            <span data-analytics-search-label>Поиск по разделу</span>
            <input
                type="search"
                value="<?= $statisticsModule !== 'card' ? h($statisticsQuery) : '' ?>"
                placeholder="Поиск"
                autocomplete="off"
                data-analytics-search
            >
        </label>
        <label data-analytics-format-control<?= $statisticsModule !== 'meta' ? ' hidden' : '' ?>>
            <span>Формат</span>
            <select data-analytics-format>
                <option value="standard"<?= $statisticsFormat === 'standard' ? ' selected' : '' ?>>Standard</option>
                <option value="wild"<?= $statisticsFormat === 'wild' ? ' selected' : '' ?>>Wild</option>
            </select>
        </label>
        <label data-analytics-rank-control<?= $statisticsModule !== 'meta' ? ' hidden' : '' ?>>
            <span>Ранг</span>
            <select data-analytics-rank>
                <?php foreach (['all' => 'Все', 'diamond_4to1' => 'Diamond 4–1', 'legend' => 'Legend', 'top_5k' => 'Top 5K', 'top_legend' => 'Top Legend', 'top_500' => 'Top 500', 'top_100' => 'Top 100'] as $rankValue => $rankLabel): ?>
                    <option value="<?= h($rankValue) ?>"<?= $statisticsRank === $rankValue ? ' selected' : '' ?>><?= h($rankLabel) ?></option>
                <?php endforeach; ?>
            </select>
        </label>
        <label data-analytics-period-control<?= $statisticsModule !== 'meta' ? ' hidden' : '' ?>>
            <span>Период</span>
            <select data-analytics-period>
                <?php foreach (['past_day' => '24 часа', 'past_3_days' => '3 дня', 'past_week' => '7 дней', 'past_2_weeks' => '14 дней'] as $periodValue => $periodLabel): ?>
                    <option value="<?= h($periodValue) ?>"<?= $statisticsPeriod === $periodValue ? ' selected' : '' ?>><?= h($periodLabel) ?></option>
                <?php endforeach; ?>
            </select>
            <input type="hidden" value="100" data-analytics-min-games>
        </label>
        <label data-analytics-mode-control hidden>
            <span>Режим</span>
            <select data-analytics-mode>
                <option value="solo">Solo</option>
                <option value="duos">Duos</option>
            </select>
        </label>
        <label data-analytics-rating-control hidden>
            <span>Рейтинг игроков</span>
            <select data-analytics-rating>
                <option value="100">Все игроки</option>
                <option value="50" selected>Top 50%</option>
                <option value="25">Top 25%</option>
                <option value="10">Top 10%</option>
                <option value="1">Top 1%</option>
            </select>
        </label>
        <label data-analytics-arena-source-control hidden>
            <span>Режим / источник</span>
            <select data-analytics-arena-source>
                <option value="firestone">Обычная · Firestone</option>
                <option value="hsreplay">Обычная · HSReplay</option>
                <option value="underground">Подпольная · Firestone</option>
            </select>
        </label>
        <label data-analytics-card-rank-control hidden>
            <span>Рейтинг карт</span>
            <select data-analytics-card-rank>
                <option value="platinum">Platinum</option>
                <option value="diamond">Diamond</option>
                <option value="diamond_4_1">Diamond 4–1</option>
                <option value="legend" selected>Legend</option>
            </select>
        </label>
        <label data-analytics-card-period-control hidden>
            <span>Период карт</span>
            <select data-analytics-card-period>
                <option value="1d">24 часа</option>
                <option value="3d">3 дня</option>
                <option value="7d" selected>7 дней</option>
                <option value="14d">14 дней</option>
                <option value="patch">Текущий патч</option>
            </select>
        </label>
        <div class="analytics-control-actions">
            <button class="button secondary" type="submit">Применить</button>
        </div>
    </form>

    <section class="analytics-result" data-analytics-content aria-busy="true">
        <section
            class="parsing-reliability"
            data-parsing-reliability
            aria-live="polite"
            aria-busy="true"
            hidden
        ></section>
        <div class="analytics-result-head">
            <div>
                <h3 data-analytics-title>Загрузка…</h3>
                <p data-analytics-description>Получаем актуальный набор из локального API.</p>
            </div>
            <div class="analytics-summary" data-analytics-summary aria-label="Ключевые показатели"></div>
        </div>
        <p class="visually-hidden" data-analytics-status aria-live="polite">Загрузка статистики…</p>
        <?php $tableNavigationTarget = '[data-analytics-table]'; $tableNavigationLabel = 'Таблица статистики'; require __DIR__ . '/table-navigation.php'; ?>
        <div data-analytics-table>
            <div class="analytics-table-skeleton analytics-skeleton" aria-hidden="true"></div>
        </div>
        <p class="analytics-meta" data-analytics-meta></p>
    </section>

    <form class="analytics-card-search" method="get" action="/" data-card-statistics-form>
        <input type="hidden" name="action" value="analytics">
        <input type="hidden" name="stats" value="card">
        <label for="statistics-card-name">
            <span>Найти подробную статистику конкретной карты</span>
            <span class="analytics-search-field">
                <input
                    id="statistics-card-name"
                    type="search"
                    name="stats_q"
                    value="<?= $statisticsModule === 'card' ? h($statisticsQuery) : '' ?>"
                    placeholder="Английское название, например Fire Fly"
                    autocomplete="off"
                    data-card-statistics-input
                >
                <button class="button" type="submit">Найти</button>
            </span>
        </label>
        <p>Поиск объединяет статистику Standard, Wild, Арены и Полей сражений.</p>
    </form>

    <div class="analytics-detail-backdrop" data-analytics-detail-backdrop hidden></div>
    <aside
        class="analytics-detail-drawer"
        data-analytics-detail-drawer
        role="dialog"
        aria-modal="true"
        aria-labelledby="analyticsDetailTitle"
        aria-describedby="analyticsDetailDescription"
        tabindex="-1"
        hidden
    >
        <header class="analytics-detail-head">
            <div>
                <span class="eyebrow" data-analytics-detail-kind>Подробные данные</span>
                <h2 id="analyticsDetailTitle" data-analytics-detail-title>Сущность</h2>
                <p id="analyticsDetailDescription" data-analytics-detail-description></p>
            </div>
            <button class="analytics-detail-close" type="button" data-analytics-detail-close aria-label="Закрыть подробные данные">Закрыть</button>
        </header>
        <div class="analytics-detail-body" data-analytics-detail-body></div>
    </aside>
</section>
