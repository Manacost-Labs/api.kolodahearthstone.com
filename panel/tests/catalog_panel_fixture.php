<?php
declare(strict_types=1);
$action = 'list';
require __DIR__ . '/shell_fixture.php';
function h($value): string { return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8'); }
require __DIR__ . '/catalog_fixture_state.php';
?>
<!doctype html>
<html lang="ru" data-theme="light">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Каталог · UI fixture</title>
    <link rel="icon" href="data:,">
    <link rel="stylesheet" href="/assets/style.css">
    <link rel="stylesheet" href="/assets/workspace.css">
    <script src="/assets/workspace.js" defer></script>
    <script src="/assets/panel-ui.js" defer></script>
    <script src="/assets/table-controls.js" defer></script>
</head>
<body>
<main class="shell">
    <?php require __DIR__ . '/../partials/sidebar.php'; ?>
    <section class="workspace" id="main-content" tabindex="-1">
        <?php require __DIR__ . '/../partials/topbar.php'; ?>
        <?php require __DIR__ . '/../partials/catalog-heading.php'; ?>
        <section class="panel data-panel">
            <?php require __DIR__ . '/../partials/catalog-controls.php'; ?>
            <?php if ($fixtureEmpty): ?>
                <section class="catalog-empty" role="status">
                    <div><h2>По этим условиям ничего не найдено</h2><p>Сбросьте часть фильтров или измените поисковый запрос.</p></div>
                    <a class="button secondary" href="<?= h($resetUrl) ?>">Сбросить фильтры</a>
                </section>
            <?php else: ?>
                <?php if ($totalPages > 1) { $paginationBottom = false; require __DIR__ . '/../partials/catalog-pagination.php'; } ?>
                <?php $tableNavigationTarget = '.cards-table'; $tableNavigationLabel = 'Широкая таблица'; require __DIR__ . '/../partials/table-navigation.php'; ?>
                <div class="cards-table">
                    <table>
                        <thead><tr>
                            <?php foreach (['Карта','Card EN','Crop','CARD_ID','DBF','Категория','Таверна','Тип','Атака','Здоровье','В пуле','Дуо','Механики','Золотая','Арт','Рамка','Wiki','Действия'] as $label): ?>
                                <th scope="col"><?= h($label) ?></th>
                            <?php endforeach; ?>
                        </tr></thead>
                        <tbody>
                            <?php foreach ($fixtureRows as $row): ?>
                            <tr>
                                <td class="card-name"><div><strong><?= h($row['name']) ?></strong><small><?= h($row['id']) ?></small></div></td>
                                <td>Fixture minion</td><td>—</td><td><code><?= h($row['id']) ?></code></td><td>10001</td><td>Существо</td>
                                <td><?= h($row['tier']) ?></td><td>Мурлок</td><td>2</td><td>3</td>
                                <td><span class="badge">Да</span></td><td>Нет</td><td>Боевой клич</td>
                                <td>—</td><td>—</td><td>—</td><td>—</td>
                                <td class="row-actions"><span class="muted">Тестовая запись</span></td>
                            </tr>
                            <?php endforeach; ?>
                        </tbody>
                    </table>
                </div>
                <?php if ($totalPages > 1) { $paginationBottom = true; require __DIR__ . '/../partials/catalog-pagination.php'; } ?>
            <?php endif; ?>
        </section>
    </section>
</main>
<?php require __DIR__ . '/../partials/command-palette.php'; ?>
</body>
</html>
