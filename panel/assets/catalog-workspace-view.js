(function (root, factory) {
    'use strict';

    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    if (root) root.CatalogWorkspaceView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
    'use strict';

    const text = (value, fallback = '—') => {
        const normalized = String(value ?? '').trim();
        return normalized || fallback;
    };

    const shouldSignalNavigation = (candidate, currentHref) => {
        if (!candidate || candidate.defaultPrevented || candidate.button > 0) return false;
        if (candidate.ctrlKey || candidate.metaKey || candidate.shiftKey || candidate.altKey) return false;
        if (candidate.download || (candidate.target && candidate.target !== '_self')) return false;

        try {
            const current = new URL(currentHref);
            const target = new URL(candidate.href, current);
            if (!['http:', 'https:'].includes(target.protocol) || target.origin !== current.origin) return false;
            if (target.pathname === current.pathname && target.search === current.search) return false;
            return true;
        } catch (error) {
            return false;
        }
    };

    const recordFromDataset = (dataset = {}) => ({
        id: text(dataset.recordId),
        dbf: text(dataset.recordDbf),
        name: text(dataset.recordName, 'Без названия'),
        englishName: text(dataset.recordEnglishName),
        image: text(dataset.recordImage, ''),
        type: text(dataset.recordType, 'Не указан'),
        tier: text(dataset.recordTier),
        attack: text(dataset.recordAttack),
        health: text(dataset.recordHealth),
        mechanics: String(dataset.recordMechanics || '')
            .split('|')
            .map((item) => item.trim())
            .filter(Boolean),
        updated: text(dataset.recordUpdated, 'Нет данных'),
        pool: text(dataset.recordPool, 'Не указано'),
        duo: text(dataset.recordDuo, 'Не указано'),
        editUrl: text(dataset.recordEditUrl, ''),
        statsUrl: text(dataset.recordStatsUrl, ''),
    });

    return { recordFromDataset, shouldSignalNavigation };
});
