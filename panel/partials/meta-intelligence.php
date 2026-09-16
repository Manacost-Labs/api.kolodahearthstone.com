<?php
declare(strict_types=1);
?>
<section id="meta-lab-root" class="panel meta-lab" data-meta-lab data-endpoint="/analytics.php" aria-label="Meta Intelligence Lab">
    <header class="meta-lab-heading">
        <div>
            <span class="eyebrow">Закрытая аналитика</span>
            <h2>Глубокий анализ меты</h2>
            <p>Сравнение сборок, эффективность карт, матчапы, сигналы и история изменений.</p>
        </div>
        <button class="button secondary mini-action" id="meta-refresh" type="button">Обновить</button>
    </header>

    <nav class="meta-tabs" aria-label="Разделы Meta Lab">
        <button class="button secondary mini-action active" type="button" data-meta-view="archetypes" aria-pressed="true">Архетипы</button>
        <button class="button secondary mini-action" type="button" data-meta-view="cards" aria-pressed="false">Карты</button>
        <button class="button secondary mini-action" type="button" data-meta-view="signals" aria-pressed="false">Сигналы</button>
        <button class="button secondary mini-action" type="button" data-meta-view="health" aria-pressed="false">Качество данных</button>
    </nav>

    <section class="meta-card meta-toolbar" id="meta-filters">
        <div class="meta-format" role="group" aria-label="Формат">
            <button type="button" data-meta-format="standard" aria-pressed="true">Standard</button>
            <button type="button" data-meta-format="wild" aria-pressed="false">Wild</button>
        </div>
        <label>Срез данных<select id="meta-scope" aria-label="Ранг, период и патч"></select></label>
        <label>Поиск<input id="meta-search" type="search" autocomplete="off" placeholder="Название архетипа"></label>
    </section>

    <div class="meta-summary" id="meta-summary" aria-label="Сводка Meta Lab"></div>
    <div id="meta-error" class="meta-error" role="alert" hidden></div>
    <div id="meta-content" aria-busy="true">
        <div class="meta-loading" aria-live="polite">Загружаем аналитику…</div>
    </div>

    <dialog class="meta-dialog" id="meta-dialog">
        <div class="meta-dialog-head">
            <h3 id="meta-dialog-title">Подробности</h3>
            <button class="button secondary mini-action" type="button" data-meta-close>Закрыть</button>
        </div>
        <div id="meta-dialog-body"></div>
    </dialog>
</section>
