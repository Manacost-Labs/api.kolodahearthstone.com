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
<section
    class="panel analytics-hub"
    id="statistics"
    data-analytics-dashboard
    data-analytics-endpoint="/analytics.php"
    data-default-module="<?= h($statisticsModule) ?>"
>
    <div class="analytics-hub-head">
        <div>
            <span class="eyebrow">Единый центр данных</span>
            <h2>База и игровая статистика</h2>
            <p>Каталоги, статистика API и нормализованные срезы PostgreSQL собраны в одной панели. Данные статистики доступны только для чтения.</p>
        </div>
        <a class="button secondary" href="#database-catalogue">Перейти к каталогу</a>
    </div>

    <nav class="analytics-module-nav" aria-label="Разделы статистики">
        <?php foreach ($statisticsModules as $moduleKey => $moduleLabel): ?>
            <button
                type="button"
                data-analytics-module="<?= h($moduleKey) ?>"
                aria-pressed="<?= $statisticsModule === $moduleKey ? 'true' : 'false' ?>"
                class="<?= $statisticsModule === $moduleKey ? 'active' : '' ?>"
            ><?= h($moduleLabel) ?></button>
        <?php endforeach; ?>
    </nav>

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
            <button class="button ghost" type="button" data-analytics-refresh>Обновить</button>
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
