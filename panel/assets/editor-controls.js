(() => {
    'use strict';
    const page = document.querySelector('[data-terms-page]');
    if (!page) return;
    const search = page.querySelector('[data-term-filter]');
    const buttons = [...page.querySelectorAll('button[data-term-status]')];
    const rows = [...page.querySelectorAll('[data-term-row]')];
    let selected = 'all';
    const filter = () => {
        const query = (search?.value || '').trim().toLocaleLowerCase('ru-RU');
        let count = 0;
        rows.forEach(row => {
            const value = row.querySelector('input:not([type=hidden])')?.value.trim() || '';
            const status = value ? 'translated' : 'missing';
            const text = `${row.querySelector('code')?.textContent || ''} ${value} ${row.querySelector('small')?.textContent || ''}`.toLocaleLowerCase('ru-RU');
            row.hidden = !(text.includes(query) && (selected === 'all' || selected === status));
            if (!row.hidden) count++;
        });
        page.querySelector('[data-term-count]').textContent = `Найдено терминов: ${count} из ${rows.length}`;
        page.querySelector('[data-term-empty]').hidden = count !== 0;
        page.querySelectorAll('[data-term-section]').forEach(section => {
            section.hidden = ![...section.querySelectorAll('[data-term-row]')].some(row => !row.hidden);
        });
        buttons.forEach(button => {
            const active = button.dataset.termStatus === selected;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
        });
    };
    buttons.forEach(button => button.addEventListener('click', () => { selected = button.dataset.termStatus; filter(); }));
    search?.addEventListener('input', filter);
    page.querySelector('[data-term-reset]')?.addEventListener('click', () => { search.value = ''; selected = 'all'; filter(); search.focus(); });
    filter();
})();
