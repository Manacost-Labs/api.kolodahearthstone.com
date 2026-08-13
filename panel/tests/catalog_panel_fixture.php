<?php
declare(strict_types=1);

function h($value): string
{
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}
$fixtureEmpty = isset($_GET['empty']);
$horizontalArtUrl = 'https://api.kolodahearthstone.com/uploads/horizontal-art/battleground_card/BG28_897.webp';
?>
<!doctype html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Каталог · UI fixture</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%232563eb'/%3E%3C/svg%3E">
    <link rel="stylesheet" href="/assets/style.css?v=32">
    <script src="/assets/panel-ui.js?v=2" defer></script>
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
            <div class="topbar-actions"><button class="topbar-command" type="button" data-command-open><svg viewBox="0 0 24 24"><path d="m20 20-4.3-4.3m2.3-5.2a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z"/></svg><span>Быстрый переход</span><kbd>⌘ K</kbd></button><div class="panel-account"><span class="panel-account-name"><i></i>GitHub · Zulut30</span></div></div>
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
            <nav class="pagination"><span class="page-link disabled">Назад</span><span class="page-link active">1</span><a class="page-link" href="#">2</a><a class="page-link" href="#">3</a><a class="page-link" href="#">Вперёд</a><span class="page-summary">Страница 1 из 25</span></nav>
            <?php $tableNavigationTarget = '.cards-table'; $tableNavigationLabel = 'Широкая таблица'; require __DIR__ . '/../partials/table-navigation.php'; ?>
            <div class="cards-table">
                <table>
                    <thead><tr><th>Карта</th><th>Crop</th><th>Тип</th><th>Таверна</th><th>Характеристики</th><th>Механики</th><th>Пул</th><th>Обновлено</th><th>Действия</th></tr></thead>
                    <tbody>
                    <?php foreach ([['Мурлок-разведчик','Существо','1','2 / 3','Боевой клич','В пуле'],['Золотой дракон','Существо','4','6 / 8','Божественный щит','В пуле'],['Призыв таверны','Заклинание','3','—','Обновление','В пуле'],['Ночной охотник','Существо','5','8 / 7','Предсмертный хрип','Не в пуле']] as $index => $row): ?>
                        <tr><td class="card-name"><span class="variant-preview"></span><div><strong><?= h($row[0]) ?></strong><small>BG_FIXTURE_<?= $index + 1 ?></small></div></td><td><figure class="horizontal-art-preview"><button type="button" class="horizontal-art-button" data-preview="<?= h($horizontalArtUrl) ?>" data-tooltip="<?= h($row[0] . "\nГоризонтальный crop · 320×64 WebP") ?>" aria-label="Открыть горизонтальный crop: <?= h($row[0]) ?>"><img src="<?= h($horizontalArtUrl) ?>" alt="" loading="lazy" decoding="async" width="160" height="32"></button><figcaption><span>Crop 320×64</span><a href="<?= h($horizontalArtUrl) ?>" target="_blank" rel="noopener">URL</a></figcaption></figure></td><td><?= h($row[1]) ?></td><td><?= h($row[2]) ?></td><td><?= h($row[3]) ?></td><td><?= h($row[4]) ?></td><td><span class="badge"><?= h($row[5]) ?></span></td><td>13 авг., 00:18</td><td class="row-actions"><button class="button ghost">Подробнее</button></td></tr>
                    <?php endforeach; ?>
                    </tbody>
                </table>
            </div>
            <?php endif; ?>
        </section>
    </section>
</main>
<?php require __DIR__ . '/../partials/command-palette.php'; ?>
</body>
</html>
