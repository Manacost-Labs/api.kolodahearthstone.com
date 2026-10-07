<dialog class="catalog-inspector" data-catalog-inspector aria-labelledby="catalogInspectorName">
    <header class="catalog-inspector-head">
        <div><span>Детали карты</span><h2 id="catalogInspectorName" data-inspector-field="name">Карта</h2><code data-inspector-field="id">—</code></div>
        <button class="button ghost" type="button" data-inspector-close autofocus>Закрыть <kbd>Esc</kbd></button>
    </header>
    <div class="catalog-inspector-tabs" role="tablist" aria-label="Информация о карте">
        <button type="button" id="cardDetailsTab" role="tab" data-inspector-tab="details" aria-controls="cardDetailsPanel" aria-selected="true">Данные</button>
        <button type="button" id="cardImagesTab" role="tab" data-inspector-tab="images" aria-controls="cardImagesPanel" aria-selected="false" tabindex="-1">Изображения <span data-inspector-field="imageCount">0</span></button>
        <button type="button" id="cardApiTab" role="tab" data-inspector-tab="api" aria-controls="cardApiPanel" aria-selected="false" tabindex="-1">JSON API</button>
    </div>
    <section id="cardDetailsPanel" role="tabpanel" aria-labelledby="cardDetailsTab" data-inspector-panel="details">
        <dl class="catalog-inspector-facts">
            <div><dt>Название EN</dt><dd data-inspector-field="englishName">—</dd></div>
            <div><dt>Категория</dt><dd data-inspector-field="type">—</dd></div>
            <div><dt>Таверна</dt><dd data-inspector-field="tier">—</dd></div>
            <div><dt>Атака</dt><dd data-inspector-field="attack">—</dd></div>
            <div><dt>Здоровье</dt><dd data-inspector-field="health">—</dd></div>
            <div><dt>Пул</dt><dd data-inspector-field="pool">—</dd></div>
            <div><dt>Режим</dt><dd data-inspector-field="duo">—</dd></div>
            <div><dt>Обновлено</dt><dd data-inspector-field="updated">—</dd></div>
        </dl>
        <section class="catalog-inspector-section"><h3>Механики</h3><div class="catalog-inspector-mechanics" data-inspector-mechanics></div></section>
        <div class="catalog-inspector-actions"><a class="button" href="#" data-inspector-link="edit">Править</a><a class="button ghost" href="#" data-inspector-link="stats">Статистика</a></div>
    </section>
    <section id="cardImagesPanel" role="tabpanel" aria-labelledby="cardImagesTab" data-inspector-panel="images" hidden>
        <div class="catalog-inspector-gallery" data-inspector-images></div>
        <p class="catalog-inspector-empty" data-inspector-image-empty>Изображения отсутствуют</p>
    </section>
    <section id="cardApiPanel" role="tabpanel" aria-labelledby="cardApiTab" data-inspector-panel="api" hidden>
        <dl class="catalog-inspector-id-grid">
            <div><dt>ID записи</dt><dd data-inspector-field="internalId">—</dd></div>
            <div><dt>card_id</dt><dd data-inspector-field="cardId">—</dd></div>
            <div><dt>dbf</dt><dd data-inspector-field="dbf">—</dd></div>
            <div><dt>Golden card_id</dt><dd data-inspector-field="goldenCardId">—</dd></div>
            <div><dt>Golden dbf</dt><dd data-inspector-field="goldenDbf">—</dd></div>
        </dl>
        <div class="catalog-inspector-api-links" data-inspector-api-links></div>
    </section>
</dialog>
<dialog class="catalog-image-preview" data-inspector-preview aria-label="Просмотр изображения карты">
    <button class="button ghost" type="button" data-inspector-preview-close autofocus>Закрыть <kbd>Esc</kbd></button>
    <img alt="">
</dialog>
