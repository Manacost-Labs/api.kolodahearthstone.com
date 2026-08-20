<?php
declare(strict_types=1);

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
<html lang="ru" data-theme="dark">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Parser panel fixture</title>
    <link rel="stylesheet" href="/assets/style.css">
</head>
<body>
<main class="shell">
    <aside class="sidebar">
        <div class="sidebar-brand"><span class="brand-mark">HS</span><div><strong>HS Data</strong><p>центр управления данными</p></div></div>
        <nav class="side-nav">
            <section class="side-section"><h2>Основное</h2><a class="side-link" href="#"><span>Карты BG</span><b>1240</b></a><a class="side-link" href="#"><span>Герои</span><b>105</b></a></section>
            <section class="side-section"><h2>Операции</h2><a class="side-link active" href="#"><span>Парсеры</span><b>Live</b></a></section>
        </nav>
    </aside>
    <section class="workspace">
        <header class="topbar"><div class="topbar-copy"><h1>Парсеры</h1></div><div class="panel-account"><span class="panel-account-name"><i></i>GitHub · Zulut30</span></div></header>
        <?php require __DIR__ . '/../partials/parser-control.php'; ?>
    </section>
</main>
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
window.fetch = async () => ({ok:true,status:200,json:async()=>({ok:true,data:fixture})});
</script>
<script src="/assets/parser-control-view.js"></script>
<script src="/assets/parser-control.js"></script>
<script src="/assets/panel-ui.js"></script>
</body>
</html>
