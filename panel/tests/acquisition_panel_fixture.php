<?php
declare(strict_types=1);
// Isolated synthetic UI fixture. No auth bypass, real API, artifact or DB access.
require dirname(__DIR__) . '/lib/analytics.php';
function h($value): string { return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }
$ids = ['hsreplay_battlegrounds_comps', 'hsguru_meta_standard_legend', 'hsguru_meta_wild_legend', 'hsreplay_arena_cards'];
$catalog = [];
$observations = [];
foreach ($ids as $i => $id) {
    $site = strpos($id, 'hsguru') === 0 ? 'hsguru' : 'hsreplay';
    $catalog[] = ['source_id' => $id, 'site' => $site, 'description' => ['BG-композиции и подробности', 'Standard · Legend', 'Wild · Legend', 'Карты Арены'][$i]];
    if ($i === 3) continue;
    $counts = ['succeeded' => $i === 0 ? 12 : 24, 'absent' => 0, 'failed' => 0, 'retry' => $i === 0 ? 4 : 0, 'running' => 0, 'pending' => $i === 0 ? 8 : 0, 'not_requested' => 0];
    $observations[] = ['source_id' => $id, 'observed_at' => gmdate('Y-m-d\TH:i:s\Z', time() - ($i === 2 ? 172800 : 600)), 'credits_spent' => 60, 'unknown_cost_attempts' => $i === 0 ? 1 : 0,
        'coverage' => ['source_id' => $id, 'scope_id' => str_repeat((string)($i + 1), 64), 'snapshot_id' => 'test-capture-' . $i, 'patch_id' => 'test-patch', 'query' => ['rank' => ['legend']], 'fragment' => '',
            'status' => $i === 0 ? 'partial' : 'complete_for_view', 'found' => 24, 'expected_count' => 24, 'listing_percent' => 100,
            'listing_complete' => true, 'view_confirmed' => true, 'details' => $counts, 'unresolved_details' => $i === 0 ? 12 : 0]];
}
$artifact = ['state' => 'available', 'payload' => ['schema_version' => 1, 'generated_at' => gmdate('Y-m-d\TH:i:s\Z'), 'sources' => $observations]];
$mode = is_string($_GET['mode'] ?? null) ? $_GET['mode'] : '';
if ($mode === 'missing') $artifact = ['state' => 'not_configured', 'payload' => null];
if ($mode === 'invalid') $artifact['payload']['sources'][0]['coverage']['status'] = 'complete_for_view';
if ($mode === 'xss') $catalog[0]['description'] = '<img src=x onerror=alert(1)> <script>alert(1)</script>';
$fetch = ['payload' => ['sources' => $catalog], 'cached' => false, 'stale_cache' => false, 'cache_age' => 0];
$definition = analytics_module_registry()['acquisition'];
if (($_GET['format'] ?? '') === 'json') {
    header('Content-Type: application/json; charset=utf-8');
    if ($mode === 'error') {
        http_response_code(503);
        echo json_encode(['ok' => false, 'message' => 'Тест: отчёт временно недоступен.']);
    } else {
        echo json_encode(analytics_acquisition_normalize($definition, $fetch, $artifact, analytics_safe_query($definition, $_GET)), JSON_HEX_TAG | JSON_HEX_AMP | JSON_THROW_ON_ERROR);
    }
    exit;
}
$_GET['stats'] = 'acquisition';
?>
<!doctype html>
<html lang="ru" data-theme="dark">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Покрытие источников · тест</title><link rel="icon" href="data:,"><link rel="stylesheet" href="/assets/style.css"></head>
<body>
<main class="shell">
    <aside class="sidebar"><div class="sidebar-brand"><span class="brand-mark">HS</span><div><strong>HS Data</strong><p>тестовые данные</p></div></div></aside>
    <section class="workspace"><header class="topbar"><div class="topbar-copy"><h1>Обзор и мета</h1><p>Тестовый просмотр · не production</p></div></header>
    <?php require dirname(__DIR__) . '/partials/analytics-dashboard.php'; ?>
    </section>
</main>
<script>
const testMode = <?= json_encode($mode, JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT) ?>;
const localFetch = window.fetch.bind(window);
window.fetch = (url, options) => {
    if (testMode === 'loading') return new Promise(() => {});
    const target = new URL('/tests/acquisition_panel_fixture.php', window.location.origin);
    target.search = new URL(url).search;
    target.searchParams.set('format', 'json');
    target.searchParams.set('mode', testMode);
    return localFetch(target, options);
};
</script>
<script src="/assets/analytics.js"></script><script src="/assets/panel-ui.js"></script>
</body></html>
