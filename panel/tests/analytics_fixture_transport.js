// Test-only transport. No producer, OAuth or database calls are performed.
(() => {
    const fixture = window.analyticsFixture;
    Object.assign(fixture, {calls: [], mode: 'success', delay: 0, ignoreAbort: false});
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, options = {}) => {
        const url = new URL(input, location.origin);
        if (url.origin !== location.origin || url.pathname !== '/analytics.php') return originalFetch(input, options);
        fixture.calls.push(url.href);
        const module = url.searchParams.get('module') || 'overview';
        const payload = structuredClone(fixture.payload);
        if (module !== 'overview') payload.title = 'Тестовый набор: ' + module;
        const mode = fixture.mode;
        const ignoreAbort = fixture.ignoreAbort;
        if (mode === 'empty') payload.rows = [];
        return new Promise((resolve, reject) => {
            const abort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); };
            const timer = setTimeout(() => {
                options.signal?.removeEventListener('abort', abort);
                if (mode === 'offline') reject(new TypeError('Нет соединения'));
                else resolve({ok: mode !== 'error', status: mode === 'error' ? 503 : 200,
                    json: async () => mode === 'error' ? {ok: false, detail: 'Тестовая ошибка сервиса'} : payload});
            }, fixture.delay);
            if (!ignoreAbort) {
                options.signal?.addEventListener('abort', abort, {once: true});
                if (options.signal?.aborted) abort();
            }
        });
    };
})();
