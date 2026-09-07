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
        if (!palette || commandButton?.closest('[inert]')) return;
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

    const storageGet = (key, fallback = []) => {
        try {
            const stored = localStorage.getItem(key);
            if (stored === null) return fallback;
            const value = JSON.parse(stored);
            return Array.isArray(value) ? value.filter(Number.isInteger) : [];
        } catch (error) {
            return fallback;
        }
    };
    const storageSet = (key, value) => {
        try { localStorage.setItem(key, JSON.stringify(value)); } catch (error) { /* optional preference */ }
    };

    document.querySelectorAll('[data-column-picker]').forEach((picker) => {
        const menu = picker.querySelector('[data-column-picker-menu]');
        const targetSelector = picker.dataset.tableTarget || 'table';
        let signature = '';
        let currentTable = null;
        let applyColumns = () => {};

        const build = () => {
            const scope = picker.closest('.data-panel, .analytics-hub, .token-list-panel, .parser-sources-panel') || document;
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
            if (signature === nextSignature && currentTable === table) {
                applyColumns();
                return;
            }
            signature = nextSignature;
            currentTable = table;
            const defaults = (picker.dataset.defaultHidden || '').split(',').filter(Boolean).map(Number);
            const hiddenColumns = new Set(storageGet(key, defaults).filter((index) => index > 0 && index < headers.length - 1));
            const configurable = headers.map((header, index) => ({ header, index }))
                .filter(({ index }) => index > 0 && index < headers.length - 1);
            const apply = () => {
                Array.from(table.rows).forEach((row) => {
                    Array.from(row.cells).forEach((cell, index) => {
                        const hidden = cell.colSpan === 1 && hiddenColumns.has(index);
                        if (cell.hidden !== hidden) cell.hidden = hidden;
                    });
                });
            };
            applyColumns = apply;
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
        const observer = new MutationObserver((changes) => {
            // Reader/gallery/menu updates are not table updates. Still handle
            // refreshed rows and whole-table replacement in analytics/parsers.
            if (changes.some(({target, addedNodes, removedNodes}) => !picker.contains(target) && (
                currentTable?.contains(target) || [...addedNodes, ...removedNodes].some(node =>
                    node instanceof Element && (node === currentTable || node.contains(currentTable)
                        || node.matches(targetSelector) || node.querySelector(targetSelector)))
            ))) build();
        });
        observer.observe(picker.closest('.data-panel, .analytics-hub, .token-list-panel, .parser-sources-panel') || document.body, { childList: true, subtree: true });
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
            const toggle = document.querySelector('[data-sidebar-toggle]');
            toggle?.setAttribute('aria-expanded', 'false');
            toggle?.focus();
        }
    });
})();
