(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.ParserControlView = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const statusCatalog = {
        ok: { label: 'Работает', tone: 'good' },
        ready: { label: 'Работает', tone: 'good' },
        running: { label: 'Выполняется', tone: 'info' },
        queued: { label: 'В очереди', tone: 'info' },
        succeeded: { label: 'Успешно', tone: 'good' },
        partial: { label: 'Частично', tone: 'warning' },
        warning: { label: 'Требует внимания', tone: 'warning' },
        missing: { label: 'Нет данных', tone: 'muted' },
        failed: { label: 'Ошибка', tone: 'bad' },
        error: { label: 'Ошибка', tone: 'bad' },
        disabled: { label: 'Отключён', tone: 'muted' },
    };

    function statusMeta(value) {
        const key = String(value || 'missing').toLowerCase();
        return { key, ...(statusCatalog[key] || { label: key, tone: 'muted' }) };
    }

    function flattenSources(snapshot) {
        const sections = Array.isArray(snapshot?.sections) ? snapshot.sections : [];
        return sections.flatMap((section) => {
            const sources = Array.isArray(section?.sources) ? section.sources : [];
            return sources.map((source) => ({
                ...source,
                sectionId: String(section.id || ''),
                sectionLabel: String(section.label || section.id || 'Раздел'),
                sectionEnabled: section.enabled !== false,
            }));
        });
    }

    function buildSummary(snapshot) {
        const sources = flattenSources(snapshot);
        const activeRun = snapshot?.activeRun || null;
        const issueStates = new Set(['warning', 'partial', 'missing', 'error', 'failed']);
        const issues = sources.filter((source) => issueStates.has(String(source.health || source.state || '').toLowerCase())).length;
        const healthy = sources.filter((source) => ['ok', 'ready'].includes(String(source.health || source.state || '').toLowerCase())).length;
        const nextRunAt = sources
            .map((source) => source.nextRunAt)
            .filter(Boolean)
            .sort()[0] || null;
        const rows = sources.reduce((total, source) => total + (Number.isFinite(Number(source.rowsTotal)) ? Number(source.rowsTotal) : 0), 0);
        return { total: sources.length, healthy, issues, rows, nextRunAt, activeRun };
    }

    function formatNumber(value) {
        const number = Number(value);
        return Number.isFinite(number) ? new Intl.NumberFormat('ru-RU').format(number) : '—';
    }

    function formatDate(value, nowValue) {
        if (!value) return '—';
        const timestamp = Date.parse(value);
        if (!Number.isFinite(timestamp)) return '—';
        const now = Number.isFinite(nowValue) ? nowValue : Date.now();
        const delta = timestamp - now;
        const absolute = Math.abs(delta);
        let unit = 'minute';
        let divisor = 60_000;
        if (absolute >= 86_400_000) {
            unit = 'day';
            divisor = 86_400_000;
        } else if (absolute >= 3_600_000) {
            unit = 'hour';
            divisor = 3_600_000;
        }
        const relative = new Intl.RelativeTimeFormat('ru-RU', { numeric: 'auto' })
            .format(Math.round(delta / divisor), unit);
        const exact = new Intl.DateTimeFormat('ru-RU', {
            day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC',
        }).format(timestamp) + ' UTC';
        return { relative, exact };
    }

    function runProgress(run) {
        const total = Math.max(0, Number(run?.totalSources) || 0);
        const completed = Math.max(0, Number(run?.completedSources) || 0);
        const failed = Math.max(0, Number(run?.failedSources) || 0);
        const done = Math.min(total, completed + failed);
        return { total, done, percent: total > 0 ? Math.round((done / total) * 100) : 0 };
    }

    return { statusMeta, flattenSources, buildSummary, formatNumber, formatDate, runProgress };
}));
