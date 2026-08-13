<?php
declare(strict_types=1);

$panelQuickLinks = [
    'Данные' => [
        ['Карты Полей сражений', '/', 'Карты, существа, заклинания и золотые варианты'],
        ['Герои', '/?card_type=hero', 'Герои, силы и компаньоны'],
        ['Скины героев', '/?card_type=hero_skin', 'Косметические варианты героев'],
        ['Стандарт и Вольный', '/?card_type=constructed', 'Полная библиотека constructed-карт'],
        ['Хрономальные карты', '/?card_type=timewarped', 'Отдельная коллекция хрономальных карт'],
    ],
    'Аналитика и операции' => [
        ['Обзор и мета', '/?action=analytics#statistics', 'Источники, архетипы, карты, Arena и Battlegrounds'],
        ['Парсеры', '/?action=parsers', 'Свежесть, расписание и история запусков'],
    ],
    'Управление' => [
        ['API-токены', '/?action=api_tokens', 'Доступ, квоты и использование API'],
        ['Аномалии', '/?card_type=anomaly', 'Библиотека аномалий'],
        ['Квесты', '/?card_type=quest', 'Квесты Полей сражений'],
        ['Аксессуары', '/?card_type=trinket', 'Обычные и большие аксессуары'],
    ],
];
?>
<dialog class="command-palette" data-command-palette aria-labelledby="commandPaletteTitle">
    <div class="command-palette-shell">
        <h2 class="visually-hidden" id="commandPaletteTitle">Быстрый переход</h2>
        <header class="command-palette-search">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m20 20-4.3-4.3m2.3-5.2a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z"/></svg>
            <label class="visually-hidden" for="commandPaletteSearch">Быстрый переход</label>
            <input id="commandPaletteSearch" type="search" placeholder="Перейти к разделу…" autocomplete="off" data-command-search>
            <button type="button" aria-label="Закрыть быстрый переход" data-command-close>Esc</button>
        </header>
        <div class="command-palette-results" data-command-results>
            <?php foreach ($panelQuickLinks as $group => $links): ?>
                <section data-command-group>
                    <h2><?= h($group) ?></h2>
                    <?php foreach ($links as [$label, $href, $description]): ?>
                        <a href="<?= h($href) ?>" data-command-item data-command-text="<?= h(mb_strtolower($label . ' ' . $description, 'UTF-8')) ?>">
                            <span><b><?= h($label) ?></b><small><?= h($description) ?></small></span>
                            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>
                        </a>
                    <?php endforeach; ?>
                </section>
            <?php endforeach; ?>
            <p class="command-palette-empty" data-command-empty hidden>Раздел не найден.</p>
        </div>
        <footer><span><kbd>↑</kbd><kbd>↓</kbd> навигация</span><span><kbd>Enter</kbd> открыть</span></footer>
    </div>
</dialog>
