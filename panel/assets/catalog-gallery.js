(() => {
    'use strict';

    const gallery = document.querySelector('[data-catalog-gallery]');
    const workbench = gallery?.closest('[data-catalog-workbench]');
    const view = window.CatalogWorkspaceView;
    if (!gallery || !workbench || !view) return;
    const rows = Array.from(workbench.querySelectorAll('[data-catalog-record]'));
    if (!rows.length) return;
    const toolbar = workbench.querySelector('[data-catalog-gallery-toolbar]');
    const preferencesKey = 'hsDataCatalogView';
    const create = (tag, className, value) => {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (value !== undefined) element.textContent = value;
        return element;
    };

    rows.forEach((row) => {
        const record = view.recordFromDataset(row.dataset);
        const tile = create('button', 'catalog-tile');
        tile.type = 'button';
        tile.setAttribute('aria-label', `${record.name}. Открыть детали карты`);
        const art = create('span', 'catalog-tile-art');
        const availableImages = ['art', 'horizontal', 'card']
            .map((kind) => record.images.find((item) => item.kind === kind)?.url)
            .filter(Boolean);
        const placeholder = create('span', 'catalog-tile-placeholder', 'Изображение недоступно');
        art.append(placeholder);
        if (availableImages.length) {
            const image = create('img');
            image.alt = '';
            image.loading = 'lazy';
            image.decoding = 'async';
            let imageIndex = 0;
            image.addEventListener('error', () => {
                imageIndex += 1;
                if (imageIndex < availableImages.length) image.src = availableImages[imageIndex];
                else image.remove();
            });
            image.src = availableImages[0];
            art.append(image);
        }
        if (record.tier !== '—') art.append(create('span', 'catalog-tile-tier', `★ ${record.tier}`));
        const copy = create('span', 'catalog-tile-copy');
        copy.append(create('span', 'catalog-tile-title', record.name), create('code', 'catalog-tile-id', record.id));
        const tags = create('span', 'catalog-tile-tags');
        tags.append(create('span', '', record.tribe || record.type));
        if (record.pool === 'В пуле') tags.append(create('span', 'catalog-tile-pool', record.pool));
        copy.append(tags);
        const footer = create('span', 'catalog-tile-footer');
        const stats = create('span', 'catalog-tile-stats');
        if (record.attack !== '—' || record.health !== '—') {
            const attack = create('span', 'catalog-tile-attack', `⚔ ${record.attack}`);
            attack.setAttribute('aria-label', `Атака ${record.attack}`);
            const health = create('span', 'catalog-tile-health', `♥ ${record.health}`);
            health.setAttribute('aria-label', `Здоровье ${record.health}`);
            stats.append(attack, health);
        }
        footer.append(stats, create('span', 'catalog-tile-more', 'Подробнее →'));
        tile.append(art, copy, footer);
        tile.addEventListener('click', () => {
            document.dispatchEvent(new CustomEvent('catalog:open', { detail: { row, trigger: tile } }));
        });
        gallery.append(tile);
    });

    const buttons = Array.from(workbench.querySelectorAll('[data-catalog-view]'));
    const setView = (mode) => {
        const grid = mode !== 'list';
        workbench.dataset.catalogView = grid ? 'grid' : 'list';
        gallery.hidden = !grid;
        buttons.forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.catalogView === (grid ? 'grid' : 'list'))));
        const panel = workbench.closest('.data-panel');
        panel?.classList.toggle('has-catalog-gallery', grid);
        workbench.querySelector('.cards-table')?.toggleAttribute('hidden', grid);
        workbench.querySelector('[data-table-navigation]')?.toggleAttribute('hidden', grid);
        try { localStorage.setItem(preferencesKey, grid ? 'grid' : 'list'); } catch (error) { /* optional preference */ }
    };
    buttons.forEach((button) => button.addEventListener('click', () => setView(button.dataset.catalogView)));
    toolbar.querySelector('[data-gallery-count]').textContent = String(rows.length);
    toolbar.hidden = false;
    let initialView = 'grid';
    try { initialView = localStorage.getItem(preferencesKey) || initialView; } catch (error) { /* optional preference */ }
    setView(initialView);
})();
