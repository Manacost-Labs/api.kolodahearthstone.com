<?php
declare(strict_types=1);
require_once __DIR__ . '/../lib/panel_shell.php';
$catalogActive = !in_array($action, ['analytics', 'parsers', 'api_tokens'], true);
$catalogLinks = [
    ['', 'Карты BG', $total ?? null],
    ['hero', 'Герои', $heroTotal ?? null],
    ['hero_skin', 'Скины героев', $heroSkinsTotal ?? null],
    ['pet', 'Питомцы', $petsTotal ?? null],
    ['coin', 'Монетки', $coinsTotal ?? null],
    ['timewarped', 'Хрономальные', $timewarpedTotal ?? null],
    ['constructed', 'Стандарт / Вольный', $constructedTotal ?? null],
    ['anomaly', 'Аномалии', null], ['quest', 'Квесты', null],
    ['darkmoon_prize', 'Призы', null], ['reward', 'Награды', null], ['trinket', 'Аксессуары', null],
];
?>
<aside class="sidebar" aria-label="Навигация по базе">
    <div class="sidebar-brand">
        <a class="sidebar-home" href="/" aria-label="HS Data — каталог">
            <span class="brand-mark" aria-hidden="true"><?= panel_icon('catalog') ?></span>
            <span><strong>HS Data</strong><small>Панель управления</small></span>
        </a>
        <button class="sidebar-toggle" type="button" aria-controls="sidebarNav" aria-expanded="false" data-sidebar-toggle>
            <?= panel_icon('menu') ?><span>Меню</span>
        </button>
    </div>
    <nav class="side-nav" id="sidebarNav" aria-label="Разделы панели">
        <a class="side-link<?= $action === 'analytics' ? ' active' : '' ?>" href="/?action=analytics"<?= $action === 'analytics' ? ' aria-current="page"' : '' ?>>
            <?= panel_icon('chart') ?><span>Обзор и статистика</span>
        </a>
        <details class="sidebar-catalog"<?= $catalogActive ? ' open' : '' ?>>
            <summary><?= panel_icon('catalog') ?><span>Каталог карт</span><span class="nav-chevron" aria-hidden="true">›</span></summary>
            <div class="sidebar-catalog-links">
                <?php foreach ($catalogLinks as [$type, $label, $count]): $active = $action === 'list' && (string)$cardType === $type; ?>
                    <a class="side-link<?= $active ? ' active' : '' ?>" href="<?= $type === '' ? '/' : '/?card_type=' . h($type) ?>"<?= $active ? ' aria-current="page"' : '' ?>>
                        <span><?= h($label) ?></span><?php if ($count !== null): ?><b><?= h($count) ?></b><?php endif; ?>
                    </a>
                <?php endforeach; ?>
            </div>
        </details>
        <a class="side-link<?= $action === 'parsers' ? ' active' : '' ?>" href="/?action=parsers"<?= $action === 'parsers' ? ' aria-current="page"' : '' ?>>
            <?= panel_icon('source') ?><span>Источники данных</span>
        </a>
        <a class="side-link<?= $action === 'api_tokens' ? ' active' : '' ?>" href="/?action=api_tokens"<?= $action === 'api_tokens' ? ' aria-current="page"' : '' ?>>
            <?= panel_icon('key') ?><span>Доступ к API</span>
        </a>
    </nav>
    <div class="sidebar-footer">
        <span class="sidebar-footer-label">Оформление</span>
        <div class="theme-switcher" role="group" aria-label="Тема панели">
            <button class="theme-button" type="button" data-theme-option="light">Светлая</button>
            <button class="theme-button" type="button" data-theme-option="dark">Темная</button>
            <button class="theme-button" type="button" data-theme-option="tavern">Таверна</button>
            <button class="theme-button" type="button" data-theme-option="arcane">Аркана</button>
        </div>
    </div>
</aside>
