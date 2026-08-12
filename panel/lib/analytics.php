<?php
declare(strict_types=1);

/**
 * Read-only integration with the local Hearthstone Data API.
 *
 * New statistics modules belong in analytics_module_registry() and the
 * corresponding normalization branch. Remote paths and query parameters are
 * deliberately allowlisted so this file cannot become a generic HTTP proxy.
 */

function analytics_module_registry(): array
{
    return [
        'overview' => [
            'title' => 'Все источники данных',
            'description' => 'Полный реестр источников: состояние, назначение и время последнего успешного обновления.',
            'path' => '/demo/overview',
            'ttl' => 30,
            'params' => [],
        ],
        'meta' => [
            'title' => 'Мета Standard / Wild',
            'description' => 'Архетипы HSGuru: винрейт, популярность, число игр и скорость подъёма.',
            'path' => '/v1/hsguru/meta',
            'ttl' => 120,
            'params' => [
                'format' => ['type' => 'enum', 'values' => ['standard', 'wild'], 'default' => 'standard'],
                'rank' => ['type' => 'enum', 'values' => ['all', 'diamond', 'diamond_4to1', 'diamond_to_legend', 'legend', 'top_5k', 'top_legend', 'top_500', 'top_100'], 'default' => 'legend'],
                'period' => ['type' => 'token', 'default' => 'past_day', 'max' => 64],
                'min_games' => ['type' => 'int', 'default' => 100, 'min' => 0, 'max' => 1000000],
            ],
        ],
        'archetypes' => [
            'title' => 'Архетипы HSReplay',
            'description' => 'Архетипы рейтинговой игры с долей меты и общей статистикой.',
            'path' => '/v1/constructed/archetypes',
            'ttl' => 120,
            'params' => [
                'q' => ['type' => 'string', 'max' => 120],
                'class_name' => ['type' => 'string', 'max' => 80],
                'rank_range' => ['type' => 'token', 'default' => 'LEGEND', 'max' => 80],
                'game_type' => ['type' => 'token', 'default' => 'RANKED_STANDARD', 'max' => 80],
                'limit' => ['type' => 'int', 'default' => 100, 'min' => 1, 'max' => 200],
                'offset' => ['type' => 'int', 'default' => 0, 'min' => 0, 'max' => 10000],
            ],
        ],
        'hsguru_archetypes' => [
            'title' => 'Архетипы HSGuru',
            'description' => 'Полный каталог актуальных архетипов Standard и Wild со статистикой и доступными сборками.',
            'path' => '/v1/hsguru/archetypes',
            'ttl' => 120,
            'params' => [
                'q' => ['type' => 'string', 'max' => 120],
                'format' => ['type' => 'enum', 'values' => ['standard', 'wild'], 'default' => 'standard'],
                'min_games' => ['type' => 'int', 'default' => 50, 'min' => 50, 'max' => 1000000],
                'sort' => ['type' => 'enum', 'values' => ['games', 'winrate', 'popularity', 'name'], 'default' => 'games'],
                'order' => ['type' => 'enum', 'values' => ['asc', 'desc'], 'default' => 'desc'],
                'limit' => ['type' => 'int', 'default' => 300, 'min' => 1, 'max' => 500],
                'offset' => ['type' => 'int', 'default' => 0, 'min' => 0, 'max' => 10000],
            ],
        ],
        'decks' => [
            'title' => 'Колоды',
            'description' => 'Собранные колоды из всех доступных источников.',
            'path' => '/v1/constructed/decks',
            'ttl' => 120,
            'params' => [
                'q' => ['type' => 'string', 'max' => 120],
                'class_name' => ['type' => 'string', 'max' => 80],
                'format_name' => ['type' => 'string', 'max' => 80],
                'source_id' => ['type' => 'token', 'max' => 120],
                'limit' => ['type' => 'int', 'default' => 50, 'min' => 1, 'max' => 100],
                'offset' => ['type' => 'int', 'default' => 0, 'min' => 0, 'max' => 10000],
            ],
        ],
        'bg_heroes' => [
            'title' => 'Герои по рейтингу',
            'description' => 'Герои Battlegrounds по MMR-срезам: выбор, среднее место, первые места и размер выборки.',
            'composite' => true,
            'ttl' => 120,
            'params' => [
                'q' => ['type' => 'string', 'max' => 120],
                'mode' => ['type' => 'enum', 'values' => ['solo', 'duos'], 'default' => 'solo'],
                'rating' => ['type' => 'enum', 'values' => ['100', '50', '25', '10', '1'], 'default' => '50'],
                'limit' => ['type' => 'int', 'default' => 200, 'min' => 1, 'max' => 200],
                'offset' => ['type' => 'int', 'default' => 0, 'min' => 0, 'max' => 10000],
            ],
        ],
        'bg_minions' => [
            'title' => 'Существа Battlegrounds',
            'description' => 'Подробная статистика существ: популярность, влияние, винрейт боя, средняя позиция и показатели по раундам.',
            'composite' => true,
            'ttl' => 120,
            'params' => [
                'q' => ['type' => 'string', 'max' => 120],
                'tavern_tier' => ['type' => 'int', 'min' => 1, 'max' => 7],
                'limit' => ['type' => 'int', 'default' => 500, 'min' => 1, 'max' => 500],
                'offset' => ['type' => 'int', 'default' => 0, 'min' => 0, 'max' => 10000],
            ],
        ],
        'arena' => [
            'title' => 'Арена',
            'description' => 'Винрейт, частота выбора и число драфтов по классам.',
            'path' => '/v1/arena/classes',
            'ttl' => 120,
            'params' => [
                'limit' => ['type' => 'int', 'default' => 20, 'min' => 1, 'max' => 100],
                'offset' => ['type' => 'int', 'default' => 0, 'min' => 0, 'max' => 10000],
            ],
        ],
        'arena_cards' => [
            'title' => 'Карты Арены',
            'description' => 'Карты обычной и Подпольной Арены: тир, винрейт, выбор и размер выборки.',
            'composite' => true,
            'ttl' => 120,
            'params' => [
                'q' => ['type' => 'string', 'max' => 120],
                'arena_source' => ['type' => 'enum', 'values' => ['hsreplay', 'firestone', 'underground'], 'default' => 'firestone'],
                'limit' => ['type' => 'int', 'default' => 200, 'min' => 1, 'max' => 500],
            ],
        ],
        'constructed_cards' => [
            'title' => 'Карты Standard / Wild',
            'description' => 'Статистика карт по формату, рейтингу и периоду: винрейт, популярность, муллиган и игры.',
            'composite' => true,
            'ttl' => 120,
            'params' => [
                'q' => ['type' => 'string', 'max' => 120],
                'format' => ['type' => 'enum', 'values' => ['standard', 'wild'], 'default' => 'standard'],
                'card_rank' => ['type' => 'enum', 'values' => ['platinum', 'diamond', 'diamond_4_1', 'legend'], 'default' => 'legend'],
                'card_period' => ['type' => 'enum', 'values' => ['1d', '3d', '7d', '14d', 'patch'], 'default' => '7d'],
                'limit' => ['type' => 'int', 'default' => 200, 'min' => 1, 'max' => 500],
            ],
        ],
        'patches' => [
            'title' => 'Патчи Hearthstone',
            'description' => 'Сопоставление официальных патчей и публикаций HS-Manacost.',
            'path' => '/api/patches',
            'ttl' => 300,
            'params' => [
                'limit' => ['type' => 'int', 'default' => 50, 'min' => 1, 'max' => 100],
                'offset' => ['type' => 'int', 'default' => 0, 'min' => 0, 'max' => 10000],
            ],
        ],
        'card' => [
            'title' => 'Статистика карты',
            'description' => 'Все найденные показатели карты в рейтинге и Battlegrounds.',
            'composite' => true,
            'ttl' => 120,
            'params' => [
                'card_name' => ['type' => 'string', 'required' => true, 'max' => 120],
            ],
        ],
    ];
}

function analytics_safe_query(array $definition, array $input): array
{
    $query = [];
    foreach ($definition['params'] as $name => $rule) {
        $hasValue = array_key_exists($name, $input) && !is_array($input[$name]);
        $raw = $hasValue ? trim((string)$input[$name]) : '';

        if ($raw === '' && array_key_exists('default', $rule)) {
            $raw = (string)$rule['default'];
        }
        if ($raw === '') {
            if (!empty($rule['required'])) {
                throw new InvalidArgumentException('Укажите название карты.');
            }
            continue;
        }

        $type = $rule['type'] ?? 'string';
        if ($type === 'int') {
            if (!preg_match('/^-?\d+$/', $raw)) {
                $raw = array_key_exists('default', $rule) ? (string)$rule['default'] : '';
            }
            if ($raw === '') {
                continue;
            }
            $value = (int)$raw;
            $value = max((int)($rule['min'] ?? PHP_INT_MIN), $value);
            $value = min((int)($rule['max'] ?? PHP_INT_MAX), $value);
            $query[$name] = $value;
            continue;
        }

        if ($type === 'enum') {
            $values = $rule['values'] ?? [];
            if (!in_array($raw, $values, true)) {
                $raw = (string)($rule['default'] ?? '');
            }
            if ($raw !== '') {
                $query[$name] = $raw;
            }
            continue;
        }

        $maxLength = (int)($rule['max'] ?? 120);
        $value = mb_substr($raw, 0, $maxLength, 'UTF-8');
        if ($type === 'token' && !preg_match('/^[A-Za-z0-9_.:-]+$/', $value)) {
            $value = (string)($rule['default'] ?? '');
        }
        if ($value !== '') {
            $query[$name] = $value;
        }
    }

    return $query;
}

function analytics_cache_directory(): string
{
    $processOwner = @fileowner('/proc/self');
    $runtimeOwner = function_exists('posix_geteuid')
        ? (string)posix_geteuid()
        : ($processOwner !== false
            ? (string)$processOwner
            : (function_exists('getmyuid') ? (string)getmyuid() : preg_replace('/[^a-z0-9_-]+/i', '-', PHP_SAPI)));
    $directory = rtrim(sys_get_temp_dir(), DIRECTORY_SEPARATOR) . DIRECTORY_SEPARATOR . 'db-kolodahs-analytics-cache-' . $runtimeOwner;
    if (!is_dir($directory) && !mkdir($directory, 0700, true) && !is_dir($directory)) {
        throw new RuntimeException('Не удалось создать кэш статистики.');
    }

    return $directory;
}

function analytics_decode_json(string $body): array
{
    $payload = json_decode($body, true, 512, JSON_THROW_ON_ERROR);
    if (!is_array($payload)) {
        throw new RuntimeException('API вернул неожиданный формат данных.');
    }

    return $payload;
}

function analytics_fetch_json(string $path, array $query, int $ttl): array
{
    $baseUrl = rtrim((string)(getenv('HS_DATA_API_INTERNAL_URL') ?: 'http://127.0.0.1:18081'), '/');
    $url = $baseUrl . $path . ($query ? '?' . http_build_query($query, '', '&', PHP_QUERY_RFC3986) : '');
    $cacheFile = analytics_cache_directory() . DIRECTORY_SEPARATOR . hash('sha256', $url) . '.json';
    $cachedBody = is_file($cacheFile) ? file_get_contents($cacheFile) : false;
    $cacheAge = is_file($cacheFile) ? max(0, time() - (int)filemtime($cacheFile)) : null;

    if (is_string($cachedBody) && $cacheAge !== null && $cacheAge <= $ttl) {
        return [
            'payload' => analytics_decode_json($cachedBody),
            'cached' => true,
            'stale_cache' => false,
            'cache_age' => $cacheAge,
        ];
    }

    $handle = curl_init($url);
    if ($handle === false) {
        throw new RuntimeException('Не удалось инициализировать запрос к API.');
    }
    curl_setopt_array($handle, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 2,
        CURLOPT_TIMEOUT => 10,
        CURLOPT_HTTPHEADER => ['Accept: application/json'],
        CURLOPT_USERAGENT => 'api.kolodahearthstone.com-analytics/1.0',
    ]);
    $body = curl_exec($handle);
    $status = (int)curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
    $error = curl_error($handle);
    curl_close($handle);

    if (!is_string($body) || $status < 200 || $status >= 300) {
        if (is_string($cachedBody)) {
            return [
                'payload' => analytics_decode_json($cachedBody),
                'cached' => true,
                'stale_cache' => true,
                'cache_age' => $cacheAge,
            ];
        }
        $safeReason = $status > 0 ? 'HTTP ' . $status : ($error !== '' ? $error : 'нет ответа');
        throw new RuntimeException('Сервис статистики недоступен: ' . $safeReason . '.');
    }

    $payload = analytics_decode_json($body);
    $temporary = $cacheFile . '.' . bin2hex(random_bytes(4)) . '.tmp';
    if (file_put_contents($temporary, $body, LOCK_EX) !== false) {
        chmod($temporary, 0600);
        rename($temporary, $cacheFile);
    }

    return [
        'payload' => $payload,
        'cached' => false,
        'stale_cache' => false,
        'cache_age' => 0,
    ];
}

function analytics_column(string $key, string $label, string $type = 'text'): array
{
    return ['key' => $key, 'label' => $label, 'type' => $type];
}

function analytics_meta(array $payload): array
{
    return is_array($payload['meta'] ?? null) ? $payload['meta'] : [];
}

function analytics_reliability_percentage($value): ?float
{
    if (!is_numeric($value)) {
        return null;
    }
    $number = (float)$value;
    if (!is_finite($number) || $number < 0 || $number > 100) {
        return null;
    }
    return round($number, 2);
}

function analytics_reliability_count($value): int
{
    if (!is_numeric($value)) {
        return 0;
    }
    return max(0, (int)$value);
}

function analytics_normalize_parsing_reliability(
    ?array $envelope,
    bool $cached = false,
    bool $staleCache = false
): array {
    $collecting = [
        'state' => 'collecting',
        'message' => 'Накапливаем статистику',
        'default_window' => '7d',
        'generated_at' => null,
        'coverage_started_at' => null,
        'methodology' => null,
        'cached' => $cached,
        'stale_cache' => $staleCache,
        'windows' => [],
    ];
    if ($envelope === null || !is_array($envelope['data'] ?? null)) {
        return $collecting;
    }

    $data = $envelope['data'];
    $methodology = is_array($data['methodology'] ?? null) ? $data['methodology'] : null;
    $rawWindows = is_array($data['windows'] ?? null) ? $data['windows'] : null;
    $limitations = is_array($methodology['limitations'] ?? null)
        ? array_values(array_filter($methodology['limitations'], 'is_string'))
        : [];
    $eligibleOutcomes = is_array($methodology['eligible_outcomes'] ?? null)
        ? array_values(array_filter($methodology['eligible_outcomes'], 'is_string'))
        : [];
    $excludedOutcomes = is_array($methodology['excluded_outcomes'] ?? null)
        ? array_values(array_filter($methodology['excluded_outcomes'], 'is_string'))
        : [];
    $requiredEligibleOutcomes = ['fresh_published', 'provisional', 'lkg_served', 'failed', 'timed_out'];
    if (
        $methodology === null
        || $rawWindows === null
        || trim((string)($methodology['version'] ?? '')) === ''
        || ($methodology['scope'] ?? '') !== 'generic_refresh_sources'
        || ($methodology['completeness'] ?? '') !== 'observed_attempts_only'
        || !in_array('dedicated_pipeline_sources_excluded', $limitations, true)
        || !in_array('best_effort_write_gaps_not_detectable', $limitations, true)
        || array_diff($requiredEligibleOutcomes, $eligibleOutcomes) !== []
        || !in_array('skipped', $excludedOutcomes, true)
    ) {
        return $collecting;
    }

    $allowedWindows = ['24h', '7d', '30d'];
    $countKeys = ['fresh_published', 'provisional', 'lkg_served', 'failed', 'timed_out', 'skipped'];
    $windows = [];
    $seen = [];
    foreach ($rawWindows as $rawWindow) {
        if (!is_array($rawWindow)) {
            continue;
        }
        $window = (string)($rawWindow['window'] ?? '');
        if (!in_array($window, $allowedWindows, true) || isset($seen[$window])) {
            continue;
        }
        $seen[$window] = true;

        $counts = [];
        $rawCounts = is_array($rawWindow['counts'] ?? null) ? $rawWindow['counts'] : [];
        foreach ($countKeys as $key) {
            $counts[$key] = analytics_reliability_count($rawCounts[$key] ?? 0);
        }
        $totalAttempts = analytics_reliability_count($rawWindow['total_attempts'] ?? 0);
        $eligibleAttempts = analytics_reliability_count($rawWindow['eligible_attempts'] ?? 0);
        $countedTotal = array_sum($counts);
        $countedEligible = $countedTotal - $counts['skipped'];
        $countsConsistent = $totalAttempts === $countedTotal && $eligibleAttempts === $countedEligible;

        $coverageRatio = is_numeric($rawWindow['coverage_ratio'] ?? null)
            ? (float)$rawWindow['coverage_ratio']
            : 0.0;
        if (!is_finite($coverageRatio)) {
            $coverageRatio = 0.0;
        }
        $coverageRatio = round(max(0.0, min(1.0, $coverageRatio)), 4);
        $measurementStatus = ($rawWindow['measurement_status'] ?? '') === 'observed'
            ? 'observed'
            : 'collecting';
        $fullFresh = analytics_reliability_percentage($rawWindow['full_fresh_rate_pct'] ?? null);
        $acceptedFresh = analytics_reliability_percentage($rawWindow['accepted_fresh_rate_pct'] ?? null);
        $dataAvailable = analytics_reliability_percentage($rawWindow['data_available_rate_pct'] ?? null);
        $ratesObserved = $measurementStatus === 'observed'
            && $eligibleAttempts > 0
            && $countsConsistent
            && $fullFresh !== null
            && $acceptedFresh !== null
            && $dataAvailable !== null;

        $windows[] = [
            'window' => $window,
            'from_at' => isset($rawWindow['from_at']) ? (string)$rawWindow['from_at'] : null,
            'to_at' => isset($rawWindow['to_at']) ? (string)$rawWindow['to_at'] : null,
            'measurement_status' => $measurementStatus,
            'coverage_ratio' => $coverageRatio,
            'total_attempts' => $totalAttempts,
            'eligible_attempts' => $eligibleAttempts,
            'counts' => $counts,
            'full_fresh_rate_pct' => $fullFresh,
            'accepted_fresh_rate_pct' => $acceptedFresh,
            'data_available_rate_pct' => $dataAvailable,
            'rates_observed' => $ratesObserved,
        ];
    }

    if ($windows === []) {
        return $collecting;
    }

    $availableWindowKeys = array_column($windows, 'window');
    return [
        'state' => 'available',
        'message' => null,
        'default_window' => in_array('7d', $availableWindowKeys, true) ? '7d' : $availableWindowKeys[0],
        'generated_at' => isset($data['generated_at']) ? (string)$data['generated_at'] : null,
        'coverage_started_at' => isset($data['coverage_started_at']) ? (string)$data['coverage_started_at'] : null,
        'methodology' => [
            'version' => (string)$methodology['version'],
            'unit' => (string)($methodology['unit'] ?? ''),
            'scope' => (string)$methodology['scope'],
            'completeness' => (string)$methodology['completeness'],
            'limitations' => $limitations,
            'eligible_outcomes' => $eligibleOutcomes,
            'excluded_outcomes' => $excludedOutcomes,
        ],
        'cached' => $cached,
        'stale_cache' => $staleCache,
        'windows' => $windows,
    ];
}

function analytics_attach_parsing_reliability(array $overview): array
{
    try {
        $fetch = analytics_fetch_json('/v1/system/parsing-reliability', [], 60);
        $overview['parsing_reliability'] = analytics_normalize_parsing_reliability(
            $fetch['payload'],
            (bool)$fetch['cached'],
            (bool)$fetch['stale_cache']
        );
    } catch (Throwable $ignored) {
        $overview['parsing_reliability'] = analytics_normalize_parsing_reliability(null);
    }
    return $overview;
}

function analytics_result_shell(string $module, array $definition, array $fetch, array $summary, array $columns, array $rows, array $extra = []): array
{
    $remoteMeta = analytics_meta($fetch['payload']);
    return array_merge([
        'ok' => true,
        'module' => $module,
        'title' => $definition['title'],
        'description' => $definition['description'],
        'summary' => $summary,
        'columns' => $columns,
        'rows' => $rows,
        'meta' => [
            'total' => $remoteMeta['count'] ?? count($rows),
            'updated_at' => $remoteMeta['fetched_at'] ?? null,
            'source_id' => $remoteMeta['source_id'] ?? null,
            'stale' => (bool)($remoteMeta['stale'] ?? false),
            'cached' => (bool)$fetch['cached'],
            'stale_cache' => (bool)$fetch['stale_cache'],
            'cache_age' => $fetch['cache_age'],
        ],
    ], $extra);
}

function analytics_source_age_label(?string $timestamp): array
{
    if ($timestamp === null || $timestamp === '') {
        return ['label' => 'Нет отметки времени', 'seconds' => null, 'tone' => 'bad'];
    }
    $parsed = strtotime($timestamp);
    if ($parsed === false) {
        return ['label' => 'Неизвестно', 'seconds' => null, 'tone' => 'warning'];
    }
    $seconds = max(0, time() - $parsed);
    if ($seconds < 3600) {
        $minutes = max(1, (int)floor($seconds / 60));
        return ['label' => $minutes . ' мин назад', 'seconds' => $seconds, 'tone' => 'good'];
    }
    if ($seconds < 86400) {
        return ['label' => (int)floor($seconds / 3600) . ' ч назад', 'seconds' => $seconds, 'tone' => 'good'];
    }
    $days = (int)floor($seconds / 86400);
    return ['label' => $days . ' дн назад', 'seconds' => $seconds, 'tone' => $days >= 3 ? 'warning' : 'neutral'];
}

function analytics_normalize(string $module, array $definition, array $fetch): array
{
    $payload = $fetch['payload'];

    if ($module === 'overview') {
        $sources = is_array($payload['sources'] ?? null) ? $payload['sources'] : [];
        $rows = [];
        $staleCount = 0;
        foreach ($sources as $source) {
            $status = is_array($source['status'] ?? null) ? $source['status'] : [];
            $cached = !empty($status['serving_cached_dataset']);
            $fetchedAt = isset($source['fetched_at']) ? (string)$source['fetched_at'] : null;
            $age = analytics_source_age_label($fetchedAt);
            $staleCount += ($age['seconds'] === null || $age['seconds'] >= 259200) ? 1 : 0;
            $rows[] = [
                'source' => $source['source_id'] ?? '',
                'site' => $source['site'] ?? '',
                'category' => $source['category'] ?? '',
                'description' => $source['description'] ?? '',
                'state' => $status['effective_state'] ?? $source['state'] ?? 'unknown',
                'fetched_at' => $fetchedAt,
                'age' => in_array($age['tone'], ['warning', 'bad'], true)
                    ? 'Устарело · ' . $age['label']
                    : 'Актуально · ' . $age['label'],
                'age_tone' => $age['tone'],
                'dataset' => !empty($source['has_dataset']) ? 'Опубликован' : 'Нет набора',
                'cache' => $cached ? 'Последний успешный набор' : 'Актуальный набор',
            ];
        }
        usort($rows, static function (array $left, array $right): int {
            $leftOk = strtolower((string)($left['state'] ?? '')) === 'ok';
            $rightOk = strtolower((string)($right['state'] ?? '')) === 'ok';
            if ($leftOk !== $rightOk) {
                return $leftOk ? 1 : -1;
            }
            $leftTime = strtotime((string)($left['fetched_at'] ?? '')) ?: 0;
            $rightTime = strtotime((string)($right['fetched_at'] ?? '')) ?: 0;
            return $leftTime <=> $rightTime;
        });
        $total = (int)($payload['total'] ?? count($sources));
        $ok = (int)($payload['ok_count'] ?? 0);
        return analytics_result_shell($module, $definition, $fetch, [
            ['label' => 'Источников', 'value' => $total],
            ['label' => 'Работают', 'value' => $ok, 'tone' => $ok === $total ? 'good' : 'warning'],
            ['label' => 'Проблемных', 'value' => max(0, $total - $ok), 'tone' => $ok === $total ? 'good' : 'bad'],
            ['label' => 'Устарели 3+ дня', 'value' => $staleCount, 'tone' => $staleCount > 0 ? 'warning' : 'good'],
        ], [
            analytics_column('source', 'Источник', 'code'),
            analytics_column('site', 'Сайт'),
            analytics_column('category', 'Категория'),
            analytics_column('description', 'Что загружается'),
            analytics_column('state', 'Состояние', 'status'),
            analytics_column('fetched_at', 'Обновлено', 'date'),
            analytics_column('age', 'Свежесть', 'status'),
        ], $rows);
    }

    if ($module === 'meta') {
        $data = is_array($payload['data'] ?? null) ? $payload['data'] : [];
        $rows = is_array($data['items'] ?? null) ? $data['items'] : [];
        return analytics_result_shell($module, $definition, $fetch, [
            ['label' => 'Архетипов', 'value' => count($rows)],
            ['label' => 'Формат', 'value' => strtoupper((string)($data['format'] ?? ''))],
            ['label' => 'Ранг', 'value' => (string)($data['rank'] ?? '')],
            ['label' => 'Период', 'value' => (string)($data['period'] ?? '')],
        ], [
            analytics_column('archetype', 'Архетип'),
            analytics_column('winrate', 'Винрейт', 'percent'),
            analytics_column('popularity', 'Популярность', 'percent'),
            analytics_column('games', 'Игры', 'number'),
            analytics_column('turns', 'Ходы', 'number'),
            analytics_column('duration_minutes', 'Минуты', 'number'),
            analytics_column('climbing_speed', 'Скорость подъёма', 'number'),
        ], $rows);
    }

    if ($module === 'archetypes') {
        $rows = is_array($payload['data'] ?? null) ? $payload['data'] : [];
        return analytics_result_shell($module, $definition, $fetch, [
            ['label' => 'Найдено', 'value' => count($rows)],
            ['label' => 'Всего', 'value' => analytics_meta($payload)['count'] ?? count($rows)],
        ], [
            analytics_column('name', 'Архетип'),
            analytics_column('class_name', 'Класс'),
            analytics_column('win_rate', 'Винрейт', 'percent'),
            analytics_column('pct_of_total', 'Доля меты', 'percent'),
            analytics_column('pct_of_class', 'Доля класса', 'percent'),
            analytics_column('total_games', 'Игры', 'number'),
            analytics_column('as_of_popularity', 'Срез', 'date'),
            analytics_column('url', 'Источник', 'link'),
        ], $rows);
    }

    if ($module === 'hsguru_archetypes') {
        $rows = is_array($payload['data'] ?? null) ? $payload['data'] : [];
        foreach ($rows as &$row) {
            $decks = is_array($row['decks'] ?? null) ? $row['decks'] : [];
            $row['class_name'] = $decks[0]['class'] ?? '';
            $row['has_decks_label'] = !empty($row['has_decks']) ? 'Есть' : 'Нет';
            $row['top_deck_win_rate'] = $decks[0]['win_rate'] ?? null;
        }
        unset($row);
        return analytics_result_shell($module, $definition, $fetch, [
            ['label' => 'Архетипов', 'value' => count($rows)],
            ['label' => 'Со сборками', 'value' => count(array_filter($rows, static fn(array $row): bool => !empty($row['has_decks'])))],
            ['label' => 'Формат', 'value' => strtoupper((string)($rows[0]['format'] ?? ''))],
        ], [
            analytics_column('archetype', 'Архетип'),
            analytics_column('class_name', 'Класс'),
            analytics_column('winrate', 'Винрейт', 'percent'),
            analytics_column('popularity_pct', 'Доля меты', 'percent'),
            analytics_column('games', 'Игры', 'number'),
            analytics_column('deck_count', 'Сборки', 'number'),
            analytics_column('has_decks_label', 'Колоды', 'status'),
            analytics_column('top_deck_win_rate', 'Лучшая сборка', 'percent'),
            analytics_column('archetype_url', 'HSGuru', 'link'),
        ], $rows);
    }

    if ($module === 'decks') {
        $rows = is_array($payload['data'] ?? null) ? $payload['data'] : [];
        return analytics_result_shell($module, $definition, $fetch, [
            ['label' => 'Показано', 'value' => count($rows)],
            ['label' => 'Всего', 'value' => analytics_meta($payload)['count'] ?? count($rows)],
        ], [
            analytics_column('archetype', 'Колода'),
            analytics_column('class', 'Класс'),
            analytics_column('source_id', 'Источник', 'code'),
            analytics_column('win_rate', 'Винрейт', 'percent'),
            analytics_column('games', 'Игры', 'number'),
            analytics_column('updated_at', 'Обновлено', 'date'),
            analytics_column('url', 'Ссылка', 'link'),
            analytics_column('deck_code', 'Код колоды', 'code'),
        ], $rows);
    }

    if ($module === 'bg_heroes') {
        $rows = is_array($payload['data'] ?? null) ? $payload['data'] : [];
        foreach ($rows as &$row) {
            $row['best_composition_name'] = $row['best_composition']['name'] ?? '';
        }
        unset($row);
        return analytics_result_shell($module, $definition, $fetch, [
            ['label' => 'Героев', 'value' => count($rows)],
            ['label' => 'Всего', 'value' => analytics_meta($payload)['count'] ?? count($rows)],
        ], [
            analytics_column('hero', 'Герой'),
            analytics_column('tier', 'Тир', 'status'),
            analytics_column('pick_rate_value', 'Выбор', 'percent'),
            analytics_column('avg_placement', 'Среднее место', 'number'),
            analytics_column('best_composition_name', 'Лучший состав'),
            analytics_column('dbfId', 'dbfId', 'code'),
        ], $rows);
    }

    if ($module === 'bg_minions') {
        $rows = is_array($payload['data'] ?? null) ? $payload['data'] : [];
        return analytics_result_shell($module, $definition, $fetch, [
            ['label' => 'Существ', 'value' => count($rows)],
            ['label' => 'Всего', 'value' => analytics_meta($payload)['count'] ?? count($rows)],
        ], [
            analytics_column('name_ru', 'Карта'),
            analytics_column('name', 'Card EN'),
            analytics_column('tavern_tier', 'Таверна', 'number'),
            analytics_column('popularity', 'Популярность', 'percent'),
            analytics_column('combat_winrate', 'Винрейт боя', 'percent'),
            analytics_column('impact', 'Влияние', 'number'),
            analytics_column('avg_placement_with', 'Среднее место', 'number'),
            analytics_column('games_with_minion', 'Игры', 'number'),
            analytics_column('card_id', 'card_id', 'code'),
            analytics_column('dbf_id', 'dbfId', 'code'),
        ], $rows);
    }

    if ($module === 'arena') {
        $rows = is_array($payload['data'] ?? null) ? $payload['data'] : [];
        return analytics_result_shell($module, $definition, $fetch, [
            ['label' => 'Классов', 'value' => count($rows)],
        ], [
            analytics_column('class_ru', 'Класс'),
            analytics_column('win_rate', 'Винрейт', 'percent'),
            analytics_column('pick_rate', 'Выбор', 'percent'),
            analytics_column('pct_7_plus', '7+ побед', 'percent'),
            analytics_column('num_drafts', 'Драфты', 'number'),
            analytics_column('url', 'Источник', 'link'),
        ], $rows);
    }

    if ($module === 'patches') {
        $rows = is_array($payload['patches'] ?? null) ? $payload['patches'] : [];
        $fetch['payload']['meta'] = [
            'count' => $payload['total'] ?? count($rows),
            'fetched_at' => $rows[0]['fetched_at'] ?? null,
            'source_id' => 'hs_manacost_patches',
            'stale' => false,
        ];
        return analytics_result_shell($module, $definition, $fetch, [
            ['label' => 'Показано', 'value' => count($rows)],
            ['label' => 'Всего', 'value' => $payload['total'] ?? count($rows)],
            ['label' => 'Сопоставлено', 'value' => $payload['matched_total'] ?? 0],
        ], [
            analytics_column('version', 'Версия', 'code'),
            analytics_column('title', 'Публикация'),
            analytics_column('published_at', 'Опубликовано', 'date'),
            analytics_column('match_state', 'Сопоставление', 'status'),
            analytics_column('source_url', 'Статья', 'link'),
            analytics_column('wiki_url', 'Wiki', 'link'),
        ], $rows);
    }

    throw new RuntimeException('Неизвестный формат модуля статистики.');
}

function analytics_fetch_absolute_json(string $url, int $ttl): array
{
    $parts = parse_url($url);
    if (($parts['scheme'] ?? '') !== 'https' || ($parts['host'] ?? '') !== 'static.zerotoheroes.com') {
        throw new InvalidArgumentException('Внешний источник статистики не разрешён.');
    }
    $cacheFile = analytics_cache_directory() . DIRECTORY_SEPARATOR . 'remote-' . hash('sha256', $url) . '.json';
    $cachedBody = is_file($cacheFile) ? file_get_contents($cacheFile) : false;
    $cacheAge = is_file($cacheFile) ? max(0, time() - (int)filemtime($cacheFile)) : null;
    if (is_string($cachedBody) && $cacheAge !== null && $cacheAge <= $ttl) {
        return ['payload' => analytics_decode_json($cachedBody), 'cached' => true, 'stale_cache' => false, 'cache_age' => $cacheAge];
    }

    $handle = curl_init($url);
    if ($handle === false) {
        throw new RuntimeException('Не удалось инициализировать запрос к источнику.');
    }
    curl_setopt_array($handle, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 3,
        CURLOPT_TIMEOUT => 12,
        CURLOPT_ENCODING => '',
        CURLOPT_HTTPHEADER => ['Accept: application/json'],
        CURLOPT_USERAGENT => 'api.kolodahearthstone.com-analytics/1.0',
    ]);
    $body = curl_exec($handle);
    $status = (int)curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
    $error = curl_error($handle);
    curl_close($handle);
    if (!is_string($body) || $status < 200 || $status >= 300) {
        if (is_string($cachedBody)) {
            return ['payload' => analytics_decode_json($cachedBody), 'cached' => true, 'stale_cache' => true, 'cache_age' => $cacheAge];
        }
        $reason = $status > 0 ? 'HTTP ' . $status : ($error !== '' ? $error : 'нет ответа');
        throw new RuntimeException('Источник статистики недоступен: ' . $reason . '.');
    }
    $payload = analytics_decode_json($body);
    $temporary = $cacheFile . '.' . bin2hex(random_bytes(4)) . '.tmp';
    if (file_put_contents($temporary, $body, LOCK_EX) !== false) {
        chmod($temporary, 0600);
        rename($temporary, $cacheFile);
    }
    return ['payload' => $payload, 'cached' => false, 'stale_cache' => false, 'cache_age' => 0];
}

function analytics_fetch_local_dataset(string $sourceId): array
{
    if (!preg_match('/^hsreplay_cards_(?:wild_)?(?:platinum|diamond|diamond_4_1|legend)_(?:1d|3d|7d|14d|patch)$/', $sourceId)) {
        throw new InvalidArgumentException('Неизвестный срез статистики карт.');
    }
    $directory = '/srv/hs-data-api/data/datasets';
    $path = realpath($directory . DIRECTORY_SEPARATOR . $sourceId . '.json');
    $root = realpath($directory);
    if ($path === false || $root === false || strpos($path, $root . DIRECTORY_SEPARATOR) !== 0 || !is_readable($path)) {
        throw new RuntimeException('Срез статистики карт пока не опубликован.');
    }
    $body = file_get_contents($path);
    if (!is_string($body)) {
        throw new RuntimeException('Не удалось прочитать срез статистики карт.');
    }
    return [
        'payload' => analytics_decode_json($body),
        'cached' => false,
        'stale_cache' => false,
        'cache_age' => max(0, time() - (int)filemtime($path)),
    ];
}

function analytics_filter_rows(array $rows, string $query, int $limit): array
{
    if ($query !== '') {
        $needle = mb_strtolower($query, 'UTF-8');
        $rows = array_values(array_filter($rows, static function (array $row) use ($needle): bool {
            $haystack = mb_strtolower(implode(' ', array_filter([
                $row['name'] ?? '',
                $row['name_ru'] ?? '',
                $row['hero'] ?? '',
                $row['archetype'] ?? '',
                $row['id'] ?? '',
                $row['card_id'] ?? '',
                $row['cardClass'] ?? '',
                $row['class_name'] ?? '',
            ], static fn($value): bool => is_scalar($value))), 'UTF-8');
            return mb_strpos($haystack, $needle, 0, 'UTF-8') !== false;
        }));
    }
    return array_slice($rows, 0, max(1, $limit));
}

function analytics_constructed_cards(array $definition, array $query): array
{
    $format = (string)($query['format'] ?? 'standard');
    $rank = (string)($query['card_rank'] ?? 'legend');
    $period = (string)($query['card_period'] ?? '7d');
    $sourceId = 'hsreplay_cards_' . ($format === 'wild' ? 'wild_' : '') . $rank . '_' . $period;
    $fetch = analytics_fetch_local_dataset($sourceId);
    $structured = is_array($fetch['payload']['data']['structured'] ?? null) ? $fetch['payload']['data']['structured'] : [];
    $rows = is_array($structured['cards'] ?? null) ? $structured['cards'] : [];
    foreach ($rows as &$row) {
        $cardId = (string)($row['id'] ?? $row['card_id'] ?? '');
        $row['card_id'] = $cardId;
        $row['image_url'] = $cardId !== '' ? 'https://art.hearthstonejson.com/v1/render/latest/ruRU/256x/' . rawurlencode($cardId) . '.png' : '';
    }
    unset($row);
    $total = count($rows);
    $rows = analytics_filter_rows($rows, (string)($query['q'] ?? ''), (int)($query['limit'] ?? 200));
    $fetch['payload']['meta'] = [
        'count' => $total,
        'fetched_at' => $fetch['payload']['fetched_at'] ?? null,
        'source_id' => $sourceId,
        'stale' => false,
    ];
    return analytics_result_shell('constructed_cards', $definition, $fetch, [
        ['label' => 'Показано', 'value' => count($rows)],
        ['label' => 'Всего', 'value' => $total],
        ['label' => 'Формат', 'value' => strtoupper($format)],
        ['label' => 'Срез', 'value' => strtoupper($rank) . ' · ' . strtoupper($period)],
    ], [
        analytics_column('image_url', 'Арт', 'image'),
        analytics_column('name', 'Карта'),
        analytics_column('cardClass', 'Класс'),
        analytics_column('cost', 'Мана', 'number'),
        analytics_column('deck_winrate', 'Винрейт колод', 'percent'),
        analytics_column('deck_popularity', 'Популярность', 'percent'),
        analytics_column('opening_hand_winrate', 'Стартовая рука', 'percent'),
        analytics_column('keep_percentage', 'Оставляют', 'percent'),
        analytics_column('winrate_when_drawn', 'При доборе', 'percent'),
        analytics_column('times_played', 'Игры', 'number'),
        analytics_column('card_id', 'card_id', 'code'),
    ], $rows);
}

function analytics_arena_cards(array $definition, array $query): array
{
    $sourceKey = (string)($query['arena_source'] ?? 'firestone');
    $sources = [
        'hsreplay' => 'hsreplay_arena_cards_advanced',
        'firestone' => 'firestone_arena_cards_normal',
        'underground' => 'firestone_arena_cards_underground',
    ];
    $sourceId = $sources[$sourceKey] ?? $sources['firestone'];
    $fetch = analytics_fetch_json('/datasets/' . $sourceId, [], (int)$definition['ttl']);
    $structured = is_array($fetch['payload']['data']['structured'] ?? null) ? $fetch['payload']['data']['structured'] : [];
    $rows = is_array($structured['cards'] ?? null) ? $structured['cards'] : [];
    foreach ($rows as &$row) {
        $cardId = (string)($row['card_id'] ?? $row['id'] ?? '');
        $row['card_id'] = $cardId;
        $row['image_url'] = $row['image_url'] ?? ($cardId !== '' ? 'https://art.hearthstonejson.com/v1/render/latest/ruRU/256x/' . rawurlencode($cardId) . '.png' : '');
    }
    unset($row);
    $total = count($rows);
    $rows = analytics_filter_rows($rows, (string)($query['q'] ?? ''), (int)($query['limit'] ?? 200));
    $fetch['payload']['meta'] = [
        'count' => $total,
        'fetched_at' => $fetch['payload']['fetched_at'] ?? $structured['last_update_date'] ?? null,
        'source_id' => $sourceId,
        'stale' => false,
    ];
    return analytics_result_shell('arena_cards', $definition, $fetch, [
        ['label' => 'Показано', 'value' => count($rows)],
        ['label' => 'Всего', 'value' => $total],
        ['label' => 'Режим', 'value' => $sourceKey === 'underground' ? 'Подпольная' : 'Обычная'],
        ['label' => 'Источник', 'value' => $sourceKey === 'hsreplay' ? 'HSReplay' : 'Firestone'],
    ], [
        analytics_column('image_url', 'Арт', 'image'),
        analytics_column('name', 'Карта'),
        analytics_column('cardClass', 'Класс'),
        analytics_column('tier', 'Тир', 'status'),
        analytics_column('win_rate', 'Винрейт', 'percent'),
        analytics_column('pick_rate', 'Выбор', 'percent'),
        analytics_column('drawn_winrate', 'При доборе', 'percent'),
        analytics_column('mulligan_winrate', 'После муллигана', 'percent'),
        analytics_column('times_played', 'Игры', 'number'),
        analytics_column('card_id', 'card_id', 'code'),
    ], $rows);
}

function analytics_bg_minions(array $definition, array $query): array
{
    $sourceId = 'hsreplay_battlegrounds_minions';
    $fetch = analytics_fetch_json('/datasets/' . $sourceId, [], (int)$definition['ttl']);
    $structured = is_array($fetch['payload']['data']['structured'] ?? null) ? $fetch['payload']['data']['structured'] : [];
    $source = is_array($structured['source'] ?? null) ? $structured['source'] : [];
    $rows = is_array($structured['minions'] ?? null) ? $structured['minions'] : [];
    foreach ($rows as &$row) {
        $cardId = (string)($row['id'] ?? $row['card_id'] ?? '');
        $row['card_id'] = $cardId;
        $row['dbf_id'] = $row['dbfId'] ?? $row['dbf_id'] ?? null;
        $row['name_ru'] = $row['name_ru'] ?? $row['name'] ?? $cardId;
        $row['image_url'] = $cardId !== '' ? '/uploads/cards/' . rawurlencode($cardId) . '.png' : '';
        $row['image_kind'] = 'card';
        $row['source_url'] = $source['url'] ?? null;
    }
    unset($row);

    if (!empty($query['tavern_tier'])) {
        $tier = (int)$query['tavern_tier'];
        $rows = array_values(array_filter($rows, static fn(array $row): bool => (int)($row['tavern_tier'] ?? 0) === $tier));
    }
    $total = count($rows);
    $rows = analytics_filter_rows($rows, (string)($query['q'] ?? ''), (int)($query['limit'] ?? $total));
    $fetch['payload']['meta'] = [
        'count' => $total,
        'fetched_at' => $fetch['payload']['fetched_at'] ?? null,
        'source_id' => $sourceId,
        'stale' => false,
    ];

    return analytics_result_shell('bg_minions', $definition, $fetch, [
        ['label' => 'Показано', 'value' => count($rows)],
        ['label' => 'Всего', 'value' => $total],
        ['label' => 'Источник', 'value' => 'HSReplay'],
        ['label' => 'Раунды', 'value' => count($rows[0]['combat_rounds'] ?? [])],
    ], [
        analytics_column('image_url', 'Карта', 'image'),
        analytics_column('name', 'Существо'),
        analytics_column('tavern_tier', 'Таверна', 'number'),
        analytics_column('popularity', 'Популярность', 'percent'),
        analytics_column('combat_winrate', 'Винрейт боя', 'percent'),
        analytics_column('impact', 'Влияние', 'number'),
        analytics_column('avg_placement_with', 'Среднее место', 'number'),
        analytics_column('games_with_minion', 'Игры', 'number'),
        analytics_column('card_id', 'card_id', 'code'),
    ], $rows);
}

function analytics_bg_heroes_ratings(array $definition, array $query): array
{
    $mode = (string)($query['mode'] ?? 'solo');
    if ($mode === 'duos') {
        $fetch = analytics_fetch_json('/v1/bg/heroes', [
            'mode' => 'duos',
            'q' => $query['q'] ?? '',
            'limit' => $query['limit'] ?? 200,
            'offset' => $query['offset'] ?? 0,
        ], (int)$definition['ttl']);
        return analytics_normalize('bg_heroes', $definition, $fetch);
    }

    $rating = (string)($query['rating'] ?? '50');
    $url = 'https://static.zerotoheroes.com/api/bgs/hero-stats/mmr-' . $rating . '/past-three/overview-from-hourly.gz.json';
    $fetch = analytics_fetch_absolute_json($url, (int)$definition['ttl']);
    $namesFetch = analytics_fetch_json('/v1/bg/heroes', ['mode' => 'solo', 'limit' => 500, 'offset' => 0], (int)$definition['ttl']);
    $names = [];
    foreach (($namesFetch['payload']['data'] ?? []) as $hero) {
        if (is_array($hero) && !empty($hero['id'])) {
            $names[(string)$hero['id']] = $hero;
        }
    }
    $rows = [];
    foreach (($fetch['payload']['heroStats'] ?? []) as $hero) {
        if (!is_array($hero) || empty($hero['heroCardId'])) {
            continue;
        }
        $cardId = (string)$hero['heroCardId'];
        $known = $names[$cardId] ?? [];
        $offered = is_numeric($hero['totalOffered'] ?? null) ? (float)$hero['totalOffered'] : 0.0;
        $picked = is_numeric($hero['totalPicked'] ?? null) ? (float)$hero['totalPicked'] : 0.0;
        $placements = is_array($hero['placementDistribution'] ?? null) ? $hero['placementDistribution'] : [];
        $firstPlace = null;
        foreach ($placements as $placement) {
            if ((int)($placement['rank'] ?? 0) === 1) {
                $firstPlace = $placement['percentage'] ?? null;
                break;
            }
        }
        $rows[] = [
            'image_url' => 'https://art.hearthstonejson.com/v1/256x/' . rawurlencode($cardId) . '.jpg',
            'image_kind' => 'hero_portrait',
            'hero' => $known['hero'] ?? $cardId,
            'tier' => $known['tier'] ?? '',
            'pick_rate_value' => $offered > 0 ? round(100 * $picked / $offered, 2) : null,
            'avg_placement' => $hero['averagePosition'] ?? null,
            'first_place' => $firstPlace,
            'games' => $hero['dataPoints'] ?? null,
            'card_id' => $cardId,
            'dbfId' => $known['dbfId'] ?? null,
        ];
    }
    usort($rows, static fn(array $a, array $b): int => ((float)($a['avg_placement'] ?? 99)) <=> ((float)($b['avg_placement'] ?? 99)));
    $total = count($rows);
    $rows = analytics_filter_rows($rows, (string)($query['q'] ?? ''), (int)($query['limit'] ?? 200));
    $fetch['payload']['meta'] = [
        'count' => $total,
        'fetched_at' => $fetch['payload']['lastUpdateDate'] ?? null,
        'source_id' => 'firestone_bg_heroes_mmr_' . $rating,
        'stale' => false,
    ];
    return analytics_result_shell('bg_heroes', $definition, $fetch, [
        ['label' => 'Героев', 'value' => count($rows)],
        ['label' => 'Рейтинг', 'value' => $rating === '100' ? 'Все игроки' : 'Top ' . $rating . '%'],
        ['label' => 'Период', 'value' => '3 дня'],
        ['label' => 'Источник', 'value' => 'Firestone'],
    ], [
        analytics_column('image_url', 'Арт', 'image'),
        analytics_column('hero', 'Герой'),
        analytics_column('tier', 'Тир', 'status'),
        analytics_column('avg_placement', 'Среднее место', 'number'),
        analytics_column('first_place', '1-е место', 'percent'),
        analytics_column('pick_rate_value', 'Выбор', 'percent'),
        analytics_column('games', 'Игры', 'number'),
        analytics_column('card_id', 'card_id', 'code'),
    ], $rows);
}

function analytics_card_statistics(array $definition, array $query): array
{
    $name = (string)$query['card_name'];
    $requests = [
        'trends' => ['/api/db/cards/trends', ['card_name' => $name, 'limit' => 100]],
        'minions' => ['/datasets/hsreplay_battlegrounds_minions', []],
        'heroes' => ['/v1/bg/heroes', ['q' => $name, 'limit' => 20]],
    ];
    $responses = [];
    $warnings = [];
    foreach ($requests as $key => [$path, $params]) {
        try {
            $responses[$key] = analytics_fetch_json($path, $params, (int)$definition['ttl']);
        } catch (Throwable $exception) {
            $warnings[] = $exception->getMessage();
        }
    }
    if (!$responses) {
        throw new RuntimeException('Не удалось получить статистику карты.');
    }

    $rows = [];
    foreach (($responses['trends']['payload']['trends'] ?? []) as $trend) {
        $rows[] = [
            'dataset' => 'Динамика карты',
            'source' => $trend['source_id'] ?? '',
            'context' => implode(' · ', array_filter([$trend['class'] ?? '', $trend['archetype'] ?? ''])),
            'recorded_at' => $trend['recorded_at'] ?? '',
            'popularity' => $trend['popularity'] ?? null,
            'winrate' => $trend['winrate'] ?? $trend['win_rate'] ?? null,
            'avg_placement' => null,
            'impact' => null,
            'games' => $trend['games'] ?? null,
            'card_id' => $trend['card_id'] ?? '',
            'dbf_id' => $trend['dbf_id'] ?? '',
        ];
    }
    $minionRows = $responses['minions']['payload']['data']['structured']['minions'] ?? [];
    $minionNeedle = mb_strtolower($name, 'UTF-8');
    $minionRows = array_slice(array_values(array_filter(is_array($minionRows) ? $minionRows : [], static function (array $minion) use ($minionNeedle): bool {
        $haystack = mb_strtolower(implode(' ', [
            (string)($minion['name'] ?? ''),
            (string)($minion['id'] ?? ''),
            (string)($minion['dbfId'] ?? ''),
        ]), 'UTF-8');
        return mb_strpos($haystack, $minionNeedle, 0, 'UTF-8') !== false;
    })), 0, 20);
    foreach ($minionRows as $minion) {
        $rows[] = [
            'dataset' => 'BG существо',
            'source' => $responses['minions']['payload']['source_id'] ?? 'hsreplay_battlegrounds_minions',
            'context' => 'Таверна ' . ($minion['tavern_tier'] ?? '—'),
            'recorded_at' => $responses['minions']['payload']['fetched_at'] ?? '',
            'popularity' => $minion['popularity'] ?? null,
            'winrate' => $minion['combat_winrate'] ?? null,
            'avg_placement' => $minion['avg_placement_with'] ?? null,
            'impact' => $minion['impact'] ?? null,
            'games' => $minion['games_with_minion'] ?? null,
            'card_id' => $minion['id'] ?? '',
            'dbf_id' => $minion['dbfId'] ?? '',
        ];
    }
    foreach (($responses['heroes']['payload']['data'] ?? []) as $hero) {
        $rows[] = [
            'dataset' => 'BG герой',
            'source' => analytics_meta($responses['heroes']['payload'])['source_id'] ?? 'hsreplay',
            'context' => implode(' · ', array_filter([$hero['tier'] ?? '', $hero['best_composition']['name'] ?? ''])),
            'recorded_at' => analytics_meta($responses['heroes']['payload'])['fetched_at'] ?? '',
            'popularity' => $hero['pick_rate_value'] ?? null,
            'winrate' => null,
            'avg_placement' => $hero['avg_placement'] ?? null,
            'impact' => null,
            'games' => null,
            'card_id' => $hero['id'] ?? '',
            'dbf_id' => $hero['dbfId'] ?? '',
        ];
    }

    $sources = [];
    foreach ($rows as $row) {
        if ($row['source'] !== '') {
            $sources[$row['source']] = true;
        }
    }
    $cached = $responses && count(array_filter($responses, static fn(array $response): bool => !empty($response['cached']))) === count($responses);
    $staleCache = count(array_filter($responses, static fn(array $response): bool => !empty($response['stale_cache']))) > 0;

    return [
        'ok' => true,
        'module' => 'card',
        'title' => $definition['title'] . ': ' . $name,
        'description' => $definition['description'],
        'summary' => [
            ['label' => 'Записей', 'value' => count($rows)],
            ['label' => 'Источников', 'value' => count($sources)],
            ['label' => 'Запрос', 'value' => $name],
        ],
        'columns' => [
            analytics_column('dataset', 'Набор'),
            analytics_column('source', 'Источник', 'code'),
            analytics_column('context', 'Контекст'),
            analytics_column('recorded_at', 'Срез', 'date'),
            analytics_column('popularity', 'Популярность / выбор', 'percent'),
            analytics_column('winrate', 'Винрейт', 'percent'),
            analytics_column('avg_placement', 'Среднее место', 'number'),
            analytics_column('impact', 'Влияние', 'number'),
            analytics_column('games', 'Игры', 'number'),
            analytics_column('card_id', 'card_id', 'code'),
            analytics_column('dbf_id', 'dbfId', 'code'),
        ],
        'rows' => $rows,
        'warnings' => array_values(array_unique($warnings)),
        'meta' => [
            'total' => count($rows),
            'updated_at' => gmdate(DATE_ATOM),
            'source_id' => implode(', ', array_keys($sources)),
            'stale' => false,
            'cached' => $cached,
            'stale_cache' => $staleCache,
            'cache_age' => null,
        ],
    ];
}

function analytics_module_response(string $module, array $input): array
{
    $registry = analytics_module_registry();
    if (!isset($registry[$module])) {
        throw new InvalidArgumentException('Неизвестный раздел статистики.');
    }
    $definition = $registry[$module];
    $query = analytics_safe_query($definition, $input);
    if (!empty($definition['composite'])) {
        if ($module === 'constructed_cards') {
            return analytics_constructed_cards($definition, $query);
        }
        if ($module === 'arena_cards') {
            return analytics_arena_cards($definition, $query);
        }
        if ($module === 'bg_heroes') {
            return analytics_bg_heroes_ratings($definition, $query);
        }
        if ($module === 'bg_minions') {
            return analytics_bg_minions($definition, $query);
        }
        return analytics_card_statistics($definition, $query);
    }

    $fetch = analytics_fetch_json((string)$definition['path'], $query, (int)$definition['ttl']);
    $response = analytics_normalize($module, $definition, $fetch);
    return $module === 'overview'
        ? analytics_attach_parsing_reliability($response)
        : $response;
}
