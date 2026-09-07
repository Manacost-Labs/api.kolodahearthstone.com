(() => {
    'use strict';
    const source = document.querySelector('.data-panel > .cards-table');
    const panel = source?.closest('.data-panel');
    const toolbar = panel?.querySelector('.catalog-toolbar-head');
    if (!source || !toolbar) return;
    const rows = Array.from(source.querySelectorAll(':scope > table > tbody > tr, :scope > .skin-gallery-grid > article'))
        .filter(row => !row.querySelector('.empty') && !row.matches('.empty'));
    if (!rows.length) return;

    const el = (tag, text, className) => {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    };
    const text = node => (node?.textContent || '').replace(/\s+/g, ' ').trim();
    const url = value => {
        if (!value?.trim()) return '';
        try {
            const parsed = new URL(value, location.href);
            return ['https:', 'http:'].includes(parsed.protocol) ? parsed.href : '';
        } catch { return ''; }
    };
    const image = (src, alt) => {
        const img = el('img');
        img.src = src;
        img.alt = alt;
        img.loading = 'lazy';
        img.decoding = 'async';
        img.addEventListener('error', () => img.replaceWith(el('span', 'Изображение недоступно', 'reader-image-error')), {once: true});
        return img;
    };
    const headers = Array.from(source.querySelectorAll(':scope > table > thead > tr > th'), text);
    const records = rows.map((row, index) => ({
        row,
        name: text(row.querySelector('.skin-card-head h3, .card-name-copy > span, .card-name strong'))
            || text(row.querySelector('.card-name, .name-en b')) || `Запись ${index + 1}`,
        id: text(row.querySelector('.skin-category-row code, td > code, .card-name small')),
        thumbnail: url(row.querySelector('img[src]')?.getAttribute('src')),
    }));

    // Copy only displayed field content, never forms/credentials, handlers or IDs.
    // Building an allowlisted read-only tree also makes every nested spoiler plain text.
    const allowed = new Set(['P', 'SPAN', 'DIV', 'B', 'STRONG', 'EM', 'I', 'U', 'S', 'BR', 'CODE', 'PRE',
        'UL', 'OL', 'LI', 'A', 'H3', 'H4', 'H5', 'BLOCKQUOTE', 'TABLE', 'THEAD', 'TBODY', 'TR', 'TH', 'TD', 'DL', 'DT', 'DD']);
    const omit = 'script,style,iframe,object,embed,form,input,select,textarea,button,img,video,audio,source,svg';
    function copyContent(node, brief = false) {
        if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent);
        if (!(node instanceof Element) || node.matches(omit) || (brief && node.matches('details'))) return document.createDocumentFragment();
        const tag = node.matches('summary') ? 'h4' : (allowed.has(node.tagName) ? node.tagName.toLowerCase() : 'section');
        const copy = el(tag);
        if (node.matches('div') && node.querySelector(':scope > b')) copy.className = 'reader-attribute';
        if (tag === 'a') {
            const href = url(node.getAttribute('href'));
            if (href) { copy.href = href; copy.rel = 'noopener noreferrer'; }
        }
        for (const child of node.childNodes) copy.append(copyContent(child, brief));
        const title = node.getAttribute('title')?.trim();
        if (!brief && title && title !== text(node)) copy.append(el('span', title, 'reader-source-title'));
        return copy;
    }
    function groupsFor(record) {
        if (record.row.matches('tr')) {
            return Array.from(record.row.cells, (node, i) => ({label: headers[i] || `Поле ${i + 1}`, node}))
                .filter(group => !group.node.matches('.row-actions'));
        }
        return Array.from(record.row.querySelector('.skin-card-body')?.children || [], (node, i) => ({
            label: node.matches('.skin-card-head') ? 'Название' : node.matches('.skin-meta-grid') ? 'Характеристики'
                : node.matches('.skin-category-row') ? 'Категория и ID'
                : text(node.querySelector('summary')) || `Данные ${i + 1}`, node,
        }));
    }
    function mediaFor(record) {
        const media = [], seen = new Set();
        for (const node of record.row.querySelectorAll('[data-preview], img[src], audio, video, a[href]')) {
            if (!node.matches('[data-preview]') && node.parentElement.closest('[data-preview]')) continue;
            const src = url(node.dataset.preview || node.getAttribute('src') || node.querySelector('source')?.getAttribute('src') || node.getAttribute('href'));
            if (!src) continue;
            const extension = new URL(src).pathname.split('.').pop().toLowerCase();
            const soundLink = node.matches('a') && text(node.closest('.wiki-section')?.querySelector(':scope > b')) === 'Sounds';
            if (node.matches('a') && !soundLink && !['png', 'jpg', 'jpeg', 'gif', 'webp', 'avif', 'svg', 'webm', 'mp4', 'ogg', 'mp3', 'wav', 'm4a', 'flac'].includes(extension)) continue;
            const type = node.matches('audio') || soundLink || ['ogg', 'mp3', 'wav', 'm4a', 'flac'].includes(extension) ? 'audio'
                : node.matches('video') || node.dataset.previewType === 'video' || ['webm', 'mp4'].includes(extension) ? 'video' : 'image';
            if (seen.has(type + src)) continue;
            seen.add(type + src);
            const label = text(node.closest('figure')?.querySelector('figcaption')) || text(node.closest('li')) || node.getAttribute('alt')
                || node.dataset.tooltip?.split('\n')[0] || text(node) || (type === 'audio' ? 'Звук' : 'Изображение');
            media.push({src, type, label, thumb: url(node.matches('img') ? node.getAttribute('src') : node.querySelector('img')?.getAttribute('src'))});
        }
        return media;
    }

    const reader = el('section', undefined, 'catalog-reader');
    reader.setAttribute('aria-label', 'Просмотр записей каталога');
    reader.innerHTML = `
        <nav class="reader-list" data-reader-list aria-label="Записи на текущей странице"></nav>
        <article class="reader-detail">
            <div class="reader-navigation">
                <label class="reader-mobile-select">Запись на странице<select data-reader-select></select></label>
                <span data-reader-position role="status"></span>
                <button type="button" data-reader-prev aria-label="Предыдущая запись">←</button>
                <button type="button" data-reader-next aria-label="Следующая запись">→</button>
            </div>
            <header class="reader-heading"><h2 data-reader-title></h2><code data-reader-id></code></header>
            <div class="reader-content">
                <section class="reader-gallery" aria-label="Изображения и медиа записи">
                    <div class="reader-stage" data-reader-stage></div>
                    <p class="reader-caption" data-reader-caption></p>
                    <div class="reader-media-nav" data-reader-media-navigation>
                        <button type="button" data-reader-media-prev aria-label="Предыдущее изображение">←</button>
                        <span data-reader-media-count role="status"></span>
                        <button type="button" data-reader-media-next aria-label="Следующее изображение">→</button>
                    </div>
                    <div class="reader-thumbs" data-reader-thumbs></div>
                    <div class="reader-media-nav" data-reader-media-pages>
                        <button type="button" data-reader-media-page-prev aria-label="Предыдущие миниатюры">←</button>
                        <span data-reader-media-page-status></span>
                        <button type="button" data-reader-media-page-next aria-label="Следующие миниатюры">→</button>
                    </div>
                </section>
                <section class="reader-fields" aria-label="Данные записи">
                    <label>Данные записи<select data-reader-field></select></label>
                    <div class="reader-data" data-reader-data tabindex="0" aria-label="Содержимое выбранного раздела"></div>
                </section>
            </div>
        </article>`;
    const find = selector => reader.querySelector(`[data-reader-${selector}]`);
    let selected = 0, mediaIndex = 0, mediaPage = 0, media = [], groups = [];
    const pageSize = 6;
    const pause = () => reader.querySelectorAll('video,audio').forEach(node => node.pause());
    const buttons = records.map((record, i) => {
        const button = el('button');
        button.type = 'button';
        button.dataset.readerRecord = i;
        if (record.thumbnail) button.append(image(record.thumbnail, ''));
        const copy = el('span');
        copy.append(el('b', record.name), el('small', record.id || `Запись ${i + 1}`));
        button.append(copy);
        button.addEventListener('click', () => selectRecord(i));
        find('list').append(button);
        const option = el('option', `${i + 1}. ${record.name}`);
        option.value = i;
        find('select').append(option);
        return button;
    });
    function renderData() {
        const content = find('data');
        content.replaceChildren();
        const key = find('field').value;
        if (key !== 'overview') {
            const group = groups[Number(key)];
            content.append(el('h3', group.label));
            for (const child of group.node.childNodes) content.append(copyContent(child));
            if (text(content) === group.label) content.append(el('p', group.node.querySelector('form')
                ? 'Изменение и удаление доступны в режиме «Таблица».'
                : group.node.querySelector('img, [data-preview], audio, video') ? 'Медиа собраны в галерее слева (на телефоне — выше).' : 'Нет данных.'));
        } else {
            const row = records[selected].row;
            const description = row.querySelector('.name-en .subtext, .skin-card-body > .subtext');
            const tooltip = row.querySelector('.card-name')?.getAttribute('title');
            const descriptionText = text(description) || tooltip;
            if (descriptionText) content.append(el('p', descriptionText, 'reader-description'));
            const facts = el('dl', undefined, 'reader-facts');
            for (const [index, group] of groups.entries()) {
                // Full content stays one selection away, including hidden table columns.
                if (index === 0 || group.node.querySelector('form') || /Карта|Card EN|Crop|Действия|Название/.test(group.label)) continue;
                if (group.node.matches('.skin-meta-grid')) {
                    for (const item of group.node.children) {
                        const pair = el('div');
                        pair.append(el('dt', text(item.querySelector('b'))), el('dd', text(item.querySelector('span')) || '—'));
                        facts.append(pair);
                    }
                } else {
                    const value = text(copyContent(group.node, true));
                    if (!value || value === '—' || value.length > 180) continue;
                    const pair = el('div');
                    pair.append(el('dt', group.label), el('dd', value));
                    facts.append(pair);
                }
            }
            content.append(facts);
            const stats = row.querySelector('a.card-stats-link');
            if (stats) content.append(copyContent(stats));
            content.append(el('p', 'Все поля, Wiki и связи доступны в списке «Данные записи». Изменения — в режиме «Таблица».', 'reader-help'));
        }
        content.scrollTop = 0;
    }
    function renderThumbs() {
        const thumbs = find('thumbs');
        thumbs.replaceChildren();
        media.slice(mediaPage * pageSize, (mediaPage + 1) * pageSize).forEach((item, offset) => {
            const index = mediaPage * pageSize + offset;
            const button = el('button');
            button.type = 'button';
            button.dataset.readerThumb = index;
            button.setAttribute('aria-label', `${index + 1}. ${item.label}`);
            button.setAttribute('aria-pressed', String(index === mediaIndex));
            button.title = item.label;
            button.append(item.type === 'image' ? image(item.thumb || item.src, '') : el('span', item.type === 'audio' ? 'Звук' : 'Видео'));
            button.addEventListener('click', () => { mediaIndex = index; renderMedia(false); });
            thumbs.append(button);
        });
        find('media-pages').hidden = media.length <= pageSize;
        find('media-page-status').textContent = `Миниатюры ${mediaPage + 1} / ${Math.ceil(media.length / pageSize)}`;
        find('media-page-prev').disabled = mediaPage === 0;
        find('media-page-next').disabled = (mediaPage + 1) * pageSize >= media.length;
    }
    function renderMedia(rebuildThumbs = true) {
        pause();
        const stage = find('stage');
        stage.replaceChildren();
        const item = media[mediaIndex];
        if (!item) {
            stage.append(el('p', 'Нет изображений и медиа для этой записи.', 'reader-help'));
        } else if (item.type === 'image') {
            const preview = el('button');
            preview.type = 'button';
            preview.dataset.preview = item.src;
            preview.dataset.tooltip = item.label;
            preview.setAttribute('aria-label', 'Открыть изображение на весь экран');
            preview.append(image(item.src, item.label), el('span', 'На весь экран ↗', 'reader-expand'));
            stage.append(preview);
        } else {
            const player = el(item.type);
            player.controls = true;
            player.preload = 'none';
            player.src = item.src;
            player.setAttribute('aria-label', item.label);
            stage.append(player);
        }
        find('caption').textContent = item?.label || '';
        find('media-count').textContent = item ? `${mediaIndex + 1} / ${media.length}` : '0 / 0';
        find('media-navigation').hidden = media.length <= 1;
        find('thumbs').hidden = media.length <= 1;
        find('media-prev').disabled = mediaIndex <= 0;
        find('media-next').disabled = mediaIndex >= media.length - 1;
        if (rebuildThumbs) renderThumbs();
        else find('thumbs').querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.readerThumb) === mediaIndex)));
    }
    function selectRecord(index) {
        selected = Math.max(0, Math.min(records.length - 1, index));
        const record = records[selected];
        buttons.forEach((button, i) => {
            button.setAttribute('aria-current', String(i === selected));
            button.tabIndex = i === selected ? 0 : -1;
        });
        find('select').value = selected;
        find('title').textContent = record.name;
        find('id').textContent = record.id;
        find('position').textContent = `${selected + 1} / ${records.length}`;
        find('prev').disabled = selected === 0;
        find('next').disabled = selected === records.length - 1;
        groups = groupsFor(record);
        const field = find('field');
        const previousLabel = field.selectedOptions[0]?.textContent;
        field.replaceChildren(new Option('Обзор · основные данные', 'overview'));
        groups.forEach((group, i) => field.append(new Option(group.label, String(i))));
        const previous = Array.from(field.options).find(option => option.textContent === previousLabel);
        if (previous) field.value = previous.value;
        renderData();
        media = mediaFor(record);
        mediaIndex = 0; mediaPage = 0;
        renderMedia();
    }
    find('field').addEventListener('change', renderData);
    find('select').addEventListener('change', event => selectRecord(Number(event.target.value)));
    find('prev').addEventListener('click', () => selectRecord(selected - 1));
    find('next').addEventListener('click', () => selectRecord(selected + 1));
    find('list').addEventListener('keydown', event => {
        const moves = {ArrowDown: selected + 1, ArrowUp: selected - 1, Home: 0, End: records.length - 1};
        if (!(event.key in moves)) return;
        event.preventDefault();
        selectRecord(moves[event.key]);
        buttons[selected].focus({preventScroll: true});
        buttons[selected].scrollIntoView({block: 'nearest'});
    });
    for (const [name, delta] of [['prev', -1], ['next', 1]]) {
        find(`media-${name}`).addEventListener('click', () => {
            mediaIndex = Math.max(0, Math.min(media.length - 1, mediaIndex + delta));
            mediaPage = Math.floor(mediaIndex / pageSize);
            renderMedia();
        });
        find(`media-page-${name}`).addEventListener('click', () => { mediaPage += delta; renderThumbs(); });
    }
    const modes = el('div', undefined, 'reader-modes');
    modes.setAttribute('role', 'group');
    modes.setAttribute('aria-label', 'Вид каталога');
    function setMode(mode) {
        pause();
        const active = mode === 'reader';
        reader.hidden = !active;
        panel.classList.toggle('is-reader-mode', active);
        modes.querySelectorAll('button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.mode === mode)));
        try { localStorage.setItem('panel-catalog-view', mode); } catch { /* Storage is optional. */ }
        window.dispatchEvent(new Event('resize'));
    }
    for (const [mode, label] of [['reader', 'Просмотр'], ['table', 'Таблица']]) {
        const button = el('button', label);
        button.type = 'button'; button.dataset.mode = mode;
        button.addEventListener('click', () => setMode(mode));
        modes.append(button);
    }
    // Enhance only after construction succeeds. The original table remains the no-JS fallback.
    // Entry-local view state: no catalogue cache, no copied record contents.
    // A new page starts at its first row; Back/reload finds the same ID if present.
    const saved = history.state?.panelReader;
    const restored = saved?.id ? records.findIndex(record => record.id === saved.id) : -1;
    selectRecord(restored < 0 ? 0 : restored);
    if (restored >= 0 && Array.from(find('field').options).some(option => option.value === saved.field)) {
        find('field').value = saved.field;
        renderData();
    }
    window.addEventListener('pagehide', () => {
        try {
            history.replaceState({...history.state, panelReader: {
                id: records[selected].id, field: find('field').value,
            }}, '');
        } catch { /* History can be unavailable; native navigation still works. */ }
    });
    source.before(reader);
    toolbar.append(modes);
    let initial = 'reader';
    try { if (localStorage.getItem('panel-catalog-view') === 'table') initial = 'table'; } catch { /* Optional. */ }
    setMode(initial);
})();
