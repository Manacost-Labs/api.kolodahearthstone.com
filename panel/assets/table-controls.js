(() => {
    'use strict';
    document.querySelectorAll('[data-autofilter]').forEach((form) => {
        const search = form.querySelector('[data-filter-search]');
        let searchTimer = 0;
        const clearPage = () => {
            window.clearTimeout(searchTimer);
            const page = form.querySelector('input[name="page"]');
            if (page) page.remove();
        };
        const submitFilters = () => {
            clearPage();
            form.requestSubmit();
        };

        form.querySelectorAll('select').forEach((select) => {
            select.addEventListener('change', submitFilters);
        });
        search?.addEventListener('input', () => {
            window.clearTimeout(searchTimer);
            const value = search.value.trim();
            if (value !== '' && value.length < 2) return;
            searchTimer = window.setTimeout(submitFilters, 520);
        });
        form.addEventListener('submit', clearPage);
    });

    document.querySelectorAll('[data-table-density]').forEach((button) => {
        const workspace = button.closest('.data-panel, .analytics-hub, .token-list-panel');
        const storageKey = button.dataset.densityKey || (workspace?.classList.contains('analytics-hub')
            ? 'analyticsTableDensity'
            : 'catalogueTableDensity');
        const setDensity = (compact) => {
            workspace?.classList.toggle('is-compact-table', compact);
            button.setAttribute('aria-pressed', compact ? 'true' : 'false');
            button.textContent = compact ? 'Обычно' : 'Компактно';
        };
        try {
            setDensity(window.localStorage.getItem(storageKey) === 'compact');
        } catch (error) {
            setDensity(false);
        }
        button.addEventListener('click', () => {
            const compact = !workspace?.classList.contains('is-compact-table');
            setDensity(compact);
            try {
                window.localStorage.setItem(storageKey, compact ? 'compact' : 'normal');
            } catch (error) {
                // Density remains usable for this page when storage is unavailable.
            }
        });
    });

    // A slash focuses the catalogue search without stealing normal form input.
    const primarySearch = document.querySelector('[data-filter-search]');
    document.addEventListener('keydown', (event) => {
        if (event.key !== '/' || event.ctrlKey || event.metaKey || event.altKey) return;
        const target = event.target;
        if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return;
        event.preventDefault();
        primarySearch?.focus();
    });
})();
