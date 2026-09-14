<?php
declare(strict_types=1);

function h($value): string
{
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}
$fixtureEmpty = isset($_GET['empty']);
$horizontalArtUrl = 'https://api.kolodahearthstone.com/uploads/horizontal-art/battleground_card/BG28_897.webp';
$cardImageUrl = 'https://api.kolodahearthstone.com/uploads/cards/BG28_897.png';
?>
<!doctype html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Каталог · UI fixture</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%232563eb'/%3E%3C/svg%3E">
    <link rel="stylesheet" href="/assets/style.css?v=37">
    <script src="/assets/catalog-workspace-view.js?v=1" defer></script>
    <script src="/assets/panel-ui.js?v=3" defer></script>
</head>
<body>
<main class="shell">
    <aside class="sidebar">
        <div class="sidebar-brand"><span class="brand-mark">HS</span><div><strong>HS Data</strong><p>центр управления данными</p></div><button class="sidebar-toggle" type="button" data-sidebar-toggle aria-expanded="false">Меню</button></div>
        <nav class="side-nav">
            <section class="side-section"><h2>Основное</h2><a class="side-link active" href="#"><span>Карты BG</span><b>1240</b></a><a class="side-link" href="#"><span>Герои</span><b>105</b></a><a class="side-link" href="#"><span>Скины героев</span><b>284</b></a></section>
            <section class="side-section"><h2>Статистика</h2><a class="side-link" href="#"><span>Обзор и мета</span><b>Live</b></a></section>
            <section class="side-section"><h2>Операции</h2><a class="side-link" href="#"><span>Парсеры</span><b>Live</b></a></section>
        </nav>
    </aside>
    <section class="workspace">
        <header class="topbar">
            <div class="topbar-copy"><span class="topbar-context">База данных</span><div><h1>Карты Полей сражений</h1><span class="result-range">1–50 из 1240</span></div></div>
            <div class="topbar-actions"><button class="topbar-command" type="button" data-command-open aria-label="Быстрый переход" aria-haspopup="dialog"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m20 20-4.3-4.3m2.3-5.2a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z"/></svg><span>Быстрый переход</span><kbd>⌘ K</kbd></button><div class="panel-account"><span class="panel-account-name"><i></i>GitHub · Zulut30</span></div></div>
        </header>
        <section class="panel data-panel">
            <div class="list-head">
                <form class="filters">
                    <label class="filter-search"><span>Поиск</span><span class="search-field"><input type="search" placeholder="Название, ID, DBF, текст или механика" data-filter-search><kbd>/</kbd></span></label>
                    <div class="filter-controls">
                        <button class="filter-toggle" type="button" data-filter-toggle><span>Фильтры</span></button>
                        <select><option>Все карты</option></select><select><option>Все уровни</option></select><select><option>Все типы</option></select><select><option>Любой пул</option></select>
                        <button class="button" type="button">Найти</button><button class="button ghost" type="button">Сброс</button>
                        <button class="table-density-toggle" type="button" data-table-density>Компактно</button>
                        <details class="table-column-picker" data-column-picker data-table-target=".cards-table > table" data-storage-key="fixture-catalog"><summary>Колонки</summary><div class="column-picker-menu" data-column-picker-menu></div></details>
                    </div>
                </form>
            </div>
            <div class="hero-coverage-strip"><span>Всего карт <b>1240</b></span><span>В пуле <b>804</b></span><span>Golden <b>1188</b></span><span class="is-ok">Изображения <b>99.4%</b></span></div>
            <?php if ($fixtureEmpty): ?>
            <section class="catalog-empty" role="status"><span class="catalog-empty-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m20 20-4.3-4.3m2.3-5.2a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z"/></svg></span><div><h2>По этим условиям ничего не найдено</h2><p>Сбросьте часть фильтров или измените поисковый запрос.</p></div><button class="button secondary" type="button">Сбросить фильтры</button></section>
            <?php else: ?>
            <div class="catalog-workbench" data-catalog-workbench>
            <section class="catalog-list-pane" aria-label="Список карт">
            <nav class="pagination"><span class="page-link disabled">Назад</span><span class="page-link active">1</span><a class="page-link" href="?page=2">2</a><a class="page-link" href="?page=3">3</a><a class="page-link" href="?page=2">Вперёд</a><span class="page-summary">Страница 1 из 25</span></nav>
            <div class="cards-table">
                <table class="battlegrounds-table">
                    <thead><tr><th class="catalog-col-card">Карта</th><th class="catalog-col-type">Категория</th><th class="catalog-col-tier">Таверна</th><th class="catalog-col-stat">Атака</th><th class="catalog-col-stat">Здоровье</th><th class="catalog-col-action">Просмотр</th></tr></thead>
                    <tbody>
                    <?php foreach ([['Мурлок-разведчик','Существо','1','2','3','Боевой клич','В пуле'],['Золотой дракон','Существо','4','6','8','Божественный щит','В пуле'],['Призыв таверны','Заклинание','3','—','—','Обновление','В пуле'],['Ночной охотник','Существо','5','8','7','Предсмертный хрип','Не в пуле']] as $index => $row): ?>
                        <tr data-catalog-record data-record-id="BG_FIXTURE_<?= $index + 1 ?>" data-record-dbf="<?= 69042 + $index ?>" data-record-name="<?= h($row[0]) ?>" data-record-english-name="Fixture card <?= $index + 1 ?>" data-record-image="<?= h($cardImageUrl) ?>" data-record-type="<?= h($row[1]) ?>" data-record-tier="<?= h($row[2]) ?>" data-record-attack="<?= h($row[3]) ?>" data-record-health="<?= h($row[4]) ?>" data-record-mechanics="<?= h($row[5]) ?>" data-record-updated="14 сент. 2026, 13:42 UTC" data-record-pool="<?= h($row[6]) ?>" data-record-duo="Обычный режим" data-record-edit-url="#edit" data-record-stats-url="#statistics" tabindex="0" aria-selected="false"><td class="card-name catalog-col-card"><img src="<?= h($cardImageUrl) ?>" alt="<?= h($row[0]) ?>" loading="lazy" decoding="async"><span class="card-name-copy"><span><?= h($row[0]) ?></span><code>BG_FIXTURE_<?= $index + 1 ?></code></span></td><td class="catalog-col-type"><span class="type-badge"><?= h($row[1]) ?></span></td><td class="catalog-col-tier"><?= h($row[2]) ?></td><td class="catalog-col-stat"><?= h($row[3]) ?></td><td class="catalog-col-stat"><?= h($row[4]) ?></td><td class="row-actions catalog-col-action"><button class="mini" type="button" data-catalog-open>Подробнее</button></td></tr>
                    <?php endforeach; ?>
                    </tbody>
                </table>
            </div>
            </section>
            <aside class="catalog-inspector" data-catalog-inspector aria-label="Детали выбранной карты" hidden>
                <header class="catalog-inspector-head"><div><span>Выбранная карта</span><h2 data-inspector-field="name">Детали карты</h2><code data-inspector-field="id">—</code></div><button class="button ghost" type="button" data-inspector-close>Закрыть</button></header>
                <div class="catalog-inspector-media"><img src="" alt="" data-inspector-image hidden><p data-inspector-image-empty>Изображение отсутствует</p></div>
                <dl class="catalog-inspector-facts"><div><dt>Название EN</dt><dd data-inspector-field="englishName">—</dd></div><div><dt>DBF ID</dt><dd data-inspector-field="dbf">—</dd></div><div><dt>Категория</dt><dd data-inspector-field="type">—</dd></div><div><dt>Таверна</dt><dd data-inspector-field="tier">—</dd></div><div><dt>Атака</dt><dd data-inspector-field="attack">—</dd></div><div><dt>Здоровье</dt><dd data-inspector-field="health">—</dd></div><div><dt>Пул</dt><dd data-inspector-field="pool">—</dd></div><div><dt>Режим</dt><dd data-inspector-field="duo">—</dd></div><div><dt>Обновлено</dt><dd data-inspector-field="updated">—</dd></div></dl>
                <section class="catalog-inspector-section"><h3>Механики</h3><div class="catalog-inspector-mechanics" data-inspector-mechanics></div></section>
                <div class="catalog-inspector-actions"><a class="button" href="#" data-inspector-link="edit">Править</a><a class="button ghost" href="#" data-inspector-link="stats">Статистика</a></div>
            </aside>
            </div>
            <?php endif; ?>
        </section>
    </section>
</main>
<?php require __DIR__ . '/../partials/command-palette.php'; ?>
</body>
</html>
