<?php
declare(strict_types=1);
// Isolated UI test data only: no authentication bypass, API calls or database.
require dirname(__DIR__) . '/lib/analytics.php';
function h($value): string
{
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}
$_GET['stats'] = 'overview';
$sources = [];
foreach (['hsguru_archetype_analysis', 'hsguru_matchups_legend', 'hsguru_meta_matrix'] as $index => $id) {
    $sources[] = [
        'source_id' => $id, 'site' => 'hsguru', 'category' => 'statistics',
        'description' => ['Архетипы: матчапы и статистика карт', 'Матрица матчапов Legend', 'Мета: форматы и рейтинги'][$index],
        'state' => $index === 0 ? 'partial' : 'ok', 'fetched_at' => gmdate(DATE_ATOM),
        'has_dataset' => true, 'stale' => $index === 0, 'serving_cached_dataset' => $index === 0,
        'data_evidence' => [
            'schema_version' => 1, 'has_dataset' => true,
            'collection' => ['fetched_at' => gmdate(DATE_ATOM, time() - ($index === 0 ? 86400 : 1800))],
            'upstream' => ['status' => 'unknown', 'as_of' => null],
            'coverage' => ['status' => $index === 0 ? 'partial' : 'unknown'],
            'components' => $index === 0 ? [[
                'name' => 'card_stats', 'entities_total' => 2,
                'state_counts' => ['cached' => 1, 'missing' => 1],
                'oldest_checked_at' => gmdate(DATE_ATOM, time() - 86400),
                'oldest_updated_at' => '2025-12-01T00:00:00Z',
                'missing_checked_at_count' => 0, 'missing_updated_at_count' => 1,
            ]] : [],
        ],
    ];
}
$payload = analytics_normalize('overview', [
    'title' => 'Состояние источников',
    'description' => 'Получение данных и подтверждённые сведения о свежести — раздельно.',
], ['payload' => ['sources' => $sources, 'total' => 3, 'ok_count' => 2], 'cached' => false, 'stale_cache' => false, 'cache_age' => 0]);
?>
<!doctype html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>HSGuru · тест сведений о данных</title>
    <link rel="icon" href="data:,">
    <link rel="stylesheet" href="/assets/style.css">
</head>
<body>
<main class="shell">
    <aside class="sidebar"><div class="sidebar-brand"><span class="brand-mark">HS</span><div><strong>HS Data</strong><p>тестовый просмотр</p></div></div></aside>
    <section class="workspace">
        <header class="topbar"><div class="topbar-copy"><h1>Обзор и мета</h1></div></header>
        <?php require dirname(__DIR__) . '/partials/analytics-dashboard.php'; ?>
    </section>
</main>
<script>
const fixturePayload = <?= json_encode($payload, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT | JSON_THROW_ON_ERROR) ?>;
window.fetch = async () => ({ok: true, status: 200, json: async () => fixturePayload});
</script>
<script src="/assets/analytics.js"></script>
</body>
</html>
