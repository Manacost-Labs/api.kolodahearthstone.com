<?php
declare(strict_types=1);

function h($value): string
{
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

$_GET['stats'] = 'overview';
?>
<!doctype html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Статистика · UI fixture</title>
    <link rel="stylesheet" href="/assets/style.css?v=31">
    <script src="/assets/panel-ui.js?v=2" defer></script>
</head>
<body>
<main class="shell">
    <aside class="sidebar">
        <div class="sidebar-brand"><span class="brand-mark">HS</span><div><strong>HS Data</strong><p>центр управления данными</p></div></div>
        <nav class="side-nav">
            <section class="side-section"><h2>Основное</h2><a class="side-link" href="#"><span>Карты BG</span><b>1240</b></a><a class="side-link" href="#"><span>Герои</span><b>105</b></a></section>
            <section class="side-section"><h2>Статистика</h2><a class="side-link active" href="#"><span>Обзор и мета</span><b>Live</b></a></section>
            <section class="side-section"><h2>Операции</h2><a class="side-link" href="#"><span>Парсеры</span><b>Live</b></a></section>
        </nav>
    </aside>
    <section class="workspace">
        <header class="topbar">
            <div class="topbar-copy"><span class="topbar-context">Аналитика</span><div><h1>Обзор и мета</h1></div></div>
            <div class="topbar-actions"><button class="topbar-command" type="button"><span>Быстрый переход</span><kbd>⌘ K</kbd></button><div class="panel-account"><span class="panel-account-name"><i></i>GitHub · Zulut30</span></div></div>
        </header>
        <?php require __DIR__ . '/../partials/analytics-dashboard.php'; ?>
    </section>
</main>
<script>
window.fetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
        ok: true,
        title: 'Состояние источников',
        description: 'Актуальность всех наборов и последняя успешная публикация.',
        summary: [
            {label: 'Источники', value: '98', tone: 'neutral'},
            {label: 'Работают', value: '91', tone: 'good'},
            {label: 'Внимание', value: '5', tone: 'warning'},
            {label: 'Ошибки', value: '2', tone: 'bad'}
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
    })
});
</script>
<script src="/assets/analytics.js?v=4"></script>
</body>
</html>
