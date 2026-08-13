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
            const hiddenColumns = new Set(storageGet(key));
            const configurable = headers.map((header, index) => ({ header, index }))
                .filter(({ index }) => index > 0 && index < headers.length - 1);
            const apply = () => {
                Array.from(table.rows).forEach((row) => {
                    Array.from(row.cells).forEach((cell, index) => {
                        cell.hidden = hiddenColumns.has(index);
                    });
                });
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
})();
