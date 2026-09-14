(() => {
    'use strict';

    const palette = document.querySelector('[data-command-palette]');
    const commandButton = document.querySelector('[data-command-open]');
    const commandSearch = palette?.querySelector('[data-command-search]');
    const commandClose = palette?.querySelector('[data-command-close]');
    const commandItems = Array.from(palette?.querySelectorAll('[data-command-item]') || []);
    const commandGroups = Array.from(palette?.querySelectorAll('[data-command-group]') || []);
    const commandEmpty = palette?.querySelector('[data-command-empty]');
    let activeCommandIndex = -1;

    const visibleCommandItems = () => commandItems.filter((item) => !item.hidden);
    const setActiveCommand = (index) => {
        const visible = visibleCommandItems();
        commandItems.forEach((item) => item.classList.remove('is-active'));
        if (!visible.length) {
            activeCommandIndex = -1;
            return;
        }
        activeCommandIndex = (index + visible.length) % visible.length;
        visible[activeCommandIndex].classList.add('is-active');
        visible[activeCommandIndex].scrollIntoView({ block: 'nearest' });
    };
    const filterCommands = () => {
        const query = (commandSearch?.value || '').trim().toLocaleLowerCase('ru-RU');
        commandItems.forEach((item) => {
            item.hidden = query !== '' && !(item.dataset.commandText || '').includes(query);
        });
        commandGroups.forEach((group) => {
            group.hidden = !group.querySelector('[data-command-item]:not([hidden])');
        });
        if (commandEmpty) commandEmpty.hidden = visibleCommandItems().length > 0;
        setActiveCommand(0);
    };
    const openPalette = () => {
        if (!palette) return;
        palette.showModal();
        if (commandSearch) commandSearch.value = '';
        filterCommands();
        window.setTimeout(() => commandSearch?.focus(), 0);
    };

    commandButton?.addEventListener('click', openPalette);
    commandClose?.addEventListener('click', () => palette?.close());
    commandSearch?.addEventListener('input', filterCommands);
    commandSearch?.addEventListener('keydown', (event) => {
        if (event.key === 'ArrowDown') {
            event.preventDefault();
            setActiveCommand(activeCommandIndex + 1);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            setActiveCommand(activeCommandIndex - 1);
        } else if (event.key === 'Enter') {
            const active = visibleCommandItems()[activeCommandIndex];
            if (active) {
                event.preventDefault();
                active.click();
            }
        }
    });
    palette?.addEventListener('click', (event) => {
        if (event.target === palette) palette.close();
    });
    document.addEventListener('keydown', (event) => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase() === 'k') {
            event.preventDefault();
            if (palette?.open) palette.close();
            else openPalette();
        }
    });

    const storageGet = (key) => {
        try {
            const value = JSON.parse(localStorage.getItem(key) || '[]');
            return Array.isArray(value) ? value.filter(Number.isInteger) : [];
        } catch (error) {
            return [];
        }
    };
    const storageSet = (key, value) => {
        try { localStorage.setItem(key, JSON.stringify(value)); } catch (error) { /* optional preference */ }
    };
    const storageHas = (key) => {
        try { return localStorage.getItem(key) !== null; } catch (error) { return false; }
    };

    document.querySelectorAll('[data-column-picker]').forEach((picker) => {
        const menu = picker.querySelector('[data-column-picker-menu]');
        const targetSelector = picker.dataset.tableTarget || 'table';
        let signature = '';

        const build = () => {
            const scope = picker.closest('.data-panel, .analytics-hub, .token-list-panel') || document;
            const table = scope.querySelector(targetSelector);
            const headers = Array.from(table?.querySelectorAll('thead tr:first-child > th') || []);
            if (!table || headers.length < 3 || !menu) {
                picker.hidden = true;
                return;
            }
            picker.hidden = false;
            const moduleKey = table.dataset.module || picker.dataset.storageKey || 'default';
            const key = `panelColumns:${moduleKey}`;
            const nextSignature = `${key}:${headers.map((header) => header.textContent.trim()).join('|')}`;
            if (signature === nextSignature) return;
            signature = nextSignature;
            const defaultHidden = (table.dataset.defaultHidden || '')
                .split(',')
                .map((value) => Number.parseInt(value, 10))
                .filter(Number.isInteger);
            const hiddenColumns = new Set(storageHas(key) ? storageGet(key) : defaultHidden);
            const configurable = headers.map((header, index) => ({ header, index }))
                .filter(({ index }) => index > 0 && index < headers.length - 1);
            const apply = () => {
                Array.from(table.rows).forEach((row) => {
                    Array.from(row.cells).forEach((cell, index) => {
                        cell.hidden = hiddenColumns.has(index);
                    });
                });
                table.classList.toggle('has-expanded-columns', hiddenColumns.size < defaultHidden.length);
            };
            menu.replaceChildren();
            const heading = document.createElement('div');
            heading.className = 'column-picker-head';
            const title = document.createElement('b');
            title.textContent = 'Видимые колонки';
            const reset = document.createElement('button');
            reset.type = 'button';
            reset.textContent = 'Сбросить';
            reset.addEventListener('click', () => {
                hiddenColumns.clear();
                storageSet(key, []);
                menu.querySelectorAll('input').forEach((input) => { input.checked = true; });
                apply();
            });
            heading.append(title, reset);
            menu.append(heading);
            configurable.forEach(({ header, index }) => {
                const label = document.createElement('label');
                const checkbox = document.createElement('input');
                checkbox.type = 'checkbox';
                checkbox.checked = !hiddenColumns.has(index);
                const text = document.createElement('span');
                text.textContent = header.textContent.trim() || `Колонка ${index + 1}`;
                checkbox.addEventListener('change', () => {
                    if (checkbox.checked) hiddenColumns.delete(index);
                    else hiddenColumns.add(index);
                    storageSet(key, Array.from(hiddenColumns));
                    apply();
                });
                label.append(checkbox, text);
                menu.append(label);
            });
            apply();
        };

        picker.addEventListener('toggle', () => { if (picker.open) build(); });
        const observer = new MutationObserver(build);
        observer.observe(picker.closest('.data-panel, .analytics-hub, .token-list-panel') || document.body, { childList: true, subtree: true });
        build();
    });

    document.addEventListener('click', (event) => {
        document.querySelectorAll('[data-column-picker][open]').forEach((picker) => {
            if (event.target instanceof Node && !picker.contains(event.target)) picker.removeAttribute('open');
        });
    });

    document.querySelectorAll('[data-table-navigation]').forEach((navigation) => {
        const scope = navigation.closest('.data-panel, .analytics-hub, .parser-workspace, .token-workspace') || document;
        const configuredTarget = scope.querySelector(navigation.dataset.tableTarget || '.cards-table');
        const leftButton = navigation.querySelector('[data-table-scroll-left]');
        const rightButton = navigation.querySelector('[data-table-scroll-right]');
        const status = navigation.querySelector('[data-table-scroll-status]');
        let scrollTarget = configuredTarget;
        let observedScrollTarget = null;

        const connectScrollTarget = (target) => {
            if (!target || target === observedScrollTarget) return;
            observedScrollTarget?.removeEventListener('scroll', updateTableNavigation);
            observedScrollTarget = target;
            observedScrollTarget.addEventListener('scroll', updateTableNavigation, {passive: true});
        };

        const resolveTarget = () => {
            const nested = configuredTarget?.querySelector('.library-table, .constructed-table, .analytics-table-scroll, .parser-table-scroll, .token-table-shell');
            scrollTarget = nested && nested.scrollWidth > nested.clientWidth ? nested : configuredTarget;
            connectScrollTarget(scrollTarget);
            return scrollTarget;
        };
        const updateTableNavigation = () => {
            const target = resolveTarget();
            if (!target) return;
            const maximum = Math.max(0, target.scrollWidth - target.clientWidth);
            const position = Math.max(0, Math.min(maximum, target.scrollLeft));
            const isScrollable = maximum > 4;
            const atStart = position <= 4;
            const atEnd = maximum - position <= 4;
            navigation.hidden = !isScrollable;
            navigation.dataset.position = atStart ? 'start' : (atEnd ? 'end' : 'middle');
            if (leftButton) leftButton.disabled = !isScrollable || atStart;
            if (rightButton) rightButton.disabled = !isScrollable || atEnd;
            if (status) status.textContent = atStart ? 'Начало' : (atEnd ? 'Конец' : `${Math.round((position / maximum) * 100)}%`);
        };
        const move = (direction) => {
            const target = resolveTarget();
            if (!target) return;
            target.scrollBy({left: direction * Math.max(240, target.clientWidth * .72), behavior: 'smooth'});
        };

        leftButton?.addEventListener('click', () => move(-1));
        rightButton?.addEventListener('click', () => move(1));
        window.addEventListener('resize', updateTableNavigation, {passive: true});
        if (configuredTarget) {
            const observer = new MutationObserver(updateTableNavigation);
            observer.observe(configuredTarget, {childList: true, subtree: true});
        }
        updateTableNavigation();
    });

    const sidebar = document.querySelector('.sidebar');
    document.querySelectorAll('.side-link').forEach((link) => {
        link.addEventListener('click', () => {
            sidebar?.classList.remove('nav-open');
            document.querySelector('[data-sidebar-toggle]')?.setAttribute('aria-expanded', 'false');
        });
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && sidebar?.classList.contains('nav-open')) {
            sidebar.classList.remove('nav-open');
            document.querySelector('[data-sidebar-toggle]')?.setAttribute('aria-expanded', 'false');
        }
    });

    const workspaceView = window.CatalogWorkspaceView;
    const beginNavigation = () => {
        document.documentElement.classList.add('is-navigating');
        document.querySelector('.workspace')?.setAttribute('aria-busy', 'true');
    };
    const endNavigation = () => {
        document.documentElement.classList.remove('is-navigating');
        document.querySelector('.workspace')?.removeAttribute('aria-busy');
    };

    document.addEventListener('click', (event) => {
        const target = event.target;
        if (!(target instanceof Element) || !workspaceView) return;
        const link = target.closest('a[href]');
        if (!link || !workspaceView.shouldSignalNavigation({
            href: link.getAttribute('href') || '',
            target: link.target,
            download: link.hasAttribute('download'),
            button: event.button,
            ctrlKey: event.ctrlKey,
            metaKey: event.metaKey,
            shiftKey: event.shiftKey,
            altKey: event.altKey,
            defaultPrevented: event.defaultPrevented,
        }, window.location.href)) return;
        beginNavigation();
    });
    document.addEventListener('submit', (event) => {
        const form = event.target;
        if (form instanceof HTMLFormElement && form.method.toLocaleLowerCase() === 'get') beginNavigation();
    });
    window.addEventListener('pageshow', endNavigation);

    const inspector = document.querySelector('[data-catalog-inspector]');
    const catalogRows = Array.from(document.querySelectorAll('[data-catalog-record]'));
    let lastSelectedRow = null;
    const inspectorFields = inspector ? Object.fromEntries(
        Array.from(inspector.querySelectorAll('[data-inspector-field]'))
            .map((field) => [field.dataset.inspectorField, field]),
    ) : {};
    const setInspectorText = (name, value) => {
        const field = inspectorFields[name];
        if (field) field.textContent = value;
    };
    const selectCatalogRow = (row, moveFocus = false) => {
        if (!inspector || !workspaceView || !(row instanceof HTMLElement)) return;
        const record = workspaceView.recordFromDataset(row.dataset);
        catalogRows.forEach((item) => {
            const selected = item === row;
            item.classList.toggle('is-selected', selected);
            item.setAttribute('aria-selected', selected ? 'true' : 'false');
        });
        lastSelectedRow = row;
        setInspectorText('name', record.name);
        setInspectorText('id', record.id);
        setInspectorText('dbf', record.dbf);
        setInspectorText('englishName', record.englishName);
        setInspectorText('type', record.type);
        setInspectorText('tier', record.tier);
        setInspectorText('attack', record.attack);
        setInspectorText('health', record.health);
        setInspectorText('pool', record.pool);
        setInspectorText('duo', record.duo);
        setInspectorText('updated', record.updated);

        const image = inspector.querySelector('[data-inspector-image]');
        const imageEmpty = inspector.querySelector('[data-inspector-image-empty]');
        if (image instanceof HTMLImageElement) {
            image.src = record.image;
            image.alt = record.image ? `Карта ${record.name}` : '';
            image.hidden = !record.image;
        }
        if (imageEmpty) imageEmpty.hidden = Boolean(record.image);

        const mechanics = inspector.querySelector('[data-inspector-mechanics]');
        if (mechanics) {
            mechanics.replaceChildren();
            const values = record.mechanics.length ? record.mechanics : ['Не указаны'];
            values.forEach((value) => {
                const item = document.createElement('span');
                item.textContent = value;
                mechanics.append(item);
            });
        }
        [['edit', record.editUrl], ['stats', record.statsUrl]].forEach(([name, href]) => {
            const link = inspector.querySelector(`[data-inspector-link="${name}"]`);
            if (link instanceof HTMLAnchorElement) {
                link.href = href || '#';
                link.hidden = !href;
            }
        });
        inspector.hidden = false;
        document.body.classList.add('catalog-inspector-open');
        if (moveFocus) inspector.querySelector('[data-inspector-close]')?.focus();
    };
    const closeInspector = () => {
        if (!inspector) return;
        inspector.hidden = true;
        document.body.classList.remove('catalog-inspector-open');
        catalogRows.forEach((row) => {
            row.classList.remove('is-selected');
            row.setAttribute('aria-selected', 'false');
        });
        lastSelectedRow?.focus();
    };

    catalogRows.forEach((row) => {
        row.addEventListener('click', (event) => {
            const target = event.target;
            if (target instanceof Element && target.closest('a, button, input, select, details')) {
                if (!target.closest('[data-catalog-open]')) return;
            }
            selectCatalogRow(row);
        });
        row.addEventListener('keydown', (event) => {
            if (event.key !== 'Enter' && event.key !== ' ') return;
            event.preventDefault();
            selectCatalogRow(row, true);
        });
    });
    inspector?.querySelector('[data-inspector-close]')?.addEventListener('click', closeInspector);
    if (catalogRows.length && window.matchMedia('(min-width: 1121px)').matches) selectCatalogRow(catalogRows[0]);
})();
