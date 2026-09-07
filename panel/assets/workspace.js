(() => {
    'use strict';
    const themes = new Set(['light', 'dark', 'tavern', 'arcane']);
    const buttons = document.querySelectorAll('[data-theme-option]');
    const setTheme = (value, persist = false) => {
        const theme = themes.has(value) ? value : 'light';
        document.documentElement.dataset.theme = theme;
        buttons.forEach((button) => {
            const active = button.dataset.themeOption === theme;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', String(active));
        });
        if (persist) {
            try { localStorage.setItem('bgCardsTheme', theme); } catch { /* optional preference */ }
        }
    };
    let saved = 'light';
    try { saved = localStorage.getItem('bgCardsTheme') || 'light'; } catch { /* optional preference */ }
    setTheme(saved);
    buttons.forEach((button) => button.addEventListener('click', () => setTheme(button.dataset.themeOption, true)));
    const sidebar = document.querySelector('.sidebar');
    const toggle = document.querySelector('[data-sidebar-toggle]');
    toggle?.addEventListener('click', () => {
        const open = sidebar?.classList.toggle('nav-open') || false;
        toggle.setAttribute('aria-expanded', String(open));
    });
})();
