(() => {
    'use strict';
    const cache = new Map();
    let selection, controller, timer, version = 0;
    const el = (tag, text, className) => {
        const node = document.createElement(tag);
        if (text !== undefined) node.textContent = text;
        if (className) node.className = className;
        return node;
    };
    const number = value => value !== null && value !== undefined && String(value).trim() !== ''
        && ['number', 'string'].includes(typeof value) && Number.isFinite(Number(value));
    const date = value => {
        const parsed = Date.parse(value);
        return Number.isFinite(parsed) ? new Date(parsed).toLocaleString('ru-RU') : 'Дата не указана';
    };
    function cancel() {
        version++;
        clearTimeout(timer);
        controller?.abort();
    }
    function show(payload, target, link) {
        const id = link.dataset.statsCardId || '', dbf = link.dataset.statsDbfId || '';
        // Fuzzy API results can include other cards. Never show their numbers here.
        const rows = payload.rows.filter(row => row && typeof row === 'object' && (
            row.card_id ? String(row.card_id) === id
                : row.dbf_id ? String(row.dbf_id) === dbf
                    : row.dataset === 'Динамика карты' // This endpoint uses an exact name query.
        )).sort((a, b) => (Date.parse(b.recorded_at) || 0) - (Date.parse(a.recorded_at) || 0));
        const warning = payload.meta?.stale_cache || payload.meta?.stale
            ? 'Сохранённый срез: источник сейчас не обновляется.'
            : payload.warnings?.length ? 'Часть источников недоступна. Показаны полученные данные.' : '';
        target.replaceChildren();
        if (warning) target.append(el('p', warning, 'reader-stats-warning'));
        if (!rows.length) {
            target.append(el('p', warning ? 'Для этой карты данные не получены. Попробуйте обновить позже.'
                : 'Для этой карты статистики пока нет.', 'reader-help'));
            return;
        }
        const select = el('select');
        select.setAttribute('aria-label', 'Срез статистики');
        rows.forEach((row, i) => select.append(new Option(
            `${row.dataset || 'Срез'} · ${row.context || 'Без контекста'} · ${date(row.recorded_at)} · ${row.source || 'Источник не указан'}`, String(i))));
        const label = el('label', `Срезы из ответа · ${rows.length}`, 'reader-stats-label');
        label.append(select);
        if (rows.length > 1) target.append(label);
        const details = el('div');
        target.append(details);
        const render = () => {
            const row = rows[Number(select.value) || 0];
            details.replaceChildren();
            details.append(el('p', [row.dataset, row.context].filter(Boolean).join(' · '), 'reader-stats-context'));
            const metrics = el('dl', undefined, 'reader-stats-metrics');
            for (const [key, title, suffix] of [
                ['popularity', row.dataset === 'BG герой' ? 'Выбирают' : 'Популярность', '%'],
                ['winrate', row.dataset === 'BG существо' ? 'Винрейт боя' : 'Винрейт', '%'],
                ['avg_placement', 'Среднее место', ''], ['impact', 'Влияние', ''], ['games', 'Игры', ''],
            ]) {
                if (!number(row[key])) continue;
                const item = el('div');
                item.append(el('dt', title), el('dd', Number(row[key]).toLocaleString('ru-RU', {maximumFractionDigits: 2}) + suffix));
                metrics.append(item);
            }
            details.append(metrics.children.length ? metrics : el('p', 'В этом срезе числовые показатели не переданы.', 'reader-help'));
            details.append(el('p', `${row.source || 'Источник не указан'} · ${date(row.recorded_at)}`, 'reader-stats-source'));
            if (!row.card_id && !row.dbf_id) details.append(el('p', 'Совпадение по названию; ID в источнике не указан.', 'reader-help'));
        };
        select.addEventListener('change', render);
        render();
    }
    function load(force = false) {
        cancel();
        if (!selection || selection.reader.hidden) return;
        const {reader, row} = selection;
        const link = row.querySelector('a.card-stats-link');
        let section = reader.querySelector('.reader-statistics');
        if (!link) { if (section) section.hidden = true; return; }
        const name = new URL(link.href).searchParams.get('stats_q');
        if (!name) { if (section) section.hidden = true; return; }
        if (!section) {
            section = el('section', undefined, 'reader-statistics');
            section.setAttribute('aria-label', 'Статистика выбранной карты');
            reader.querySelector('.reader-fields').prepend(section);
        }
        section.hidden = false;
        const refocus = force && section.contains(document.activeElement);
        section.replaceChildren();
        const heading = el('header'), refresh = el('button', 'Обновить');
        refresh.type = 'button';
        refresh.addEventListener('click', () => load(true));
        heading.append(el('h3', 'Статистика карты'), refresh);
        const status = el('p', 'Загрузка статистики…', 'reader-help');
        status.setAttribute('role', 'status');
        const content = el('div');
        const full = el('a', 'Полная статистика ↗', 'reader-stats-full');
        full.href = link.href;
        section.append(heading, status, content, full);
        if (refocus) refresh.focus({preventScroll: true});
        const key = name + '|' + link.dataset.statsCardId + '|' + link.dataset.statsDbfId;
        const stored = cache.get(key);
        if (!force && stored && Date.now() - stored.at < 120000) {
            show(stored.payload, content, link);
            status.textContent = 'Срез из памяти этой страницы';
            return;
        }
        const requestVersion = version;
        // Coalesce rapid keyboard/click navigation; do not fetch all catalogue rows.
        timer = setTimeout(async () => {
            const request = new AbortController();
            controller = request;
            const deadline = setTimeout(() => request.abort(), 15000);
            try {
                const response = await fetch('/analytics.php?' + new URLSearchParams({module: 'card', card_name: name}), {
                    signal: request.signal, credentials: 'same-origin', headers: {Accept: 'application/json'},
                });
                if (!response.ok) throw new Error(response.status === 401 ? 'auth' : 'request');
                const payload = await response.json();
                if (payload.ok !== true || !Array.isArray(payload.rows)) throw new Error('shape');
                if (requestVersion !== version) return;
                show(payload, content, link);
                status.textContent = 'Показатели выбранного среза';
                cache.delete(key);
                cache.set(key, {at: Date.now(), payload});
                if (cache.size > 20) cache.delete(cache.keys().next().value);
            } catch (error) {
                if (requestVersion !== version) return;
                status.textContent = error.message === 'auth' ? 'Сессия истекла. Войдите в панель заново.'
                    : request.signal.aborted ? 'Источник отвечает долго. Можно продолжить просмотр или повторить запрос.'
                        : 'Статистика временно недоступна. Можно повторить запрос.';
            } finally {
                clearTimeout(deadline);
            }
        }, 250);
    }
    document.addEventListener('panel:reader-selection', event => { selection = event.detail; load(); });
    window.addEventListener('pagehide', () => { cancel(); cache.clear(); });
    window.addEventListener('pageshow', event => { if (event.persisted) load(); });
})();
