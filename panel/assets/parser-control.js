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
    const live = root.querySelector('[data-parser-live]');
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
    let submitting = false;

    const readFilters = () => {
        const params = new URL(window.location.href).searchParams;
        selectedSection = params.get('source_section') || 'all';
        search.value = params.get('source_q') || '';
        const status = params.get('source_status') || 'all';
        statusFilter.value = Array.from(statusFilter.options).some((option) => option.value === status) ? status : 'all';
    };
    const saveFilters = () => {
        const url = new URL(window.location.href);
        Object.entries({ source_q: search.value.trim(), source_status: statusFilter.value, source_section: selectedSection }).forEach(([key, value]) => {
            if (value && value !== 'all') url.searchParams.set(key, value);
            else url.searchParams.delete(key);
        });
        window.history.replaceState(null, '', url);
    };
    readFilters();

    const isRecord = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
    const isEntity = (value) => isRecord(value) && typeof value.id === 'string' && value.id.length > 0;
    const isSnapshot = (value) => isRecord(value) && Array.isArray(value.sections)
        && value.sections.every((section) => isEntity(section) && Array.isArray(section.sources) && section.sources.every(isEntity))
        && (value.recentRuns === undefined || (Array.isArray(value.recentRuns) && value.recentRuns.every(isEntity)))
        && (value.activeRun == null || isEntity(value.activeRun));

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

    const sourceDiagnostics = (source) => {
        if (!source.lastError) return null;
        const details = element('details', 'parser-source-diagnostics');
        const summary = element('summary', '', 'Подробности ошибки');
        summary.dataset.parserFocus = `source:${source.id}:diagnostics`;
        const body = element('div', 'parser-source-diagnostics-body');
        body.append(
            element('b', '', view.errorSummary(source.lastError)),
            element('p', '', 'Технический ответ последней попытки:'),
            element('pre', '', source.lastError),
        );
        details.append(summary, body);
        return details;
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
        setSummary('health', `${summary.fresh}/${summary.operational}`, 'со свежими опубликованными данными', summary.fresh === summary.operational ? 'good' : '');
        setSummary(
            'issues',
            view.formatNumber(summary.fallback),
            summary.unavailable > 0
                ? `${view.formatNumber(summary.unavailable)} без доступных данных`
                : 'резерв доступен · полных отказов нет',
            summary.unavailable > 0 ? 'bad' : (summary.fallback > 0 ? 'warning' : 'good'),
        );
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
        all.dataset.parserFocus = 'section:all';
        all.setAttribute('aria-pressed', selectedSection === 'all' ? 'true' : 'false');
        sectionHost.append(all);
        sections.forEach((section) => {
            const button = element('button', selectedSection === section.id ? 'active' : '', `${section.label} · ${section.sourceCount ?? section.sources?.length ?? 0}`);
            button.type = 'button';
            button.dataset.sectionId = String(section.id || '');
            button.dataset.parserFocus = `section:${section.id}`;
            button.setAttribute('aria-pressed', selectedSection === section.id ? 'true' : 'false');
            if (section.enabled === false) button.classList.add('is-disabled');
            sectionHost.append(button);
        });
        runSectionButton.hidden = selectedSection === 'all';
        const selected = sections.find((section) => section.id === selectedSection);
        runSectionButton.disabled = !selected || selected.enabled === false
            || !(selected.sources || []).some((source) => source.canRunManually !== false && source.enabled !== false);
    };

    const sourceMatches = (source) => {
        const query = (search?.value || '').trim().toLocaleLowerCase('ru-RU');
        const presentation = view.sourcePresentation(source);
        const status = statusFilter?.value || 'all';
        const statusMatches = status === 'all'
            || status === presentation.filter;
        const haystack = `${source.label || ''} ${source.id || ''} ${source.sectionLabel || ''}`.toLocaleLowerCase('ru-RU');
        return statusMatches
            && (selectedSection === 'all' || source.sectionId === selectedSection)
            && (query === '' || haystack.includes(query));
    };

    const openRunDialog = ({ sourceId = '', sectionId = '', label = '' }) => {
        if (submitting) return;
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
        if (!snapshot) return;
        const allSources = view.flattenSources(snapshot);
        const priority = { unavailable: 0, fallback: 1, upstream_pending: 2, fresh: 3, disabled: 4 };
        const sources = allSources.filter(sourceMatches).sort((left, right) => {
            const stateOrder = (priority[view.sourcePresentation(left).key] ?? 2)
                - (priority[view.sourcePresentation(right).key] ?? 2);
            return stateOrder || String(left.label || left.id).localeCompare(String(right.label || right.id), 'ru');
        });
        sourcesBody.replaceChildren();
        sources.forEach((source) => {
            const row = document.createElement('tr');
            const sourceCell = element('td', 'parser-source-name');
            const name = element('b', '', source.label || source.id);
            const id = element('code', '', source.id);
            sourceCell.append(name, id, element('small', '', source.sectionLabel));

            const presentation = view.sourcePresentation(source);
            const stateCell = element('td', 'parser-current-data');
            stateCell.append(
                statusBadge(presentation.key),
                element('small', 'parser-source-state-copy', presentation.description),
            );
            const lastSuccess = view.formatDate(source.lastSuccessAt);
            stateCell.append(element(
                'small',
                'parser-source-success',
                lastSuccess === '—' ? 'Успешных публикаций ещё нет' : `Последний успех ${lastSuccess.relative}`,
            ));

            const attemptCell = element('td', 'parser-last-attempt');
            const attemptState = presentation.attemptFailed ? 'Ошибка получения' : 'Успешно';
            attemptCell.append(element(
                'span',
                `parser-attempt-state is-${presentation.attemptFailed ? 'bad' : 'good'}`,
                attemptState,
            ));
            attemptCell.append(dateCell(source.lastAttemptAt || source.lastSuccessAt));
            const diagnostics = sourceDiagnostics(source);
            if (diagnostics) attemptCell.append(diagnostics);

            const rowsCell = document.createElement('td');
            rowsCell.append(element('b', 'parser-row-count', view.formatNumber(source.rowsTotal)));
            rowsCell.append(element('small', '', view.publicationLabel(source.publicationChannel)));

            const scheduleCell = document.createElement('td');
            scheduleCell.append(element('span', 'parser-schedule', source.schedule || 'Ручной запуск'));
            const nextCell = document.createElement('td');
            nextCell.append(dateCell(source.nextRunAt));

            const actionCell = element('td', 'parser-row-action');
            const button = element('button', 'parser-run-button', 'Запустить');
            button.type = 'button';
            button.disabled = source.canRunManually === false || source.sectionEnabled === false || source.enabled === false;
            button.dataset.parserFocus = `source:${source.id}:run`;
            button.addEventListener('click', () => openRunDialog({ sourceId: source.id, label: source.label || source.id }));
            actionCell.append(button);
            row.append(sourceCell, stateCell, attemptCell, rowsCell, scheduleCell, nextCell, actionCell);
            sourcesBody.append(row);
        });
        empty.hidden = sources.length > 0;
        const hasSources = allSources.length > 0;
        root.querySelector('[data-parser-empty-message]').textContent = hasSources
            ? 'Источники с такими параметрами не найдены.' : 'В реестре пока нет источников данных.';
        root.querySelector('[data-parser-reset]').hidden = !hasSources;
        sourceCount.textContent = `Показано ${view.formatNumber(sources.length)} из ${view.formatNumber(allSources.length)} источников`;
    };

    const renderRunDetails = (run) => {
        const details = element('details', 'parser-run-details');
        const summary = element('summary', '', 'Подробности');
        summary.dataset.parserFocus = `run:${run.id}`;
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
        live.textContent = 'Нет связи · повторим автоматически';
        updated.textContent = snapshot ? 'Показано последнее полученное состояние' : 'Состояние недоступно';
        if (!snapshot) {
            sourcesBody.replaceChildren();
            sourceCount.textContent = 'Не удалось загрузить реестр';
            runsHost.replaceChildren(element('p', 'parser-empty', 'История запусков временно недоступна.'));
        }
    };

    const scheduleReload = () => {
        window.clearTimeout(reloadTimer);
        if (document.hidden) return;
        reloadTimer = window.setTimeout(() => {
            // Expanded diagnostics deliberately pause polling; ordinary focus does not.
            if (dialog.open || root.querySelector('.parser-source-diagnostics[open], .parser-run-details[open]')) {
                live.textContent = 'Автообновление приостановлено · закройте подробности или диалог';
                scheduleReload();
            } else load();
        }, snapshot?.activeRun ? 12_000 : 60_000);
    };

    async function load() {
        controller?.abort();
        const request = new AbortController();
        controller = request;
        let timedOut = false;
        const deadline = window.setTimeout(() => { timedOut = true; request.abort(); }, 15_000);
        refreshButton.disabled = true;
        summaryHost.setAttribute('aria-busy', 'true');
        try {
            const response = await fetch(endpoint, {
                credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: request.signal,
            });
            const payload = await response.json();
            if (!response.ok || payload.ok !== true || !payload.data) throw new Error(payload.message || 'Не удалось загрузить состояние.');
            if (!isSnapshot(payload.data)) throw new Error('Сервер вернул некорректный реестр источников.');
            if (controller !== request) return;
            const focusedKey = document.activeElement?.dataset.parserFocus;
            snapshot = payload.data;
            if (!snapshot.sections?.some((section) => section.id === selectedSection)) selectedSection = 'all';
            alert.hidden = true;
            renderSummary();
            renderSections();
            renderSources();
            renderRuns();
            if (focusedKey) {
                const control = Array.from(root.querySelectorAll('[data-parser-focus]'))
                    .find((node) => node.dataset.parserFocus === focusedKey && !node.disabled);
                (control || search).focus({ preventScroll: true });
            }
            const generated = view.formatDate(snapshot.generatedAt);
            updated.textContent = generated === '—' ? 'Состояние обновлено' : `Обновлено ${generated.relative}`;
            updated.title = generated === '—' ? '' : generated.exact;
            live.textContent = snapshot.activeRun ? 'Обновление каждые 12 секунд' : 'Обновление каждую минуту';
        } catch (error) {
            if (controller === request && (timedOut || error.name !== 'AbortError')) {
                showError(timedOut ? 'Сервер не ответил за 15 секунд. Попробуйте обновить состояние.' : error.message);
            }
        } finally {
            window.clearTimeout(deadline);
            if (controller === request) {
                refreshButton.disabled = false;
                summaryHost.setAttribute('aria-busy', 'false');
                scheduleReload();
            }
        }
    }

    const submitRun = async () => {
        if (submitting || !runReason.value.trim()) return;
        submitting = true;
        runConfirm.disabled = true;
        runReason.readOnly = true;
        const cancel = root.querySelector('[data-run-cancel]');
        cancel.disabled = true;
        const request = new AbortController();
        const deadline = window.setTimeout(() => request.abort(), 20_000);
        let rejected = false;
        runStatus.textContent = 'Ставим запуск в очередь…';
        const body = { action: 'run', reason: runReason.value.trim() };
        if (runSourceId.value) body.source_ids = [runSourceId.value];
        if (runSectionId.value) body.section_ids = [runSectionId.value];
        try {
            const response = await fetch(endpoint, {
                method: 'POST', credentials: 'same-origin',
                headers: { Accept: 'application/json', 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                body: JSON.stringify(body),
                signal: request.signal,
            });
            const payload = await response.json();
            if (!response.ok || payload.ok !== true) {
                rejected = response.status >= 400 && response.status < 500;
                throw new Error(payload.message || 'Не удалось запустить парсер.');
            }
            const feedback = root.querySelector('[data-parser-run-feedback]');
            feedback.textContent = payload.data?.deduplicated ? 'Такой запуск уже находится в очереди.' : 'Запуск добавлен в очередь.';
            feedback.hidden = false;
            dialog.close();
            load();
        } catch (error) {
            runStatus.textContent = rejected ? error.message
                : 'Подтверждение не получено. Запуск мог попасть в очередь. Закройте окно и обновите историю перед повторной попыткой.';
            runConfirm.disabled = !rejected;
        } finally {
            window.clearTimeout(deadline);
            submitting = false;
            runReason.readOnly = false;
            cancel.disabled = false;
        }
    };

    sectionHost.addEventListener('click', (event) => {
        const button = event.target.closest('[data-section-id]');
        if (!button) return;
        selectedSection = button.dataset.sectionId || 'all';
        saveFilters();
        renderSections();
        renderSources();
        sectionHost.querySelectorAll('[data-section-id]').forEach((item) => {
            if (item.dataset.sectionId === selectedSection) item.focus();
        });
    });
    const filterSources = () => { saveFilters(); renderSources(); };
    search.addEventListener('input', filterSources);
    statusFilter.addEventListener('change', filterSources);
    root.querySelector('[data-parser-reset]').addEventListener('click', () => {
        search.value = '';
        statusFilter.value = 'all';
        selectedSection = 'all';
        saveFilters();
        renderSections();
        renderSources();
        search.focus();
    });
    window.addEventListener('popstate', () => { readFilters(); renderSections(); renderSources(); });
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
        if (event.target === dialog && !submitting) dialog.close();
    });
    dialog.addEventListener('cancel', (event) => { if (submitting) event.preventDefault(); });
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
