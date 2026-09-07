(() => {
    'use strict';
    document.querySelectorAll('[data-autofilter]').forEach((form) => {
        const search = form.querySelector('[data-filter-search]');
        let searchTimer = 0;
        let composing = false;
        let submittedSearch = search?.value.trim() || '';
        const status = form.querySelector('[data-catalog-request-status]');
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
        const scheduleSearch = () => {
            window.clearTimeout(searchTimer);
            if (composing) return;
            const value = search?.value.trim() || '';
            if (value === submittedSearch) return;
            if (value !== '' && value.length < 2) return;
            searchTimer = window.setTimeout(submitFilters, 520);
        };
        search?.addEventListener('input', scheduleSearch);
        search?.addEventListener('compositionstart', () => {
            composing = true;
            window.clearTimeout(searchTimer);
        });
        search?.addEventListener('compositionend', () => { composing = false; scheduleSearch(); });
        form.addEventListener('submit', () => {
            clearPage();
            submittedSearch = search?.value.trim() || '';
            if (status) status.textContent = 'Загружаем данные…';
        });
        window.addEventListener('pageshow', () => { if (status) status.textContent = ''; });
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
        if (primarySearch?.closest('[inert]')) return;
        const target = event.target;
        if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return;
        if (target instanceof HTMLElement && target.isContentEditable) return;
        event.preventDefault();
        primarySearch?.focus();
    });
})();
