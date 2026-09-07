<?php
declare(strict_types=1);
$action = 'analytics';
require __DIR__ . '/shell_fixture.php';

function h($value): string
{
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}


?>
<!doctype html>
<html lang="ru" data-theme="light">
<head>
    <script src="/assets/workspace.js" defer></script>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Статистика · UI fixture</title>
    <link rel="stylesheet" href="/assets/style.css?v=31">
    <script src="/assets/panel-ui.js?v=2" defer></script>
    <link rel="stylesheet" href="/assets/workspace.css">
    <script src="/assets/table-controls.js" defer></script>
</head>
<body>
<main class="shell">
    <?php require __DIR__ . '/../partials/sidebar.php'; ?>
    <section class="workspace" id="main-content" tabindex="-1">
        <?php require __DIR__ . '/../partials/topbar.php'; ?>
        <?php require __DIR__ . '/../partials/analytics-dashboard.php'; ?>
    </section>
</main>
<?php require __DIR__ . '/../partials/command-palette.php'; ?>
<script>
window.analyticsFixture = {payload: {
        ok: true,
        title: 'Состояние источников',
        description: 'Актуальность всех наборов и последняя успешная публикация.',
        summary: [
            {label: 'Источники', value: '5', tone: 'neutral'},
            {label: 'Работают', value: '3', tone: 'good'},
            {label: 'Внимание', value: '1', tone: 'warning'},
            {label: 'Ошибки', value: '1', tone: 'bad'}
        ],
        columns: [
            {key: 'source', label: 'Источник'},
            {key: 'dataset', label: 'Набор'},
            {key: 'state', label: 'Состояние', type: 'status'},
            {key: 'updated_at', label: 'Обновлён', type: 'date'},
            {key: 'age', label: 'Свежесть'},
            {key: 'records', label: 'Записей', type: 'number'}
        ],
        rows: [
            {source: 'HSGuru', dataset: 'Standard archetypes', state: 'ok', updated_at: '2026-08-13T00:18:00Z', age: '19 минут', age_tone: 'good', records: 184},
            {source: 'HSReplay', dataset: 'Wild meta', state: 'cached', updated_at: '2026-08-12T19:40:00Z', age: '4 часа', age_tone: 'warning', records: 93},
            {source: 'Firestone', dataset: 'Arena cards', state: 'ok', updated_at: '2026-08-13T00:07:00Z', age: '30 минут', age_tone: 'good', records: 288},
            {source: 'HSGuru', dataset: 'Battlegrounds heroes', state: 'ok', updated_at: '2026-08-13T00:02:00Z', age: '35 минут', age_tone: 'good', records: 105},
            {source: 'Blizzard', dataset: 'Card library', state: 'error', updated_at: '2026-08-12T12:00:00Z', age: '12 часов', age_tone: 'bad', records: 0}
        ],
        meta: {updated_at: '2026-08-13T00:37:00Z', source_id: 'dataset-registry'},
        parsing_reliability: null
}};
</script>
<script src="/tests/analytics_fixture_transport.js"></script>
<script src="/assets/analytics.js?v=4"></script>
</body>
</html>
