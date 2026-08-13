(() => {
    const dashboard = document.querySelector('[data-analytics-dashboard]');
    if (!dashboard) return;

    const endpoint = dashboard.dataset.analyticsEndpoint || '/analytics.php';
    const moduleButtons = Array.from(dashboard.querySelectorAll('[data-analytics-module]'));
    const controls = dashboard.querySelector('[data-analytics-controls]');
    const searchInput = dashboard.querySelector('[data-analytics-search]');
    const queryControl = dashboard.querySelector('.analytics-query-control');
    const searchLabel = dashboard.querySelector('[data-analytics-search-label]');
    const formatControl = dashboard.querySelector('[data-analytics-format-control]');
    const rankControl = dashboard.querySelector('[data-analytics-rank-control]');
    const periodControl = dashboard.querySelector('[data-analytics-period-control]');
    const modeControl = dashboard.querySelector('[data-analytics-mode-control]');
    const ratingControl = dashboard.querySelector('[data-analytics-rating-control]');
    const arenaSourceControl = dashboard.querySelector('[data-analytics-arena-source-control]');
    const cardRankControl = dashboard.querySelector('[data-analytics-card-rank-control]');
    const cardPeriodControl = dashboard.querySelector('[data-analytics-card-period-control]');
    const refreshButton = dashboard.querySelector('[data-analytics-refresh]');
    const applyButton = controls?.querySelector('button[type="submit"]');
    const cardForm = dashboard.querySelector('[data-card-statistics-form]');
    const cardInput = dashboard.querySelector('[data-card-statistics-input]');
    const content = dashboard.querySelector('[data-analytics-content]');
    const title = dashboard.querySelector('[data-analytics-title]');
    const description = dashboard.querySelector('[data-analytics-description]');
    const summary = dashboard.querySelector('[data-analytics-summary]');
    const tableHost = dashboard.querySelector('[data-analytics-table]');
    const meta = dashboard.querySelector('[data-analytics-meta]');
    const status = dashboard.querySelector('[data-analytics-status]');
    const detailDrawer = dashboard.querySelector('[data-analytics-detail-drawer]');
    const detailBackdrop = dashboard.querySelector('[data-analytics-detail-backdrop]');
    const detailClose = dashboard.querySelector('[data-analytics-detail-close]');
    const detailKind = dashboard.querySelector('[data-analytics-detail-kind]');
    const detailTitle = dashboard.querySelector('[data-analytics-detail-title]');
    const detailDescription = dashboard.querySelector('[data-analytics-detail-description]');
    const detailBody = dashboard.querySelector('[data-analytics-detail-body]');
    const reliabilityHost = dashboard.querySelector('[data-parsing-reliability]');
    const urlState = new URLSearchParams(window.location.search);
    const knownModules = new Set(moduleButtons.map((button) => button.dataset.analyticsModule));
    knownModules.add('card');

    const searchableModules = new Set(['archetypes', 'hsguru_archetypes', 'constructed_cards', 'arena_cards', 'decks', 'bg_heroes', 'bg_minions']);
    const searchLabels = {
        archetypes: 'Архетип или класс',
        hsguru_archetypes: 'Архетип HSGuru',
        constructed_cards: 'Карта Standard / Wild',
        arena_cards: 'Карта Арены или класс',
        decks: 'Название колоды или класс',
        bg_heroes: 'Герой',
        bg_minions: 'Существо',
    };
    const cache = new Map();
    let requestController = null;
    let activePayload = null;
    let lastDetailTrigger = null;
    let imageErrorCount = 0;
    let reliabilityWindow = null;
    const brokenImageUrls = new Set();
    let metaBaseText = '';
    let activeModule = knownModules.has(urlState.get('stats'))
        ? urlState.get('stats')
        : (dashboard.dataset.defaultModule || 'overview');

    const numberFormatter = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2});
    const integerFormatter = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 0});
    const dateFormatter = new Intl.DateTimeFormat('ru-RU', {
        dateStyle: 'medium',
        timeStyle: 'short',
        timeZone: 'UTC',
    });

    const setBusy = (busy) => {
        content?.setAttribute('aria-busy', busy ? 'true' : 'false');
        refreshButton?.toggleAttribute('disabled', busy);
    };

    const renderLoading = () => {
        setBusy(true);
        if (reliabilityHost) {
            const showReliability = activeModule === 'overview';
            reliabilityHost.hidden = !showReliability;
            reliabilityHost.setAttribute('aria-busy', showReliability ? 'true' : 'false');
            if (showReliability) {
                const skeleton = document.createElement('div');
                skeleton.className = 'parsing-reliability-skeleton analytics-skeleton';
                skeleton.setAttribute('aria-label', 'Загрузка надёжности парсинга');
                reliabilityHost.replaceChildren(skeleton);
            } else {
                reliabilityHost.replaceChildren();
            }
        }
        if (status) status.textContent = 'Загрузка статистики…';
        if (title) title.textContent = 'Загрузка…';
        if (description) description.textContent = 'Получаем актуальный набор из локального API.';
        if (summary) {
            summary.replaceChildren(...Array.from({length: 4}, () => {
                const item = document.createElement('span');
                item.className = 'analytics-summary-item analytics-skeleton';
                item.setAttribute('aria-hidden', 'true');
                return item;
            }));
        }
        if (tableHost) {
            const skeleton = document.createElement('div');
            skeleton.className = 'analytics-table-skeleton analytics-skeleton';
            skeleton.setAttribute('aria-hidden', 'true');
            tableHost.replaceChildren(skeleton);
        }
        if (meta) meta.textContent = '';
        metaBaseText = '';
        imageErrorCount = 0;
        brokenImageUrls.clear();
    };

    const statusTone = (value) => {
        const normalized = String(value || '').toLocaleLowerCase('ru-RU');
        if (['ok', 'matched', 'актуальный набор', 's', 'есть'].includes(normalized)) return 'good';
        if (normalized === 'нет') return 'neutral';
        if (normalized.includes('cached') || normalized.includes('stale') || normalized.includes('последний') || normalized.includes('устар')) return 'warning';
        if (normalized.includes('error') || normalized.includes('fail') || normalized.includes('проблем')) return 'bad';
        return 'neutral';
    };

    const imagePlaceholder = (label = 'Изображение недоступно') => {
        const placeholder = document.createElement('span');
        placeholder.className = 'analytics-image-placeholder';
        placeholder.setAttribute('role', 'img');
        placeholder.setAttribute('aria-label', label);
        placeholder.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m4 16 4.5-5 3.5 4 2.5-3 5.5 6H4Z"/><circle cx="9" cy="7" r="2"/><path d="M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z"/></svg>';
        return placeholder;
    };

    const updateImageHealth = () => {
        if (!meta) return;
        const suffix = imageErrorCount > 0 ? ` · битых изображений: ${imageErrorCount}` : '';
        meta.textContent = `${metaBaseText}${suffix}`;
        meta.classList.toggle('has-image-errors', imageErrorCount > 0);
    };

    const formatCell = (value, type, row = {}) => {
        if (value === null || value === undefined || value === '') {
            const empty = document.createElement('span');
            empty.className = 'analytics-empty-value';
            empty.textContent = '—';
            return empty;
        }

        if (type === 'link') {
            const link = document.createElement('a');
            try {
                const parsed = new URL(String(value));
                if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('Unsupported protocol');
                link.href = parsed.href;
                link.target = '_blank';
                link.rel = 'noopener';
                link.textContent = 'Открыть ↗';
            } catch (error) {
                link.removeAttribute('href');
                link.textContent = String(value);
            }
            return link;
        }

        if (type === 'image') {
            const button = document.createElement('button');
            button.className = 'analytics-image-button';
            button.type = 'button';
            const entityName = String(row.hero || row.archetype || row.name_ru || row.name || row.card_id || 'сущности');
            if (row.image_kind === 'hero_portrait' || activeModule === 'bg_heroes') button.classList.add('is-hero-portrait');
            try {
                const parsed = new URL(String(value), window.location.origin);
                if (!['http:', 'https:'].includes(parsed.protocol)) throw new Error('Unsupported protocol');
                button.dataset.preview = parsed.href;
                button.dataset.tooltip = `Открыть изображение: ${entityName}`;
                button.setAttribute('aria-label', `Открыть изображение ${entityName} на весь экран`);
                const image = document.createElement('img');
                image.src = parsed.href;
                image.alt = entityName;
                image.loading = 'lazy';
                image.decoding = 'async';
                image.addEventListener('error', () => {
                    brokenImageUrls.add(parsed.href);
                    imageErrorCount = brokenImageUrls.size;
                    button.classList.add('is-broken');
                    button.disabled = true;
                    button.removeAttribute('data-preview');
                    button.removeAttribute('data-tooltip');
                    button.replaceChildren(imagePlaceholder(`Изображение ${entityName} недоступно`));
                    updateImageHealth();
                }, {once: true});
                button.append(image);
            } catch (error) {
                brokenImageUrls.add(String(value));
                imageErrorCount = brokenImageUrls.size;
                button.disabled = true;
                button.classList.add('is-broken');
                button.append(imagePlaceholder(`Изображение ${entityName} недоступно`));
                updateImageHealth();
            }
            return button;
        }

        if (type === 'code') {
            const code = document.createElement('code');
            code.textContent = String(value);
            code.title = String(value);
            return code;
        }

        if (type === 'status') {
            const badge = document.createElement('span');
            badge.className = `analytics-state is-${statusTone(value)}`;
            badge.textContent = String(value);
            return badge;
        }

        const span = document.createElement('span');
        if (type === 'percent') {
            span.textContent = typeof value === 'number'
                ? `${numberFormatter.format(value)}%`
                : (String(value).includes('%') ? String(value) : `${value}%`);
            span.className = 'analytics-number';
            return span;
        }
        if (type === 'number') {
            span.textContent = typeof value === 'number' ? numberFormatter.format(value) : String(value);
            span.className = 'analytics-number';
            return span;
        }
        if (type === 'date') {
            const parsed = new Date(String(value));
            span.textContent = Number.isNaN(parsed.getTime()) ? String(value) : dateFormatter.format(parsed);
            span.className = 'analytics-date';
            return span;
        }
        span.textContent = String(value);
        return span;
    };

    const renderSummary = (items) => {
        if (!summary) return;
        const nodes = (items || []).map((item) => {
            const node = document.createElement('span');
            node.className = `analytics-summary-item is-${item.tone || 'neutral'}`;
            const label = document.createElement('small');
            label.textContent = item.label || '';
            const value = document.createElement('b');
            value.textContent = typeof item.value === 'number' ? integerFormatter.format(item.value) : String(item.value ?? '—');
            node.append(label, value);
            return node;
        });
        summary.replaceChildren(...nodes);
    };

    const reliabilityWindowLabels = {
        '24h': '24 часа',
        '7d': '7 дней',
        '30d': '30 дней',
    };

    const renderParsingReliability = (reliability, selectedWindow = reliabilityWindow) => {
        if (!reliabilityHost) return;
        if (activeModule !== 'overview') {
            reliabilityHost.hidden = true;
            reliabilityHost.replaceChildren();
            return;
        }

        const viewBuilder = window.ParsingReliabilityUI?.buildReliabilityViewModel;
        const model = typeof viewBuilder === 'function'
            ? viewBuilder(reliability || {state: 'collecting', windows: []}, selectedWindow)
            : {
                hasWindow: false,
                windows: [],
                badge: 'Накапливаем статистику',
                observed: false,
                preliminary: false,
                ratesAvailable: false,
                fullFresh: '—',
                availability: '—',
                acceptedFresh: '—',
                coverage: '—',
                counts: {},
            };
        reliabilityWindow = model.selectedWindow || selectedWindow;
        reliabilityHost.hidden = false;
        reliabilityHost.setAttribute('aria-busy', 'false');

        const header = document.createElement('header');
        header.className = 'parsing-reliability-head';
        const headingGroup = document.createElement('div');
        const eyebrow = document.createElement('span');
        eyebrow.className = 'eyebrow';
        eyebrow.textContent = 'Наблюдаемость парсера';
        const heading = document.createElement('h3');
        heading.textContent = 'Успешность получения новых данных';
        const explanation = document.createElement('p');
        explanation.textContent = 'Главный процент считает только новые валидные публикации. Резерв LKG не улучшает его и показан отдельно; до завершения журнала расписаний срез помечен как предварительный.';
        headingGroup.append(eyebrow, heading, explanation);
        const badge = document.createElement('span');
        const badgeState = model.observed ? 'is-observed' : (model.preliminary ? 'is-preliminary' : 'is-collecting');
        badge.className = `parsing-reliability-badge ${badgeState}`;
        badge.textContent = model.badge;
        header.append(headingGroup, badge);

        const windowNav = document.createElement('nav');
        windowNav.className = 'parsing-reliability-windows';
        windowNav.setAttribute('aria-label', 'Период измерения надёжности парсинга');
        (model.windows || []).forEach((windowKey) => {
            const button = document.createElement('button');
            const active = windowKey === model.selectedWindow;
            button.type = 'button';
            button.className = active ? 'active' : '';
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
            button.textContent = reliabilityWindowLabels[windowKey] || windowKey;
            button.addEventListener('click', () => renderParsingReliability(reliability, windowKey));
            windowNav.append(button);
        });

        if (!model.hasWindow) {
            const empty = document.createElement('div');
            empty.className = 'parsing-reliability-empty';
            empty.setAttribute('role', 'status');
            const emptyTitle = document.createElement('strong');
            emptyTitle.textContent = 'Накапливаем статистику';
            const emptyCopy = document.createElement('p');
            emptyCopy.textContent = 'Сервис метрик пока не вернул измерительное окно. Проценты появятся только после достаточного покрытия — текущее состояние не считается 100%.';
            empty.append(emptyTitle, emptyCopy);
            reliabilityHost.replaceChildren(header, empty);
            return;
        }

        const rateCard = (label, value, description, className = '') => {
            const card = document.createElement('article');
            card.className = `parsing-reliability-rate ${className}`.trim();
            const cardLabel = document.createElement('span');
            cardLabel.textContent = label;
            const cardValue = document.createElement('strong');
            cardValue.textContent = value;
            const cardCopy = document.createElement('p');
            cardCopy.textContent = description;
            card.append(cardLabel, cardValue, cardCopy);
            return card;
        };
        const pendingCopy = 'Появится после первой согласованной выборки.';
        const freshCopy = model.preliminary
            ? 'Предварительно по зафиксированным попыткам; provisional и LKG не входят.'
            : 'Новая полная публикация без provisional и LKG.';
        const rates = document.createElement('div');
        rates.className = 'parsing-reliability-rates';
        rates.append(
            rateCard(
                'Новые данные · fresh-only',
                model.fullFresh,
                model.ratesAvailable ? freshCopy : pendingCopy,
                'is-primary'
            ),
            rateCard(
                'Доступность данных · с LKG',
                model.availability,
                model.ratesAvailable ? 'Fresh + provisional + последний успешный набор LKG.' : pendingCopy,
                'is-availability'
            ),
            rateCard(
                'Принятая свежесть',
                model.acceptedFresh,
                model.ratesAvailable ? 'Fresh + provisional, без резервного LKG.' : pendingCopy
            )
        );

        const countCard = (label, value, detail) => {
            const card = document.createElement('div');
            card.className = 'parsing-reliability-count';
            const cardLabel = document.createElement('span');
            cardLabel.textContent = label;
            const cardValue = document.createElement('strong');
            cardValue.textContent = value === null || value === undefined ? '—' : integerFormatter.format(value);
            const cardDetail = document.createElement('small');
            cardDetail.textContent = detail;
            card.append(cardLabel, cardValue, cardDetail);
            return card;
        };
        const counts = document.createElement('div');
        counts.className = 'parsing-reliability-counts';
        counts.append(
            countCard('Provisional', model.counts.provisional, 'приняты условно'),
            countCard('LKG', model.counts.lkg, 'отдан последний успешный набор'),
            countCard('Ошибки', model.counts.failed, 'без результата'),
            countCard('Таймауты', model.counts.timedOut, 'завершены по лимиту времени')
        );

        const foot = document.createElement('p');
        foot.className = 'parsing-reliability-meta';
        const metaParts = [
            model.preliminary ? 'статус: предварительный' : null,
            `покрытие окна: ${model.coverage}`,
            `eligible попыток: ${model.eligibleAttempts ?? '—'}`,
            `всего исходов: ${model.totalAttempts ?? '—'}`,
            `skipped: ${model.counts.skipped ?? '—'}`,
        ].filter(Boolean);
        if (model.generatedAt) {
            const generated = new Date(model.generatedAt);
            if (!Number.isNaN(generated.getTime())) metaParts.push(`срез: ${dateFormatter.format(generated)}`);
        }
        foot.textContent = metaParts.join(' · ');
        reliabilityHost.replaceChildren(header, windowNav, rates, counts, foot);
    };

    const detailLabels = {
        archetype: 'Архетип', hero: 'Герой', minion: 'Существо', name: 'Название', name_ru: 'Название RU', title: 'Название',
        class: 'Класс', class_name: 'Класс', cardClass: 'Класс', format: 'Формат', format_id: 'ID формата', period: 'Период', rank: 'Рейтинг',
        games: 'Игры', games_with_minion: 'Игры с существом', games_without_minion: 'Игры без существа',
        winrate: 'Винрейт', win_rate: 'Винрейт', popularity: 'Популярность', popularity_pct: 'Доля меты',
        avg_turns: 'Среднее число ходов', avg_duration_minutes: 'Средняя длительность, мин',
        climbing_speed_stars_per_hour: 'Скорость подъёма, звёзд/ч', deck_count: 'Сборки', deck_code: 'Код колоды', score: 'Размер выборки',
        has_decks: 'Есть сборки', has_decks_label: 'Статус сборок', top_deck_win_rate: 'Лучший винрейт сборки',
        sample_rank: 'Ранг выборки', sample_period: 'Период выборки',
        avg_placement: 'Среднее место', avg_placement_with: 'Среднее место с существом',
        avg_placement_without: 'Среднее место без существа', first_place: 'Первое место', pick_rate_value: 'Выбор',
        pick_rate: 'Выбор', drawn_winrate: 'Винрейт при доборе', mulligan_winrate: 'Винрейт после муллигана', times_played: 'Разыграно',
        combat_winrate: 'Винрейт боя', combat_winrate_value: 'Винрейт боя', impact: 'Влияние',
        win_share: 'Доля побед', popularity_value: 'Популярность', combat_round: 'Раунд', wins: 'Победы', losses: 'Поражения',
        tavern_tier: 'Уровень таверны', techLevel: 'Уровень таверны', cost: 'Стоимость', tier: 'Тир', type: 'Тип', rarity: 'Редкость',
        isBattlegroundsPoolMinion: 'В пуле существ BG', isBattlegroundsPoolSpell: 'В пуле заклинаний BG',
        card_id: 'CARD_ID', id: 'CARD_ID', dbfId: 'DBF ID', dbf_id: 'DBF ID', minion_dbf_id: 'DBF ID существа', source_id: 'Источник', source: 'Источник',
        fetched_at: 'Последнее обновление', updated_at: 'Обновлено', state: 'Состояние', site: 'Сайт', cache: 'Режим данных',
        category: 'Категория', description: 'Описание', dataset: 'Набор', age: 'Свежесть',
        source_url: 'Страница источника', archetype_url: 'Страница архетипа', decks_url: 'Все сборки', url: 'Ссылка',
        combat_rounds: 'Статистика по раундам', decks: 'Сборки колод', placementDistribution: 'Распределение мест',
    };

    const detailLabel = (key) => {
        if (detailLabels[key]) return detailLabels[key];
        const normalized = String(key).replace(/_/g, ' ');
        return normalized.charAt(0).toLocaleUpperCase('ru-RU') + normalized.slice(1);
    };

    const detailValueNode = (key, value) => {
        if (/(?:^|_)(?:url|link)$|_url$/i.test(key)) return formatCell(value, 'link');
        if (/date|_at$|fetched/i.test(key)) return formatCell(value, 'date');
        if (/rate|winrate|popularity|percentage|share|first_place/i.test(key)) return formatCell(value, 'percent');
        if (key === 'deck_code') {
            const wrapper = document.createElement('span');
            wrapper.className = 'analytics-copy-value';
            wrapper.append(formatCell(value, 'code'));
            const copy = document.createElement('button');
            copy.type = 'button';
            copy.textContent = 'Копировать';
            copy.addEventListener('click', async () => {
                const showCopied = () => {
                    copy.textContent = 'Скопировано';
                    window.setTimeout(() => { copy.textContent = 'Копировать'; }, 1600);
                };
                const fallbackCopy = () => {
                    const area = document.createElement('textarea');
                    area.value = String(value);
                    area.setAttribute('readonly', '');
                    area.style.position = 'fixed';
                    area.style.opacity = '0';
                    document.body.append(area);
                    area.select();
                    const copied = document.execCommand('copy');
                    area.remove();
                    if (!copied) throw new Error('Copy failed');
                    showCopied();
                };
                try {
                    if (navigator.clipboard?.writeText) {
                        await navigator.clipboard.writeText(String(value));
                        showCopied();
                    } else {
                        fallbackCopy();
                    }
                } catch (error) {
                    try {
                        fallbackCopy();
                    } catch (fallbackError) {
                        copy.textContent = 'Не удалось';
                    }
                }
            });
            wrapper.append(copy);
            return wrapper;
        }
        if (/deck_code|card_id|dbf|source_id|^id$/i.test(key)) return formatCell(value, 'code');
        if (typeof value === 'number') return formatCell(value, 'number');
        if (typeof value === 'boolean') return formatCell(value ? 'Да' : 'Нет', 'status');
        return formatCell(value, 'text');
    };

    const detailSection = (headingText) => {
        const section = document.createElement('section');
        section.className = 'analytics-detail-section';
        const heading = document.createElement('h3');
        heading.textContent = headingText;
        section.append(heading);
        return section;
    };

    const renderNestedDetail = (key, value) => {
        const section = detailSection(detailLabel(key));
        if (Array.isArray(value) && value.length && value.every((item) => item && typeof item === 'object' && !Array.isArray(item))) {
            const rows = value;
            const keys = Array.from(rows.reduce((set, item) => {
                Object.keys(item).forEach((itemKey) => {
                    if (item[itemKey] === null || ['object', 'undefined'].includes(typeof item[itemKey])) return;
                    set.add(itemKey);
                });
                return set;
            }, new Set()));
            const scroll = document.createElement('div');
            scroll.className = 'analytics-detail-table-scroll';
            scroll.tabIndex = 0;
            const table = document.createElement('table');
            table.className = 'analytics-detail-table';
            const head = document.createElement('thead');
            const headRow = document.createElement('tr');
            keys.forEach((itemKey) => {
                const cell = document.createElement('th');
                cell.scope = 'col';
                cell.textContent = detailLabel(itemKey);
                headRow.append(cell);
            });
            head.append(headRow);
            const body = document.createElement('tbody');
            rows.forEach((item) => {
                const row = document.createElement('tr');
                keys.forEach((itemKey) => {
                    const cell = document.createElement('td');
                    cell.append(detailValueNode(itemKey, item[itemKey]));
                    row.append(cell);
                });
                body.append(row);
            });
            table.append(head, body);
            scroll.append(table);
            section.append(scroll);
            return section;
        }

        if (Array.isArray(value)) {
            const list = document.createElement('div');
            list.className = 'analytics-detail-tags';
            value.forEach((item) => {
                const tag = document.createElement('span');
                tag.textContent = typeof item === 'object' ? JSON.stringify(item) : String(item);
                list.append(tag);
            });
            section.append(list);
            return section;
        }

        const pre = document.createElement('pre');
        pre.textContent = JSON.stringify(value, null, 2);
        section.append(pre);
        return section;
    };

    const closeDetail = () => {
        if (!detailDrawer || detailDrawer.hidden) return;
        detailDrawer.hidden = true;
        if (detailBackdrop) detailBackdrop.hidden = true;
        document.body.classList.remove('analytics-detail-open');
        const trigger = lastDetailTrigger;
        lastDetailTrigger = null;
        if (trigger instanceof HTMLElement && document.contains(trigger)) trigger.focus();
    };

    const openDetail = (row, trigger) => {
        if (!detailDrawer || !detailBody) return;
        lastDetailTrigger = trigger instanceof HTMLElement ? trigger : null;
        const entityTitle = row.archetype || row.hero || row.minion || row.name_ru || row.name || row.source || row.card_id || row.id || 'Подробные данные';
        if (detailKind) detailKind.textContent = activePayload?.title || 'Подробные данные';
        if (detailTitle) detailTitle.textContent = String(entityTitle);
        const descriptionParts = [activePayload?.meta?.source_id ? `Источник: ${activePayload.meta.source_id}` : '', activePayload?.meta?.updated_at ? `обновлено ${formatCell(activePayload.meta.updated_at, 'date').textContent}` : ''].filter(Boolean);
        if (detailDescription) detailDescription.textContent = descriptionParts.join(' · ');

        const fragment = document.createDocumentFragment();
        if (row.image_url) {
            const visual = document.createElement('div');
            visual.className = `analytics-detail-visual${row.image_kind === 'hero_portrait' ? ' is-hero-portrait' : ''}`;
            visual.append(formatCell(row.image_url, 'image', row));
            fragment.append(visual);
        }

        const scalarEntries = Object.entries(row).filter(([key, value]) => (
            !['image_url', 'image_kind', 'age_tone'].includes(key)
            && value !== null && value !== '' && ['string', 'number', 'boolean'].includes(typeof value)
        ));
        if (scalarEntries.length) {
            const metrics = detailSection('Все показатели');
            const grid = document.createElement('dl');
            grid.className = 'analytics-detail-metrics';
            scalarEntries.forEach(([key, value]) => {
                const item = document.createElement('div');
                const term = document.createElement('dt');
                term.textContent = detailLabel(key);
                const definition = document.createElement('dd');
                definition.append(detailValueNode(key, value));
                item.append(term, definition);
                grid.append(item);
            });
            metrics.append(grid);
            fragment.append(metrics);
        }

        Object.entries(row).forEach(([key, value]) => {
            if (value === null || value === '' || ['image_url', 'image_kind'].includes(key)) return;
            if (typeof value === 'object') fragment.append(renderNestedDetail(key, value));
        });

        const rawDetails = document.createElement('details');
        rawDetails.className = 'analytics-detail-raw';
        const rawSummary = document.createElement('summary');
        rawSummary.textContent = 'Все исходные поля JSON';
        const raw = document.createElement('pre');
        raw.textContent = JSON.stringify(row, null, 2);
        rawDetails.append(rawSummary, raw);
        fragment.append(rawDetails);

        detailBody.replaceChildren(fragment);
        detailDrawer.hidden = false;
        if (detailBackdrop) detailBackdrop.hidden = false;
        document.body.classList.add('analytics-detail-open');
        detailDrawer.focus();
    };

    detailClose?.addEventListener('click', closeDetail);
    detailBackdrop?.addEventListener('click', closeDetail);
    document.addEventListener('keydown', (event) => {
        if (!detailDrawer || detailDrawer.hidden) return;
        const fullscreenPreview = document.getElementById('fullscreenCard');
        if (fullscreenPreview && !fullscreenPreview.hidden) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            closeDetail();
            return;
        }
        if (event.key !== 'Tab') return;
        const focusable = Array.from(detailDrawer.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), details > summary, [tabindex]:not([tabindex="-1"])'))
            .filter((node) => node instanceof HTMLElement && !node.hidden && node.offsetParent !== null);
        if (!focusable.length) {
            event.preventDefault();
            detailDrawer.focus();
            return;
        }
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    }, true);

    const renderTable = (columns, rows, warnings = []) => {
        if (!tableHost) return;
        imageErrorCount = 0;
        brokenImageUrls.clear();
        const fragment = document.createDocumentFragment();

        if (warnings.length) {
            const warning = document.createElement('div');
            warning.className = 'analytics-message is-warning';
            warning.setAttribute('role', 'status');
            warning.textContent = `Часть источников недоступна: ${warnings.join(' ')}`;
            fragment.append(warning);
        }

        if (!rows.length) {
            const empty = document.createElement('div');
            empty.className = 'analytics-empty';
            const emptyTitle = document.createElement('h3');
            emptyTitle.textContent = activeModule === 'card' ? 'Статистика карты пока не найдена' : 'Нет данных по выбранным фильтрам';
            const emptyText = document.createElement('p');
            emptyText.textContent = activeModule === 'card'
                ? 'Проверьте английское название или выберите другую карту из каталога.'
                : 'Измените поисковый запрос или параметры среза.';
            empty.append(emptyTitle, emptyText);
            fragment.append(empty);
            tableHost.replaceChildren(fragment);
            return;
        }

        const wrapper = document.createElement('div');
        wrapper.className = 'analytics-table-scroll';
        wrapper.tabIndex = 0;
        wrapper.setAttribute('role', 'region');
        wrapper.setAttribute('aria-label', 'Таблица статистики');
        const table = document.createElement('table');
        table.className = 'analytics-table';
        table.dataset.module = activeModule;
        const head = document.createElement('thead');
        const headRow = document.createElement('tr');
        columns.forEach((column) => {
            const cell = document.createElement('th');
            cell.dataset.column = column.key;
            cell.scope = 'col';
            cell.textContent = column.label || column.key;
            headRow.append(cell);
        });
        const actionHead = document.createElement('th');
        actionHead.className = 'analytics-row-action';
        actionHead.scope = 'col';
        actionHead.textContent = 'Данные';
        headRow.append(actionHead);
        head.append(headRow);
        const body = document.createElement('tbody');
        rows.forEach((row) => {
            const tableRow = document.createElement('tr');
            if (activeModule === 'overview') {
                const effectiveState = String(row.state || '').toLocaleLowerCase('ru-RU');
                if (effectiveState !== 'ok') tableRow.classList.add('is-problem');
                else if (['warning', 'bad'].includes(row.age_tone)) tableRow.classList.add('is-stale');
            }
            columns.forEach((column) => {
                const cell = document.createElement('td');
                cell.dataset.column = column.key;
                cell.append(formatCell(row[column.key], column.type || 'text', row));
                tableRow.append(cell);
            });
            const actionCell = document.createElement('td');
            actionCell.className = 'analytics-row-action';
            const detailButton = document.createElement('button');
            detailButton.type = 'button';
            detailButton.className = 'analytics-detail-button';
            detailButton.textContent = 'Подробнее';
            detailButton.addEventListener('click', () => openDetail(row, detailButton));
            actionCell.append(detailButton);
            tableRow.append(actionCell);
            body.append(tableRow);
        });
        table.append(head, body);
        wrapper.append(table);
        fragment.append(wrapper);
        tableHost.replaceChildren(fragment);
    };

    const renderMeta = (payload) => {
        if (!meta) return;
        const parts = [];
        if (payload.meta?.updated_at) parts.push(`срез ${formatCell(payload.meta.updated_at, 'date').textContent}`);
        if (payload.meta?.source_id) parts.push(`источник: ${payload.meta.source_id}`);
        if (payload.meta?.stale) parts.push('данные помечены как устаревшие');
        if (payload.meta?.stale_cache) parts.push('показан резервный кэш');
        else if (payload.meta?.cached) parts.push('локальный кэш');
        metaBaseText = parts.join(' · ');
        updateImageHealth();
        meta.classList.toggle('is-warning', Boolean(payload.meta?.stale || payload.meta?.stale_cache));
    };

    const renderError = (message) => {
        setBusy(false);
        if (reliabilityHost) {
            reliabilityHost.hidden = true;
            reliabilityHost.replaceChildren();
        }
        if (status) status.textContent = 'Ошибка загрузки статистики.';
        if (title) title.textContent = 'Статистика недоступна';
        if (description) description.textContent = message;
        if (summary) summary.replaceChildren();
        if (meta) meta.textContent = '';
        metaBaseText = '';
        if (!tableHost) return;
        const error = document.createElement('div');
        error.className = 'analytics-empty is-error';
        error.setAttribute('role', 'alert');
        const heading = document.createElement('h3');
        heading.textContent = 'Не удалось получить данные';
        const copy = document.createElement('p');
        copy.textContent = message;
        const retry = document.createElement('button');
        retry.className = 'button secondary';
        retry.type = 'button';
        retry.textContent = 'Повторить';
        retry.addEventListener('click', () => loadModule(activeModule, true));
        error.append(heading, copy, retry);
        tableHost.replaceChildren(error);
    };

    const updateControls = () => {
        moduleButtons.forEach((button) => {
            const active = button.dataset.analyticsModule === activeModule;
            button.classList.toggle('active', active);
            button.setAttribute('aria-pressed', active ? 'true' : 'false');
        });
        const searchable = searchableModules.has(activeModule);
        const hasFilters = searchable || ['meta', 'hsguru_archetypes', 'constructed_cards', 'arena_cards', 'bg_heroes'].includes(activeModule);
        controls?.classList.toggle('is-refresh-only', !hasFilters);
        searchInput?.toggleAttribute('disabled', !searchable);
        queryControl?.toggleAttribute('hidden', !searchable);
        applyButton?.toggleAttribute('hidden', !hasFilters);
        if (searchInput) {
            searchInput.placeholder = searchable ? `Поиск: ${searchLabels[activeModule].toLocaleLowerCase('ru-RU')}` : 'Поиск недоступен для раздела';
        }
        if (searchLabel) searchLabel.textContent = searchable ? searchLabels[activeModule] : 'Поиск по разделу';
        formatControl?.toggleAttribute('hidden', !['meta', 'hsguru_archetypes', 'constructed_cards'].includes(activeModule));
        rankControl?.toggleAttribute('hidden', activeModule !== 'meta');
        periodControl?.toggleAttribute('hidden', activeModule !== 'meta');
        modeControl?.toggleAttribute('hidden', activeModule !== 'bg_heroes');
        ratingControl?.toggleAttribute('hidden', activeModule !== 'bg_heroes');
        arenaSourceControl?.toggleAttribute('hidden', activeModule !== 'arena_cards');
        cardRankControl?.toggleAttribute('hidden', activeModule !== 'constructed_cards');
        cardPeriodControl?.toggleAttribute('hidden', activeModule !== 'constructed_cards');
    };

    const requestUrl = (module) => {
        const request = new URL(endpoint, window.location.origin);
        request.searchParams.set('module', module);
        const query = searchInput?.value.trim() || '';
        if (searchableModules.has(module) && query) request.searchParams.set('q', query);
        if (module === 'card') request.searchParams.set('card_name', cardInput?.value.trim() || query);
        if (module === 'meta') {
            request.searchParams.set('format', dashboard.querySelector('[data-analytics-format]')?.value || 'standard');
            request.searchParams.set('rank', dashboard.querySelector('[data-analytics-rank]')?.value || 'legend');
            request.searchParams.set('period', dashboard.querySelector('[data-analytics-period]')?.value || 'past_day');
            request.searchParams.set('min_games', dashboard.querySelector('[data-analytics-min-games]')?.value || '100');
        }
        if (module === 'hsguru_archetypes') {
            request.searchParams.set('format', dashboard.querySelector('[data-analytics-format]')?.value || 'standard');
            request.searchParams.set('min_games', '50');
            request.searchParams.set('limit', '300');
        }
        if (module === 'constructed_cards') {
            request.searchParams.set('format', dashboard.querySelector('[data-analytics-format]')?.value || 'standard');
            request.searchParams.set('card_rank', dashboard.querySelector('[data-analytics-card-rank]')?.value || 'legend');
            request.searchParams.set('card_period', dashboard.querySelector('[data-analytics-card-period]')?.value || '7d');
            request.searchParams.set('limit', '200');
        }
        if (module === 'arena_cards') {
            request.searchParams.set('arena_source', dashboard.querySelector('[data-analytics-arena-source]')?.value || 'firestone');
            request.searchParams.set('limit', '200');
        }
        if (module === 'bg_heroes') {
            request.searchParams.set('mode', dashboard.querySelector('[data-analytics-mode]')?.value || 'solo');
            request.searchParams.set('rating', dashboard.querySelector('[data-analytics-rating]')?.value || '50');
        }
        return request;
    };

    const updateUrlState = (module) => {
        const next = new URL(window.location.href);
        next.searchParams.set('stats', module);
        const query = module === 'card' ? cardInput?.value.trim() : searchInput?.value.trim();
        if (query) next.searchParams.set('stats_q', query);
        else next.searchParams.delete('stats_q');
        if (['meta', 'hsguru_archetypes', 'constructed_cards'].includes(module)) {
            next.searchParams.set('stats_format', dashboard.querySelector('[data-analytics-format]')?.value || 'standard');
            if (module === 'meta') {
                next.searchParams.set('stats_rank', dashboard.querySelector('[data-analytics-rank]')?.value || 'legend');
                next.searchParams.set('stats_period', dashboard.querySelector('[data-analytics-period]')?.value || 'past_day');
            }
        } else {
            next.searchParams.delete('stats_format');
            next.searchParams.delete('stats_rank');
            next.searchParams.delete('stats_period');
        }
        if (module === 'bg_heroes') next.searchParams.set('stats_rating', dashboard.querySelector('[data-analytics-rating]')?.value || '50');
        else next.searchParams.delete('stats_rating');
        if (module === 'arena_cards') next.searchParams.set('stats_arena_source', dashboard.querySelector('[data-analytics-arena-source]')?.value || 'firestone');
        else next.searchParams.delete('stats_arena_source');
        if (module === 'constructed_cards') {
            next.searchParams.set('stats_card_rank', dashboard.querySelector('[data-analytics-card-rank]')?.value || 'legend');
            next.searchParams.set('stats_card_period', dashboard.querySelector('[data-analytics-card-period]')?.value || '7d');
        } else {
            next.searchParams.delete('stats_card_rank');
            next.searchParams.delete('stats_card_period');
        }
        window.history.replaceState({}, '', next);
    };

    const loadModule = async (module, force = false) => {
        if (!knownModules.has(module)) module = 'overview';
        activeModule = module;
        updateControls();
        const request = requestUrl(module);
        updateUrlState(module);
        const cacheKey = request.href;
        renderLoading();
        requestController?.abort();
        requestController = new AbortController();

        try {
            let payload = !force ? cache.get(cacheKey) : null;
            if (!payload) {
                const response = await fetch(request, {
                    headers: {'Accept': 'application/json'},
                    credentials: 'same-origin',
                    cache: force ? 'no-store' : 'default',
                    signal: requestController.signal,
                });
                payload = await response.json();
                if (!response.ok || !payload.ok) {
                    throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
                }
                cache.set(cacheKey, payload);
            }
            activePayload = payload;
            if (title) title.textContent = payload.title || 'Статистика';
            if (description) description.textContent = payload.description || '';
            renderSummary(payload.summary || []);
            renderParsingReliability(payload.parsing_reliability);
            renderTable(payload.columns || [], payload.rows || [], payload.warnings || []);
            renderMeta(payload);
            if (status) status.textContent = `Данные загружены. Записей: ${(payload.rows || []).length}.`;
            setBusy(false);
        } catch (error) {
            if (error.name === 'AbortError') return;
            renderError(error.message || 'Неизвестная ошибка.');
        }
    };

    moduleButtons.forEach((button) => {
        button.addEventListener('click', () => loadModule(button.dataset.analyticsModule || 'overview'));
    });
    controls?.addEventListener('submit', (event) => {
        event.preventDefault();
        loadModule(activeModule, true);
    });
    controls?.querySelectorAll('select').forEach((select) => {
        select.addEventListener('change', () => loadModule(activeModule, true));
    });
    refreshButton?.addEventListener('click', () => loadModule(activeModule, true));
    cardForm?.addEventListener('submit', (event) => {
        event.preventDefault();
        const value = cardInput?.value.trim() || '';
        if (!value) {
            cardInput?.focus();
            return;
        }
        activeModule = 'card';
        loadModule('card', true);
    });

    const initialQuery = urlState.get('stats_q') || '';
    if (activeModule === 'card' && cardInput) cardInput.value = initialQuery;
    else if (searchInput) searchInput.value = initialQuery;
    const initialFormat = urlState.get('stats_format');
    const initialRank = urlState.get('stats_rank');
    const initialPeriod = urlState.get('stats_period');
    const formatInput = dashboard.querySelector('[data-analytics-format]');
    const rankInput = dashboard.querySelector('[data-analytics-rank]');
    const periodInput = dashboard.querySelector('[data-analytics-period]');
    const ratingInput = dashboard.querySelector('[data-analytics-rating]');
    const arenaSourceInput = dashboard.querySelector('[data-analytics-arena-source]');
    const cardRankInput = dashboard.querySelector('[data-analytics-card-rank]');
    const cardPeriodInput = dashboard.querySelector('[data-analytics-card-period]');
    if (formatInput && initialFormat && Array.from(formatInput.options).some((option) => option.value === initialFormat)) formatInput.value = initialFormat;
    if (rankInput && initialRank && Array.from(rankInput.options).some((option) => option.value === initialRank)) rankInput.value = initialRank;
    if (periodInput && initialPeriod) periodInput.value = initialPeriod;
    const setKnownOption = (select, value) => {
        if (select && value && Array.from(select.options).some((option) => option.value === value)) select.value = value;
    };
    setKnownOption(ratingInput, urlState.get('stats_rating'));
    setKnownOption(arenaSourceInput, urlState.get('stats_arena_source'));
    setKnownOption(cardRankInput, urlState.get('stats_card_rank'));
    setKnownOption(cardPeriodInput, urlState.get('stats_card_period'));
    loadModule(activeModule);
})();
