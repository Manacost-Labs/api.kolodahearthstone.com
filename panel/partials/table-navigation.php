<?php
declare(strict_types=1);

$tableNavigationTarget = (string)($tableNavigationTarget ?? 'table');
$tableNavigationLabel = (string)($tableNavigationLabel ?? 'Широкая таблица');
?>
<div class="table-navigation" data-table-navigation data-table-target="<?= h($tableNavigationTarget) ?>" hidden>
    <div class="table-navigation-copy">
        <span><?= h($tableNavigationLabel) ?></span>
        <b data-table-scroll-status>Начало</b>
    </div>
    <div class="table-navigation-actions">
        <button type="button" data-table-scroll-left aria-label="Прокрутить таблицу влево">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m15 18-6-6 6-6"/></svg>
        </button>
        <button type="button" data-table-scroll-right aria-label="Прокрутить таблицу вправо">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>
        </button>
    </div>
</div>
