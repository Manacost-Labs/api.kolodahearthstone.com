<?php
declare(strict_types=1);
$action = 'parsers';
require __DIR__ . '/shell_fixture.php';

function h($value): string
{
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function panel_parser_control_csrf_token(): string
{
    return str_repeat('0', 64);
}
?>
<!doctype html>
<html lang="ru" data-theme="light">
<head>
    <script src="/assets/workspace.js" defer></script>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Parser panel fixture</title>
    <link rel="stylesheet" href="/assets/style.css">
    <link rel="stylesheet" href="/assets/workspace.css">
</head>
<body>
<main class="shell">
    <?php require __DIR__ . '/../partials/sidebar.php'; ?>
    <section class="workspace" id="main-content" tabindex="-1">
        <?php require __DIR__ . '/../partials/topbar.php'; ?>
        <?php require __DIR__ . '/../partials/parser-control.php'; ?>
    </section>
</main>
<?php require __DIR__ . '/../partials/command-palette.php'; ?>
<script>
const fixture = {
    generatedAt: new Date().toISOString(), revision: 4,
    sections: [
        {id:'meta',label:'Мета и архетипы',enabled:true,sourceCount:4,sources:[
            {id:'hsguru-meta-standard',label:'HSGuru · Standard meta',health:'ok',rowsTotal:184,lastSuccessAt:new Date(Date.now()-18*60000).toISOString(),lastAttemptAt:new Date(Date.now()-18*60000).toISOString(),schedule:'каждые 2 часа',nextRunAt:new Date(Date.now()+42*60000).toISOString(),publicationChannel:'stable',canRunManually:true},
            {id:'hsguru-meta-wild',label:'HSGuru · Wild meta',health:'warning',servingCachedDataset:true,rowsTotal:93,lastSuccessAt:new Date(Date.now()-9*3600000).toISOString(),lastAttemptAt:new Date(Date.now()-12*60000).toISOString(),lastError:'curl_cffi[1]: quality check failed: source contract failed: row_retrieval has unexplained dropped rows; flaresolverr[2]: quality check failed: source contract failed: row_retrieval has unexplained dropped rows',schedule:'каждые 2 часа',nextRunAt:new Date(Date.now()+42*60000).toISOString(),publicationChannel:'stable_baseline',canRunManually:true},
            {id:'hsreplay-archetypes',label:'HSReplay · архетипы',health:'ok',rowsTotal:341,lastSuccessAt:new Date(Date.now()-53*60000).toISOString(),lastAttemptAt:new Date(Date.now()-53*60000).toISOString(),schedule:'каждые 4 часа',nextRunAt:new Date(Date.now()+2*3600000).toISOString(),publicationChannel:'stable',canRunManually:true},
            {id:'new-source',label:'Новый источник без публикации',health:'error',rowsTotal:0,lastAttemptAt:new Date(Date.now()-7*60000).toISOString(),lastError:'origin timeout after 120 seconds',schedule:'каждые 2 часа',nextRunAt:new Date(Date.now()+42*60000).toISOString(),publicationChannel:'unavailable',canRunManually:true}
        ]},
        {id:'battlegrounds',label:'Поля сражений',enabled:true,sourceCount:2,sources:[
            {id:'bg-heroes',label:'Герои Battlegrounds',health:'ok',rowsTotal:105,lastSuccessAt:new Date(Date.now()-35*60000).toISOString(),lastAttemptAt:new Date(Date.now()-35*60000).toISOString(),schedule:'каждые 3 часа',nextRunAt:new Date(Date.now()+3600000).toISOString(),publicationChannel:'stable',canRunManually:true},
            {id:'bg-minions',label:'Существа Battlegrounds',health:'partial',servingCachedDataset:true,rowsTotal:612,lastSuccessAt:new Date(Date.now()-5*3600000).toISOString(),lastAttemptAt:new Date(Date.now()-8*60000).toISOString(),lastError:'Часть рейтинговых срезов временно недоступна',schedule:'каждые 3 часа',nextRunAt:new Date(Date.now()+3600000).toISOString(),publicationChannel:'stable_baseline',canRunManually:true}
        ]},
        {id:'arena',label:'Арена',enabled:false,sourceCount:1,sources:[
            {id:'arena-firestone',label:'Арена · Firestone',health:'ok',rowsTotal:288,lastSuccessAt:new Date(Date.now()-6*3600000).toISOString(),lastAttemptAt:new Date(Date.now()-6*3600000).toISOString(),schedule:'каждые 6 часов',publicationChannel:'stable',canRunManually:true}
        ]}
    ],
    activeRun:{id:'run-current',status:'running',reason:'Обновление после патча',requestedBy:'scheduler',createdAt:new Date(Date.now()-12*60000).toISOString(),startedAt:new Date(Date.now()-11*60000).toISOString(),totalSources:6,completedSources:4,failedSources:0},
    recentRuns:[
        {id:'run-current',status:'running',reason:'Обновление после патча',requestedBy:'scheduler',createdAt:new Date(Date.now()-12*60000).toISOString(),startedAt:new Date(Date.now()-11*60000).toISOString(),totalSources:6,completedSources:4,failedSources:0},
        {id:'run-old',status:'partial',reason:'Плановый сбор меты',requestedBy:'scheduler',createdAt:new Date(Date.now()-3*3600000).toISOString(),startedAt:new Date(Date.now()-3*3600000).toISOString(),finishedAt:new Date(Date.now()-2.8*3600000).toISOString(),totalSources:4,completedSources:3,failedSources:1,errors:['HSGuru Wild: сохранена стабильная предыдущая версия']}
    ]
};
// Deterministic, isolated browser harness. This fixture never calls the parser bridge.
const fixtureParams = new URL(location.href).searchParams;
window.parserFixture = {
    data: fixture,
    mode: fixtureParams.get('fixture_mode') || 'success',
    delay: Number(fixtureParams.get('fixture_delay')) || 0,
    calls: [],
};
window.fetch = async (url, options = {}) => {
    const harness = window.parserFixture;
    const mode = harness.mode;
    harness.calls.push({url, method: options.method || 'GET', body: options.body, headers: options.headers});
    await new Promise((resolve, reject) => {
        const timer = setTimeout(resolve, harness.delay);
        const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
        if (options.signal?.aborted) abort();
        else options.signal?.addEventListener('abort', abort, {once:true});
    });
    if (mode === 'offline') throw new TypeError('Тест: сеть недоступна');
    if (mode === 'error') return {ok:false,status:503,json:async()=>({ok:false,message:'Тест: сервис временно недоступен'})};
    if (options.method === 'POST') {
        if (mode === 'rejected') return {ok:false,status:429,json:async()=>({ok:false,message:'Лимит запусков. Попробуйте позже.'})};
        return {ok:true,status:200,json:async()=>({ok:true,data:{deduplicated:mode==='deduplicated'}})};
    }
    const data = mode === 'empty' ? {...fixture,sections:[],recentRuns:[],activeRun:null} : harness.data;
    return {ok:true,status:200,json:async()=>({ok:true,data})};
};
</script>
<script src="/assets/parser-control-view.js"></script>
<script src="/assets/parser-control.js"></script>
<script src="/assets/panel-ui.js"></script>
</body>
</html>
