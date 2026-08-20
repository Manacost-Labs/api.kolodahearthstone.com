(function (root, factory) {
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.ParserControlView = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const statusCatalog = {
        ok: { label: 'Работает', tone: 'good' },
        ready: { label: 'Работает', tone: 'good' },
        fresh: { label: 'Свежие данные', tone: 'good' },
        fallback: { label: 'Резервный набор', tone: 'warning' },
        unavailable: { label: 'Данных нет', tone: 'bad' },
        running: { label: 'Выполняется', tone: 'info' },
        queued: { label: 'В очереди', tone: 'info' },
        succeeded: { label: 'Успешно', tone: 'good' },
        partial: { label: 'Частично', tone: 'warning' },
        warning: { label: 'Требует внимания', tone: 'warning' },
        upstream_pending: { label: 'Ожидает публикации', tone: 'info' },
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

    function sourcePresentation(source) {
        const health = String(source?.health || source?.state || 'missing').toLowerCase();
        const disabled = source?.sectionEnabled === false
            || source?.enabled === false
            || health === 'disabled';
        const hasPublishedData = Boolean(source?.lastSuccessAt)
            || Number(source?.rowsTotal) > 0
            || !['', 'unavailable'].includes(String(source?.publicationChannel || ''));
        const servingFallback = source?.servingCachedDataset === true
            || (hasPublishedData && ['warning', 'partial', 'error', 'failed'].includes(health));

        if (disabled) {
            return {
                key: 'disabled', filter: 'disabled', label: 'Отключён', tone: 'muted',
                dataAvailable: hasPublishedData, attemptFailed: false,
                description: 'Автоматические и ручные запуски отключены.',
            };
        }
        if (health === 'upstream_pending') {
            return {
                key: 'upstream_pending', filter: 'fallback', label: 'Ожидает публикации', tone: 'info',
                dataAvailable: hasPublishedData, attemptFailed: false,
                description: 'Текущие данные доступны; источник ещё не опубликовал обновление.',
            };
        }
        if (servingFallback) {
            return {
                key: 'fallback', filter: 'fallback', label: 'Резервный набор', tone: 'warning',
                dataAvailable: true, attemptFailed: Boolean(source?.lastError) || health !== 'ok',
                description: 'Публичный API продолжает отдавать последний успешный набор.',
            };
        }
        if (['ok', 'ready'].includes(health)) {
            return {
                key: 'fresh', filter: 'fresh', label: 'Свежие данные', tone: 'good',
                dataAvailable: true, attemptFailed: false,
                description: 'Последний сбор опубликован и доступен.',
            };
        }
        if (health === 'partial' && hasPublishedData) {
            return {
                key: 'fallback', filter: 'fallback', label: 'Частичные данные', tone: 'warning',
                dataAvailable: true, attemptFailed: true,
                description: 'Опубликована только подтверждённая часть набора.',
            };
        }
        return {
            key: 'unavailable', filter: 'unavailable', label: 'Данных нет', tone: 'bad',
            dataAvailable: false, attemptFailed: true,
            description: health === 'missing'
                ? 'Источник ещё не создал пригодный набор.'
                : 'Последняя попытка завершилась без пригодных данных.',
        };
    }

    function errorSummary(value) {
        const message = String(value || '').trim();
        if (!message) return '';
        const normalized = message.toLowerCase();
        if (normalized.includes('row_retrieval has unexplained dropped rows')) {
            return 'Проверка полноты обнаружила необъяснённые пропуски строк.';
        }
        if (normalized.includes('proxy_payment') || normalized.includes('payment required')) {
            return 'Прокси отклонил запрос из-за ограничения оплаты.';
        }
        if (normalized.includes('timeout') || normalized.includes('timed out')) {
            return 'Источник не ответил за отведённое время.';
        }
        if (normalized.includes('cloudflare') || normalized.includes('captcha') || normalized.includes('403')) {
            return 'Источник заблокировал автоматический запрос.';
        }
        if (normalized.includes('contract failed') || normalized.includes('quality check failed')) {
            return 'Ответ не прошёл проверку структуры и качества.';
        }
        if (normalized.includes('parse')) {
            return 'Страница получена, но данные не удалось корректно разобрать.';
        }
        const first = message.split(';', 1)[0].replace(/\s+/g, ' ');
        return first.length > 180 ? `${first.slice(0, 177)}…` : first;
    }

    function publicationLabel(value) {
        const channel = String(value || '').toLowerCase();
        if (channel === 'stable') return 'Опубликованный набор';
        if (channel === 'early') return 'Предварительный набор';
        if (channel === 'stable_baseline' || channel === 'stable baseline') return 'Стабильный резерв';
        if (channel === 'unavailable') return 'Набор отсутствует';
        return value ? String(value) : 'Канал не указан';
    }

    function buildSummary(snapshot) {
        const sources = flattenSources(snapshot);
        const activeRun = snapshot?.activeRun || null;
        const presentations = sources.map(sourcePresentation);
        const fresh = presentations.filter((item) => item.filter === 'fresh').length;
        const fallback = presentations.filter((item) => item.filter === 'fallback').length;
        const unavailable = presentations.filter((item) => item.filter === 'unavailable').length;
        const disabled = presentations.filter((item) => item.filter === 'disabled').length;
        const issues = presentations.filter((item) => item.attemptFailed).length;
        const healthy = fresh + fallback;
        const nextRunAt = sources
            .map((source) => source.nextRunAt)
            .filter(Boolean)
            .sort()[0] || null;
        const rows = sources.reduce((total, source) => total + (Number.isFinite(Number(source.rowsTotal)) ? Number(source.rowsTotal) : 0), 0);
        return {
            total: sources.length, healthy, issues, fresh, fallback, unavailable, disabled,
            operational: sources.length - disabled, rows, nextRunAt, activeRun,
        };
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

    return {
        statusMeta, flattenSources, sourcePresentation, errorSummary, publicationLabel,
        buildSummary, formatNumber, formatDate, runProgress,
    };
}));
