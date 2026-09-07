<?php
declare(strict_types=1);
require_once __DIR__ . '/../lib/catalog_navigation.php';
$sort = panel_catalog_sort($sort ?? ($_GET['sort'] ?? null));
$catalogAdvancedActive = $tier !== '' || $creatureType !== '' || $pool !== '' || $duos !== ''
    || $media !== '' || $skinRarity !== '' || $constructedFormat !== 'all';
$catalogIsGallery = $showPets || $showCoins || $showHeroSkins;
$catalogHiddenColumns = '1,2,3,4,5,11,13,14,15,16';
if ($showConstructed) $catalogHiddenColumns = '1,2,4,10';
elseif ($showHeroes) $catalogHiddenColumns = '1,3,4,8';
elseif ($showTimewarped) $catalogHiddenColumns = '1,2,3,4,9';
elseif ($showLibrary) $catalogHiddenColumns = $libraryType === 'trinket' ? '2,4,5,9,10' : '1,3,4,8,9';
?>
        <div class="list-head">
            <div class="catalog-toolbar-head">
                <div><h2>Каталог</h2><p>Показано <?= $pageFrom ?>–<?= $pageTo ?> из <?= $filteredTotal ?></p></div>
                <?php if (!$catalogIsGallery): ?>
                <div class="catalog-view-actions">
                    <button class="table-density-toggle" type="button" data-table-density aria-pressed="false">Компактно</button>
                    <details class="table-column-picker" data-column-picker data-table-target=".cards-table > table" data-storage-key="catalogue-<?= h($cardType !== '' ? $cardType : 'battlegrounds') ?>" data-default-hidden="<?= h($catalogHiddenColumns) ?>">
                        <summary>Колонки</summary>
                        <div class="column-picker-menu" data-column-picker-menu></div>
                    </details>
                </div>
                <?php endif; ?>
            </div>
            <form class="filters" method="get" data-autofilter>
                <label class="filter-search">
                    <span>Поиск</span>
                    <span class="search-field">
                        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m20 20-4.3-4.3m2.3-5.2a7.5 7.5 0 1 1-15 0 7.5 7.5 0 0 1 15 0Z"/></svg>
                        <input type="search" name="q" value="<?= h($q) ?>" placeholder="<?= $showHeroSkins ? 'Скин, character, actor, class, category' : ($showPets ? 'Питомец, вариант, id, dbf' : ($showCoins ? 'Coin, RU, id, dbf, художник' : ($showHeroes ? 'Герой, сила, компаньон, художник' : 'Название, ID, DBF, текст или механика'))) ?>" autocomplete="off" data-filter-search aria-keyshortcuts="/">
                        <kbd aria-hidden="true">/</kbd>
                    </span>
                </label>
                <label class="catalog-type"><span>Раздел</span>
                <select name="card_type" aria-label="Раздел базы">
                    <option value="">Все карты BG</option>
                    <?php foreach (filter_card_types() as $value => $label): ?>
                        <option value="<?= h($value) ?>"<?= $cardType === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                    <?php endforeach; ?>
                </select>
                </label>
                <button class="button catalog-search-submit" type="submit">Найти</button>
                <div class="catalog-browse-controls">
                    <label><span>Сортировка</span>
                        <select name="sort" aria-label="Сортировка всей выборки">
                            <?php foreach (panel_catalog_sort_options() as $value => $label): ?>
                                <option value="<?= h($value) ?>"<?= $sort === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                            <?php endforeach; ?>
                        </select>
                    </label>
                    <label><span>На странице</span>
                        <select name="per_page" aria-label="Карт на странице">
                            <?php foreach ([25, 50, 100, 150] as $size): ?>
                                <option value="<?= $size ?>"<?= $perPage === $size ? ' selected' : '' ?>><?= $size ?></option>
                            <?php endforeach; ?>
                        </select>
                    </label>
                    <span class="catalog-request-status" data-catalog-request-status role="status" aria-live="polite"></span>
                </div>
                <details class="catalog-more"<?= $catalogAdvancedActive ? ' open' : '' ?>>
                <summary>Дополнительные фильтры</summary>
                <div class="filter-controls filters-open">
                <?php if (!$showHeroes && !$showHeroSkins && !$showCoins && !$showConstructed && (!$showLibrary || $libraryType === 'darkmoon_prize')): ?>
                    <select name="tier" aria-label="Уровень таверны">
                        <option value=""><?= $showPets ? 'Все уровни питомца' : ($showLibrary && $libraryType === 'darkmoon_prize' ? 'Все тиры' : 'Все уровни') ?></option>
                        <?php for ($i = 1; $i <= (($showLibrary && $libraryType === 'darkmoon_prize') || $showPets ? 4 : 7); $i++): ?>
                            <option value="<?= $i ?>"<?= $tier === (string)$i ? ' selected' : '' ?>><?= $showPets ? 'Уровень ' : ($showLibrary && $libraryType === 'darkmoon_prize' ? 'Тир ' : 'Таверна ') ?><?= $i ?></option>
                        <?php endfor; ?>
                    </select>
                <?php endif; ?>
                <?php if (!$showHeroes && !$showHeroSkins && !$showPets && !$showCoins && !$showConstructed && !$showLibrary): ?>
                    <select name="creature_type" aria-label="Тип существа">
                        <option value="">Все типы</option>
                        <?php foreach (creature_types() as $value => $label): ?>
                            <option value="<?= h($value) ?>"<?= $creatureType === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                        <?php endforeach; ?>
                    </select>
                <?php endif; ?>
                <?php if ($showConstructed): ?>
                    <select name="constructed_format" aria-label="Формат игры">
                        <option value="all"<?= $constructedFormat === 'all' ? ' selected' : '' ?>>Стандарт + Вольный</option>
                        <option value="standard"<?= $constructedFormat === 'standard' ? ' selected' : '' ?>>Стандартный</option>
                        <option value="wild"<?= $constructedFormat === 'wild' ? ' selected' : '' ?>>Вольный</option>
                    </select>
                    <select name="media" aria-label="Медиа и качество">
                        <option value="">Все качества</option>
                        <option value="golden"<?= $media === 'golden' ? ' selected' : '' ?>>Есть Golden</option>
                        <option value="signature"<?= $media === 'signature' ? ' selected' : '' ?>>Есть Signature</option>
                        <option value="diamond"<?= $media === 'diamond' ? ' selected' : '' ?>>Есть Diamond</option>
                        <option value="animated_diamond"<?= $media === 'animated_diamond' ? ' selected' : '' ?>>Есть Animated Diamond</option>
                    </select>
                <?php endif; ?>
                <?php if ($showHeroes): ?>
                    <select name="media" aria-label="Медиа и качество">
                        <option value="">Все герои</option>
                        <?php foreach ($mediaLabels as $value => $label): ?>
                            <option value="<?= h($value) ?>"<?= $media === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                        <?php endforeach; ?>
                    </select>
                <?php endif; ?>
                <?php if ($showHeroSkins): ?>
                    <select name="rarity" aria-label="Редкость скина">
                        <option value="">Любая редкость</option>
                        <?php foreach ($skinRarityLabels as $value => $label): ?>
                            <option value="<?= h($value) ?>"<?= $skinRarity === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                        <?php endforeach; ?>
                    </select>
                    <select name="media" aria-label="Медиа и качество">
                        <option value="">Все скины</option>
                        <?php foreach ($skinMediaLabels as $value => $label): ?>
                            <option value="<?= h($value) ?>"<?= $media === $value ? ' selected' : '' ?>><?= h($label) ?></option>
                        <?php endforeach; ?>
                    </select>
                <?php endif; ?>
                <?php if ($showPets): ?>
                    <select name="media" aria-label="Медиа и качество">
                        <option value="">Все питомцы</option>
                        <option value="background"<?= $media === 'background' ? ' selected' : '' ?>>Есть end screen</option>
                        <option value="gallery"<?= $media === 'gallery' ? ' selected' : '' ?>>Есть Gallery</option>
                    </select>
                <?php endif; ?>
                <?php if (!$showHeroes && !$showHeroSkins && !$showPets && !$showCoins && !$showTimewarped && !$showConstructed): ?>
                    <select name="pool" aria-label="Статус в пуле">
                        <option value="">Любой пул</option>
                        <option value="1"<?= $pool === '1' ? ' selected' : '' ?>>В пуле</option>
                        <option value="0"<?= $pool === '0' ? ' selected' : '' ?>>Не в пуле</option>
                    </select>
                <?php endif; ?>
                <?php if (!$showHeroes && !$showHeroSkins && !$showPets && !$showCoins && !$showTimewarped && !$showConstructed && !$showLibrary): ?>
                    <select name="duos" aria-label="Режим игры">
                        <option value="">Любой режим</option>
                        <option value="1"<?= $duos === '1' ? ' selected' : '' ?>>Только дуо</option>
                        <option value="0"<?= $duos === '0' ? ' selected' : '' ?>>Не только дуо</option>
                    </select>
                <?php endif; ?>
                <a class="button ghost" href="<?= h($resetUrl) ?>">Сбросить фильтры</a>
                </div>
                </details>
            </form>
            <?php if ($activeFilters): ?>
                <div class="active-filters" aria-label="Активные фильтры">
                    <?php foreach ($activeFilters as $filter): ?>
                        <a href="<?= h($filter['href']) ?>"><?= h($filter['label']) ?><span aria-hidden="true">×</span></a>
                    <?php endforeach; ?>
                </div>
            <?php endif; ?>
        </div>
