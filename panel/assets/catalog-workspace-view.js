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

    const recordFromDataset = (dataset = {}) => {
        const id = text(dataset.recordId);
        const dbf = text(dataset.recordDbf);
        const goldenId = text(dataset.recordGoldenId);
        const goldenDbf = text(dataset.recordGoldenDbf);
        const image = text(dataset.recordImage, '');
        const images = [
            ['card', 'Обычная карта', image],
            ['golden', 'Золотая карта', text(dataset.recordGoldenImage, '')],
            ['art', 'Арт без рамки', text(dataset.recordArtImage, '')],
            ['golden-art', 'Золотой арт', text(dataset.recordGoldenArtImage, '')],
            ['framed', 'Арт в рамке', text(dataset.recordFramedImage, '')],
            ['golden-framed', 'Золотой арт в рамке', text(dataset.recordGoldenFramedImage, '')],
            ['horizontal', 'Горизонтальный арт', text(dataset.recordHorizontalImage, '')],
            ['golden-horizontal', 'Золотой горизонтальный арт', text(dataset.recordGoldenHorizontalImage, '')],
        ].filter(([, , url]) => Boolean(url)).map(([kind, label, url]) => ({ kind, label, url }));
        const apiLinks = [
            ['Обычная · card_id', id, id !== '—' ? `/api/v1/cards/${encodeURIComponent(id)}` : ''],
            ['Обычная · dbf', dbf, dbf !== '—' ? `/api/v1/cards/by-dbf/${encodeURIComponent(dbf)}` : ''],
            ['Золотая · card_id', goldenId, goldenId !== '—' ? `/api/v1/cards/${encodeURIComponent(goldenId)}` : ''],
            ['Золотая · dbf', goldenDbf, goldenDbf !== '—' ? `/api/v1/cards/by-dbf/${encodeURIComponent(goldenDbf)}` : ''],
        ].filter(([, , url]) => Boolean(url)).map(([label, value, url]) => ({ label, value, url }));

        return {
            id,
            dbf,
            name: text(dataset.recordName, 'Без названия'),
            englishName: text(dataset.recordEnglishName),
            image,
            images,
            identifiers: {
                internalId: text(dataset.recordInternalId),
                cardId: id,
                dbf,
                goldenCardId: goldenId,
                goldenDbf,
            },
            apiLinks,
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
        };
    };

    return { recordFromDataset, shouldSignalNavigation };
});
