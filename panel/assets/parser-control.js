(() => {
    'use strict';

    const root = document.querySelector('[data-parser-control]');
    const view = window.ParserControlView;
    if (!root || !view) return;

    const endpoint = root.dataset.endpoint || '/parser-control.php';
    const csrf = root.dataset.csrf || '';
    const summaryHost = root.querySelector('[data-parser-summary]');
    const sourcesBody = root.querySelector('[data-parser-sources-body]');
    const sourceCount = root.querySelector('[data-parser-source-count]');
    const empty = root.querySelector('[data-parser-empty]');
    const sectionHost = root.querySelector('[data-parser-sections]');
    const runsHost = root.querySelector('[data-parser-runs]');
    const updated = root.querySelector('[data-parser-updated]');
    const alert = root.querySelector('[data-parser-alert]');
    const alertMessage = root.querySelector('[data-parser-alert-message]');
    const refreshButton = root.querySelector('[data-parser-refresh]');
    const search = root.querySelector('[data-parser-search]');
    const statusFilter = root.querySelector('[data-parser-status]');
    const densityButton = root.querySelector('[data-parser-density]');
    const runSectionButton = root.querySelector('[data-run-section]');
    const dialog = root.querySelector('[data-run-dialog]');
    const runForm = root.querySelector('[data-run-form]');
    const runTitle = root.querySelector('[data-run-dialog-title]');
    const runDescription = root.querySelector('[data-run-dialog-description]');
    const runSourceId = root.querySelector('[data-run-source-id]');
    const runSectionId = root.querySelector('[data-run-section-id]');
    const runReason = root.querySelector('[data-run-reason]');
    const runStatus = root.querySelector('[data-run-status]');
    const runConfirm = root.querySelector('[data-run-confirm]');
    let snapshot = null;
    let selectedSection = 'all';
    let controller = null;
    let reloadTimer = 0;

    const element = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = String(text);
        return node;
    };

    const dateCell = (value) => {
        const formatted = view.formatDate(value);
        if (formatted === '—') return element('span', 'parser-empty-value', '—');
        const wrapper = element('span', 'parser-date');
        wrapper.textContent = formatted.relative;
        wrapper.title = formatted.exact;
        const exact = element('small', '', formatted.exact);
        wrapper.append(exact);
        return wrapper;
    };

    const statusBadge = (value) => {
        const meta = view.statusMeta(value);
        const badge = element('span', `parser-status is-${meta.tone}`);
        badge.append(element('i', '', ''), document.createTextNode(meta.label));
        return badge;
    };

    const setSummary = (key, value, detail, tone = '') => {
        const card = summaryHost?.querySelector(`[data-summary-card="${key}"]`);
        if (!card) return;
        card.querySelector('b').textContent = value;
        card.querySelector('small').textContent = detail;
        card.dataset.tone = tone;
    };

    const renderSummary = () => {
        const summary = view.buildSummary(snapshot);
        const active = summary.activeRun;
        setSummary('active', active ? '1' : '0', active ? 'выполняется или ожидает' : 'очередь свободна', active ? 'info' : 'good');
        setSummary('health', `${summary.healthy}/${summary.total}`, 'работают стабильно', summary.healthy === summary.total ? 'good' : '');
        setSummary('issues', view.formatNumber(summary.issues), summary.issues === 1 ? 'источник требует внимания' : 'источников требуют внимания', summary.issues > 0 ? 'warning' : 'good');
        const next = view.formatDate(summary.nextRunAt);
        setSummary('next', next === '—' ? '—' : next.relative, next === '—' ? 'нет активного расписания' : next.exact, '');
        summaryHost?.setAttribute('aria-busy', 'false');
    };

    const renderSections = () => {
        const sections = Array.isArray(snapshot?.sections) ? snapshot.sections : [];
        sectionHost.replaceChildren();
        const all = element('button', selectedSection === 'all' ? 'active' : '', `Все · ${view.flattenSources(snapshot).length}`);
        all.type = 'button';
        all.dataset.sectionId = 'all';
        all.setAttribute('aria-pressed', selectedSection === 'all' ? 'true' : 'false');
        sectionHost.append(all);
        sections.forEach((section) => {
            const button = element('button', selectedSection === section.id ? 'active' : '', `${section.label} · ${section.sourceCount ?? section.sources?.length ?? 0}`);
            button.type = 'button';
            button.dataset.sectionId = String(section.id || '');
            button.setAttribute('aria-pressed', selectedSection === section.id ? 'true' : 'false');
            if (section.enabled === false) button.classList.add('is-disabled');
            sectionHost.append(button);
        });
        runSectionButton.hidden = selectedSection === 'all';
    };

    const sourceMatches = (source) => {
        const query = (search?.value || '').trim().toLocaleLowerCase('ru-RU');
        const state = String(source.health || source.state || 'missing').toLowerCase();
        const enabled = source.sectionEnabled !== false && source.enabled !== false;
        const issues = new Set(['warning', 'partial', 'missing', 'error', 'failed']);
        const status = statusFilter?.value || 'all';
        const statusMatches = status === 'all'
            || (status === 'issues' && issues.has(state))
            || (status === 'ok' && ['ok', 'ready', 'upstream_pending'].includes(state) && enabled)
            || (status === 'disabled' && !enabled);
        const haystack = `${source.label || ''} ${source.id || ''} ${source.sectionLabel || ''}`.toLocaleLowerCase('ru-RU');
        return statusMatches
            && (selectedSection === 'all' || source.sectionId === selectedSection)
            && (query === '' || haystack.includes(query));
    };

    const openRunDialog = ({ sourceId = '', sectionId = '', label = '' }) => {
        runSourceId.value = sourceId;
        runSectionId.value = sectionId;
        runTitle.textContent = sourceId ? `Запустить «${label}»` : `Запустить раздел «${label}»`;
        runDescription.textContent = sourceId
            ? `Источник ${sourceId} будет поставлен в безопасную очередь.`
            : 'Все источники раздела будут поставлены в очередь в штатном порядке.';
        runReason.value = 'Ручной запуск из панели';
        runStatus.textContent = '';
        runConfirm.disabled = false;
        dialog.showModal();
        window.setTimeout(() => runReason.focus(), 0);
    };

    const renderSources = () => {
        const allSources = view.flattenSources(snapshot);
        const priority = { error: 0, failed: 0, warning: 1, partial: 1, missing: 2, ok: 3, ready: 3 };
        const sources = allSources.filter(sourceMatches).sort((left, right) => {
            const stateOrder = (priority[left.health] ?? 2) - (priority[right.health] ?? 2);
            return stateOrder || String(left.label || left.id).localeCompare(String(right.label || right.id), 'ru');
        });
        sourcesBody.replaceChildren();
        sources.forEach((source) => {
            const row = document.createElement('tr');
            const sourceCell = element('td', 'parser-source-name');
            const name = element('b', '', source.label || source.id);
            const id = element('code', '', source.id);
            sourceCell.append(name, id, element('small', '', source.sectionLabel));

            const stateCell = document.createElement('td');
            const effectiveState = source.sectionEnabled === false || source.enabled === false ? 'disabled' : (source.health || source.state);
            stateCell.append(statusBadge(effectiveState));
            if (source.lastError) {
                const error = element('small', 'parser-source-error', source.lastError);
                error.title = source.lastError;
                stateCell.append(error);
            }

            const rowsCell = document.createElement('td');
            rowsCell.append(element('b', 'parser-row-count', view.formatNumber(source.rowsTotal)));
            if (source.publicationChannel) rowsCell.append(element('small', '', source.publicationChannel));

            const successCell = document.createElement('td');
            successCell.append(dateCell(source.lastSuccessAt));
            const scheduleCell = document.createElement('td');
            scheduleCell.append(element('span', 'parser-schedule', source.schedule || 'Ручной запуск'));
            const nextCell = document.createElement('td');
            nextCell.append(dateCell(source.nextRunAt));

            const actionCell = element('td', 'parser-row-action');
            const button = element('button', 'parser-run-button', 'Запустить');
            button.type = 'button';
            button.disabled = source.canRunManually === false || source.sectionEnabled === false;
            button.addEventListener('click', () => openRunDialog({ sourceId: source.id, label: source.label || source.id }));
            actionCell.append(button);
            row.append(sourceCell, stateCell, rowsCell, successCell, scheduleCell, nextCell, actionCell);
            sourcesBody.append(row);
        });
        empty.hidden = sources.length > 0;
        sourceCount.textContent = `Показано ${view.formatNumber(sources.length)} из ${view.formatNumber(allSources.length)} источников`;
    };

    const renderRunDetails = (run) => {
        const details = element('details', 'parser-run-details');
        const summary = element('summary', '', 'Подробности');
        const content = element('div', 'parser-run-detail-grid');
        const fields = [
            ['ID запуска', run.id], ['Инициатор', run.requestedBy], ['Причина', run.reason],
            ['Создан', view.formatDate(run.createdAt)?.exact], ['Начат', view.formatDate(run.startedAt)?.exact],
            ['Завершён', view.formatDate(run.finishedAt)?.exact],
        ];
        fields.forEach(([label, value]) => {
            if (!value || value === '—') return;
            const item = element('div');
            item.append(element('span', '', label), element('b', '', value));
            content.append(item);
        });
        const errors = Array.isArray(run.errors) ? run.errors : (run.error ? [run.error] : []);
        if (errors.length) {
            const errorList = element('ul', 'parser-run-errors');
            errors.forEach((message) => errorList.append(element('li', '', message)));
            content.append(errorList);
        }
        details.append(summary, content);
        return details;
    };

    const renderRuns = () => {
        const runs = Array.isArray(snapshot?.recentRuns) ? snapshot.recentRuns : [];
        runsHost.replaceChildren();
        if (!runs.length) {
            runsHost.append(element('p', 'parser-empty', 'Запусков пока нет. Запустите нужный источник из таблицы выше.'));
            return;
        }
        runs.forEach((run) => {
            const item = element('article', 'parser-run-item');
            const heading = element('div', 'parser-run-heading');
            const copy = element('div');
            copy.append(element('b', '', run.reason || 'Запуск парсеров'));
            const created = view.formatDate(run.createdAt);
            copy.append(element('span', '', created === '—' ? (run.id || '') : `${created.relative} · ${run.requestedBy || 'scheduler'}`));
            heading.append(statusBadge(run.status), copy);
            const progress = view.runProgress(run);
            const meter = element('div', 'parser-run-progress');
            const track = element('span');
            const fill = element('i');
            fill.style.width = `${progress.percent}%`;
            track.append(fill);
            meter.append(track, element('b', '', progress.total ? `${progress.done}/${progress.total}` : '—'));
            item.append(heading, meter, renderRunDetails(run));
            runsHost.append(item);
        });
    };

    const showError = (message) => {
        alertMessage.textContent = message || 'Попробуйте повторить запрос.';
        alert.hidden = false;
    };

    const scheduleReload = () => {
        window.clearTimeout(reloadTimer);
        if (document.hidden) return;
        reloadTimer = window.setTimeout(load, snapshot?.activeRun ? 12_000 : 60_000);
    };

    async function load() {
        controller?.abort();
        controller = new AbortController();
        refreshButton.disabled = true;
        try {
            const response = await fetch(endpoint, {
                credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: controller.signal,
            });
            const payload = await response.json();
            if (!response.ok || payload.ok !== true || !payload.data) throw new Error(payload.message || 'Не удалось загрузить состояние.');
            snapshot = payload.data;
            alert.hidden = true;
            renderSummary();
            renderSections();
            renderSources();
            renderRuns();
            const generated = view.formatDate(snapshot.generatedAt);
            updated.textContent = generated === '—' ? 'Состояние обновлено' : `Обновлено ${generated.relative}`;
            updated.title = generated === '—' ? '' : generated.exact;
        } catch (error) {
            if (error.name !== 'AbortError') showError(error.message);
        } finally {
            refreshButton.disabled = false;
            scheduleReload();
        }
    }

    const submitRun = async () => {
        runConfirm.disabled = true;
        runStatus.textContent = 'Ставим запуск в очередь…';
        const body = { action: 'run', reason: runReason.value.trim() };
        if (runSourceId.value) body.source_ids = [runSourceId.value];
        if (runSectionId.value) body.section_ids = [runSectionId.value];
        try {
            const response = await fetch(endpoint, {
                method: 'POST', credentials: 'same-origin',
                headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify(body),
            });
            const payload = await response.json();
            if (!response.ok || payload.ok !== true) throw new Error(payload.message || 'Не удалось запустить парсер.');
            runStatus.textContent = payload.data?.deduplicated ? 'Такой запуск уже находится в очереди.' : 'Запуск добавлен в очередь.';
            window.setTimeout(() => dialog.close(), 650);
            window.setTimeout(load, 750);
        } catch (error) {
            runStatus.textContent = error.message;
            runConfirm.disabled = false;
        }
    };

    sectionHost.addEventListener('click', (event) => {
        const button = event.target.closest('[data-section-id]');
        if (!button) return;
        selectedSection = button.dataset.sectionId || 'all';
        renderSections();
        renderSources();
    });
    search.addEventListener('input', renderSources);
    statusFilter.addEventListener('change', renderSources);
    refreshButton.addEventListener('click', load);
    root.querySelector('[data-parser-retry]').addEventListener('click', load);
    runSectionButton.addEventListener('click', () => {
        const section = snapshot?.sections?.find((item) => item.id === selectedSection);
        if (section) openRunDialog({ sectionId: section.id, label: section.label || section.id });
    });
    root.querySelector('[data-run-cancel]').addEventListener('click', () => dialog.close());
    runForm.addEventListener('submit', (event) => {
        event.preventDefault();
        submitRun();
    });
    dialog.addEventListener('click', (event) => {
        if (event.target === dialog) dialog.close();
    });
    densityButton.addEventListener('click', () => {
        const compact = root.classList.toggle('is-compact');
        densityButton.setAttribute('aria-pressed', compact ? 'true' : 'false');
        densityButton.textContent = compact ? 'Обычно' : 'Компактно';
        try { localStorage.setItem('parserPanelDensity', compact ? 'compact' : 'normal'); } catch (error) { /* optional preference */ }
    });
    try {
        if (localStorage.getItem('parserPanelDensity') === 'compact') densityButton.click();
    } catch (error) { /* optional preference */ }
    document.addEventListener('visibilitychange', () => document.hidden ? window.clearTimeout(reloadTimer) : load());
    document.addEventListener('keydown', (event) => {
        if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
        if (event.target.matches('input, textarea, select')) return;
        event.preventDefault();
        search.focus();
    });

    load();
})();
