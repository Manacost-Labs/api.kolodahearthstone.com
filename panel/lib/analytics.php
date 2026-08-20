<?php
declare(strict_types=1);

const ANALYTICS_RELIABILITY_STALE_CACHE_MAX_AGE_SECONDS = 300;

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

function analytics_reliability_exact_count($value): ?int
{
    if (!is_numeric($value)) {
        return null;
    }
    $number = (float)$value;
    if (!is_finite($number) || $number < 0 || floor($number) !== $number) {
        return null;
    }
    return (int)$number;
}

function analytics_empty_outcome_recovery(): array
{
    $emptyCounts = [
        'events' => null,
        'recovered_to_fresh' => null,
        'reclassified_upstream_pending' => null,
        'unresolved' => null,
    ];
    return [
        'reported' => false,
        'provisional' => $emptyCounts,
        'lkg_served' => $emptyCounts,
    ];
}

function analytics_normalize_outcome_recovery($value, array $counts): array
{
    $empty = analytics_empty_outcome_recovery();
    if (!is_array($value)) {
        return $empty;
    }

    $normalized = ['reported' => true];
    foreach (['provisional', 'lkg_served'] as $outcome) {
        $raw = is_array($value[$outcome] ?? null) ? $value[$outcome] : null;
        if ($raw === null) {
            return $empty;
        }
        $events = analytics_reliability_exact_count($raw['events'] ?? null);
        $recovered = analytics_reliability_exact_count(
            $raw['recovered_to_fresh'] ?? null
        );
        $upstreamPending = analytics_reliability_exact_count(
            $raw['reclassified_upstream_pending'] ?? null
        );
        $unresolved = analytics_reliability_exact_count($raw['unresolved'] ?? null);
        if (
            $events === null
            || $recovered === null
            || $upstreamPending === null
            || $unresolved === null
            || $events !== ($counts[$outcome] ?? null)
            || $events !== $recovered + $upstreamPending + $unresolved
        ) {
            return $empty;
        }
        $normalized[$outcome] = [
            'events' => $events,
            'recovered_to_fresh' => $recovered,
            'reclassified_upstream_pending' => $upstreamPending,
            'unresolved' => $unresolved,
        ];
    }
    return $normalized;
}

function analytics_empty_reliability_slo(): array
{
    return [
        'reported' => false,
        'target_rate_pct' => 99.0,
        'objective_status' => 'collecting',
        'good_attempts' => null,
        'bad_attempts' => null,
        'allowed_bad_attempts' => null,
        'bad_attempts_over_budget' => null,
        'error_budget_remaining_attempts' => null,
        'error_budget_consumed_pct' => null,
    ];
}

function analytics_normalize_reliability_slo(
    $value,
    int $eligibleAttempts,
    string $measurementStatus,
    bool $staleCache = false
): array {
    $empty = analytics_empty_reliability_slo();
    if (!is_array($value)) {
        return $empty;
    }

    $target = analytics_reliability_percentage($value['target_rate_pct'] ?? null);
    $good = analytics_reliability_exact_count($value['good_attempts'] ?? null);
    $bad = analytics_reliability_exact_count($value['bad_attempts'] ?? null);
    $overBudget = analytics_reliability_exact_count(
        $value['bad_attempts_over_budget'] ?? null
    );
    $allowedBad = is_numeric($value['allowed_bad_attempts'] ?? null)
        ? (float)$value['allowed_bad_attempts']
        : null;
    $remaining = is_numeric($value['error_budget_remaining_attempts'] ?? null)
        ? (float)$value['error_budget_remaining_attempts']
        : null;
    $consumed = is_numeric($value['error_budget_consumed_pct'] ?? null)
        ? (float)$value['error_budget_consumed_pct']
        : null;
    $objective = (string)($value['objective_status'] ?? '');
    if (
        $target === null
        || $good === null
        || $bad === null
        || $overBudget === null
        || $allowedBad === null
        || $remaining === null
        || !is_finite($allowedBad)
        || !is_finite($remaining)
        || $allowedBad < 0.0
        || ($consumed !== null && (!is_finite($consumed) || $consumed < 0.0))
        || !in_array($objective, ['collecting', 'meeting', 'breached'], true)
        || $good + $bad !== $eligibleAttempts
    ) {
        return $empty;
    }

    $expectedAllowed = round($eligibleAttempts * ((100.0 - $target) / 100.0), 2);
    $expectedRemaining = round($expectedAllowed - $bad, 2);
    $expectedOverBudget = max(0, (int)ceil($bad - $expectedAllowed));
    $expectedConsumed = $expectedAllowed > 0.0
        ? round(($bad / $expectedAllowed) * 100.0, 2)
        : null;
    if (
        abs($allowedBad - $expectedAllowed) > 0.011
        || abs($remaining - $expectedRemaining) > 0.011
        || $overBudget !== $expectedOverBudget
        || (($consumed === null) !== ($expectedConsumed === null))
        || (
            $consumed !== null
            && $expectedConsumed !== null
            && abs($consumed - $expectedConsumed) > 0.011
        )
    ) {
        return $empty;
    }

    $presentedObjective = $staleCache || $measurementStatus !== 'observed'
        ? 'collecting'
        : (($eligibleAttempts > 0 && $good / $eligibleAttempts >= $target / 100.0)
            ? 'meeting'
            : 'breached');
    if ($measurementStatus === 'observed' && !$staleCache && $objective !== $presentedObjective) {
        return $empty;
    }

    return [
        'reported' => true,
        'target_rate_pct' => $target,
        'objective_status' => $presentedObjective,
        'good_attempts' => $good,
        'bad_attempts' => $bad,
        'allowed_bad_attempts' => round($allowedBad, 2),
        'bad_attempts_over_budget' => $overBudget,
        'error_budget_remaining_attempts' => round($remaining, 2),
        'error_budget_consumed_pct' => $consumed === null ? null : round($consumed, 2),
    ];
}

function analytics_empty_scheduled_reliability(): array
{
    return [
        'reported' => false,
        'ledger_status' => 'absent',
        'measurement_status' => 'collecting',
        'schedule_coverage_ratio' => 0.0,
        'temporal_coverage_ratio' => 0.0,
        'coverage_started_at' => null,
        'materialized_through' => null,
        'tracked_schedules' => null,
        'catalog_schedules' => null,
        'expected_slots' => null,
        'eligible_slots' => null,
        'excluded_slots' => null,
        'pending_slots' => null,
        'due_slots' => null,
        'on_time_fresh' => null,
        'on_time_upstream_pending' => null,
        'on_time_nonfresh' => null,
        'late' => null,
        'missing' => null,
        'on_time_fresh_rate_pct' => null,
        'parser_eligible_due_slots' => null,
        'parser_on_time_fresh_rate_pct' => null,
        'target_rate_pct' => 99.0,
        'objective_status' => 'collecting',
        'parser_objective_status' => 'collecting',
    ];
}

function analytics_reliability_ratio($value): ?float
{
    if (!is_numeric($value) || is_bool($value)) {
        return null;
    }
    $number = (float)$value;
    if (!is_finite($number) || $number < 0.0 || $number > 1.0) {
        return null;
    }
    return round($number, 4);
}

function analytics_reliability_iso_datetime($value): ?string
{
    if (!is_string($value) || !preg_match(
        '/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/D',
        $value
    )) {
        return null;
    }
    $parts = date_parse($value);
    if (($parts['error_count'] ?? 1) !== 0 || ($parts['warning_count'] ?? 1) !== 0) {
        return null;
    }
    return $value;
}

function analytics_normalize_scheduled_reliability(
    $raw,
    bool $forceCollecting = false
): array
{
    $empty = analytics_empty_scheduled_reliability();
    if (!is_array($raw)) {
        return $empty;
    }

    $ledgerStatus = (string)($raw['ledger_status'] ?? '');
    $measurementStatus = (string)($raw['measurement_status'] ?? '');
    $objectiveStatus = (string)($raw['objective_status'] ?? '');
    $parserObjectiveStatus = (string)($raw['parser_objective_status'] ?? $objectiveStatus);
    $scheduleCoverage = analytics_reliability_ratio($raw['schedule_coverage_ratio'] ?? null);
    $temporalCoverage = analytics_reliability_ratio($raw['temporal_coverage_ratio'] ?? null);
    $target = analytics_reliability_percentage($raw['target_rate_pct'] ?? null);
    if (
        !in_array($ledgerStatus, ['absent', 'partial', 'covered'], true)
        || !in_array($measurementStatus, ['collecting', 'observed'], true)
        || !in_array($objectiveStatus, ['collecting', 'meeting', 'breached'], true)
        || !in_array($parserObjectiveStatus, ['collecting', 'meeting', 'breached'], true)
        || $scheduleCoverage === null
        || $temporalCoverage === null
        || $target === null
    ) {
        return $empty;
    }
    if ($ledgerStatus === 'absent') {
        return $empty;
    }

    $countKeys = [
        'tracked_schedules', 'catalog_schedules', 'expected_slots', 'eligible_slots',
        'excluded_slots', 'pending_slots', 'due_slots', 'on_time_fresh',
        'on_time_nonfresh', 'late', 'missing',
    ];
    $counts = [];
    foreach ($countKeys as $key) {
        $counts[$key] = analytics_reliability_exact_count($raw[$key] ?? null);
        if ($counts[$key] === null) {
            return $empty;
        }
    }
    $counts['on_time_upstream_pending'] = analytics_reliability_exact_count(
        $raw['on_time_upstream_pending'] ?? 0
    );
    $defaultParserEligible = $counts['due_slots'] - $counts['on_time_upstream_pending'];
    $counts['parser_eligible_due_slots'] = analytics_reliability_exact_count(
        $raw['parser_eligible_due_slots'] ?? $defaultParserEligible
    );
    if (
        $counts['on_time_upstream_pending'] === null
        || $counts['parser_eligible_due_slots'] === null
    ) {
        return $empty;
    }

    $coverageStartedAt = analytics_reliability_iso_datetime(
        $raw['coverage_started_at'] ?? null
    );
    $materializedThrough = analytics_reliability_iso_datetime(
        $raw['materialized_through'] ?? null
    );
    if (
        $coverageStartedAt === null
        || $materializedThrough === null
        || new DateTimeImmutable($materializedThrough) < new DateTimeImmutable($coverageStartedAt)
    ) {
        return $empty;
    }

    $expectedScheduleCoverage = $counts['catalog_schedules'] === 0
        ? 0.0
        : round($counts['tracked_schedules'] / $counts['catalog_schedules'], 4);
    $onTimeRate = analytics_reliability_percentage(
        $raw['on_time_fresh_rate_pct'] ?? null
    );
    $expectedOnTimeRate = $counts['due_slots'] === 0
        ? null
        : round($counts['on_time_fresh'] / $counts['due_slots'] * 100, 2);
    $parserOnTimeRate = analytics_reliability_percentage(
        $raw['parser_on_time_fresh_rate_pct'] ?? (
            $counts['parser_eligible_due_slots'] === 0
                ? null
                : round(
                    $counts['on_time_fresh']
                    / $counts['parser_eligible_due_slots'] * 100,
                    2
                )
        )
    );
    $expectedParserOnTimeRate = $counts['parser_eligible_due_slots'] === 0
        ? null
        : round(
            $counts['on_time_fresh'] / $counts['parser_eligible_due_slots'] * 100,
            2
        );
    $covered = $scheduleCoverage === 1.0 && $temporalCoverage === 1.0;
    $expectedObjective = 'collecting';
    if ($measurementStatus === 'observed' && $counts['due_slots'] > 0) {
        $expectedObjective = $counts['on_time_fresh'] * 100
            >= $target * $counts['due_slots']
            ? 'meeting'
            : 'breached';
    }
    $expectedParserObjective = 'collecting';
    if ($measurementStatus === 'observed' && $counts['parser_eligible_due_slots'] > 0) {
        $expectedParserObjective = $counts['on_time_fresh'] * 100
            >= $target * $counts['parser_eligible_due_slots']
            ? 'meeting'
            : 'breached';
    }

    if (
        $counts['tracked_schedules'] === 0
        || $counts['catalog_schedules'] === 0
        || $counts['tracked_schedules'] > $counts['catalog_schedules']
        || abs($scheduleCoverage - $expectedScheduleCoverage) > 0.00011
        || $counts['expected_slots'] !== $counts['eligible_slots'] + $counts['excluded_slots']
        || $counts['eligible_slots'] !== $counts['due_slots'] + $counts['pending_slots']
        || $counts['due_slots'] !== $counts['on_time_fresh']
            + $counts['on_time_upstream_pending'] + $counts['on_time_nonfresh']
            + $counts['late'] + $counts['missing']
        || $counts['parser_eligible_due_slots'] !== $counts['due_slots']
            - $counts['on_time_upstream_pending']
        || ($expectedOnTimeRate === null && $onTimeRate !== null)
        || ($expectedOnTimeRate !== null && (
            $onTimeRate === null || abs($onTimeRate - $expectedOnTimeRate) > 0.011
        ))
        || ($expectedParserOnTimeRate === null && $parserOnTimeRate !== null)
        || ($expectedParserOnTimeRate !== null && (
            $parserOnTimeRate === null
            || abs($parserOnTimeRate - $expectedParserOnTimeRate) > 0.011
        ))
        || ($ledgerStatus === 'covered' && !$covered)
        || ($ledgerStatus === 'partial' && $covered)
        || ($measurementStatus === 'observed' && ($ledgerStatus !== 'covered' || !$covered))
        || ($measurementStatus === 'collecting' && $objectiveStatus !== 'collecting')
        || ($measurementStatus === 'collecting' && $parserObjectiveStatus !== 'collecting')
        || $objectiveStatus !== $expectedObjective
        || $parserObjectiveStatus !== $expectedParserObjective
    ) {
        return $empty;
    }

    return array_merge(
        [
            'reported' => true,
            'ledger_status' => $ledgerStatus,
            'measurement_status' => $forceCollecting ? 'collecting' : $measurementStatus,
            'schedule_coverage_ratio' => $scheduleCoverage,
            'temporal_coverage_ratio' => $temporalCoverage,
            'coverage_started_at' => $coverageStartedAt,
            'materialized_through' => $materializedThrough,
        ],
        $counts,
        [
            'on_time_fresh_rate_pct' => $onTimeRate,
            'parser_on_time_fresh_rate_pct' => $parserOnTimeRate,
            'target_rate_pct' => $target,
            'objective_status' => $forceCollecting ? 'collecting' : $objectiveStatus,
            'parser_objective_status' => $forceCollecting
                ? 'collecting'
                : $parserObjectiveStatus,
        ]
    );
}

function analytics_empty_verified_completeness(): array
{
    return [
        'reported' => false,
        'instrumented_sources' => null,
        'catalog_sources' => null,
        'source_catalog_coverage_pct' => null,
        'observed_instrumented_sources' => null,
        'instrumented_source_observation_coverage_pct' => null,
        'sources_meeting_target' => null,
        'sources_below_target' => null,
        'sources_without_observations' => null,
        'source_target_attainment_pct' => null,
        'macro_complete_fresh_rate_pct' => null,
        'macro_target_met' => null,
        'worst_observed_source_rate_pct' => null,
        'tracked_attempts' => null,
        'complete_fresh' => null,
        'states' => [
            'complete' => null,
            'incomplete' => null,
            'unknown' => null,
        ],
        'coverage_of_all_parser_attempts_pct' => null,
        'complete_fresh_rate_pct' => null,
        'target_rate_pct' => 99.0,
        'objective_status' => 'collecting',
    ];
}

function analytics_empty_parsesunix_rollout(): array
{
    return [
        'reported' => false,
        'observed_attempts' => null,
        'observed_sources' => null,
        'shadow_attempts' => null,
        'active_attempts' => null,
        'transport_checked' => null,
        'transport_validated' => null,
        'transport_validated_rate_pct' => null,
        'candidate_checked' => null,
        'candidate_validated' => null,
        'candidate_validated_rate_pct' => null,
        'publication_checked' => null,
        'publication_validated' => null,
        'publication_validated_rate_pct' => null,
        'http_status_compared' => null,
        'http_status_matches' => null,
        'http_status_match_rate_pct' => null,
        'content_hash_compared' => null,
        'content_hash_matches' => null,
        'content_hash_match_rate_pct' => null,
        'paid_requests_known_attempts' => null,
        'paid_requests' => null,
        'paid_cost_known_attempts' => null,
        'paid_cost_usd' => null,
    ];
}

function analytics_normalize_parsesunix_rollout($raw): array
{
    $empty = analytics_empty_parsesunix_rollout();
    if (!is_array($raw)) {
        return $empty;
    }

    $countKeys = [
        'observed_attempts', 'observed_sources', 'shadow_attempts', 'active_attempts',
        'transport_checked', 'transport_validated', 'candidate_checked',
        'candidate_validated', 'publication_checked', 'publication_validated',
        'http_status_compared', 'http_status_matches', 'content_hash_compared',
        'content_hash_matches', 'paid_requests_known_attempts',
        'paid_cost_known_attempts',
    ];
    $counts = [];
    foreach ($countKeys as $key) {
        $counts[$key] = analytics_reliability_exact_count($raw[$key] ?? null);
        if ($counts[$key] === null) {
            return $empty;
        }
    }

    $ratePairs = [
        'transport_validated_rate_pct' => ['transport_validated', 'transport_checked'],
        'candidate_validated_rate_pct' => ['candidate_validated', 'candidate_checked'],
        'publication_validated_rate_pct' => ['publication_validated', 'publication_checked'],
        'http_status_match_rate_pct' => ['http_status_matches', 'http_status_compared'],
        'content_hash_match_rate_pct' => ['content_hash_matches', 'content_hash_compared'],
    ];
    $rates = [];
    foreach ($ratePairs as $rateKey => [$numeratorKey, $denominatorKey]) {
        $rates[$rateKey] = analytics_reliability_percentage($raw[$rateKey] ?? null);
        $expected = $counts[$denominatorKey] === 0
            ? null
            : round($counts[$numeratorKey] / $counts[$denominatorKey] * 100, 2);
        if (
            ($expected === null && $rates[$rateKey] !== null)
            || ($expected !== null && (
                $rates[$rateKey] === null || abs($rates[$rateKey] - $expected) > 0.011
            ))
        ) {
            return $empty;
        }
    }

    $observedAttempts = $counts['observed_attempts'];
    if (
        $counts['shadow_attempts'] + $counts['active_attempts'] !== $observedAttempts
        || $counts['observed_sources'] > $observedAttempts
        || $counts['transport_checked'] > $observedAttempts
        || $counts['transport_validated'] > $counts['transport_checked']
        || $counts['candidate_checked'] > $counts['transport_validated']
        || $counts['candidate_validated'] > $counts['candidate_checked']
        || $counts['publication_checked'] > $counts['active_attempts']
        || $counts['publication_validated'] > $counts['publication_checked']
        || $counts['http_status_compared'] > $counts['shadow_attempts']
        || $counts['http_status_matches'] > $counts['http_status_compared']
        || $counts['content_hash_compared'] > $counts['shadow_attempts']
        || $counts['content_hash_matches'] > $counts['content_hash_compared']
        || $counts['paid_requests_known_attempts'] > $observedAttempts
        || $counts['paid_cost_known_attempts'] > $observedAttempts
    ) {
        return $empty;
    }

    $paidRequests = null;
    if ($counts['paid_requests_known_attempts'] === $observedAttempts) {
        $paidRequests = analytics_reliability_exact_count($raw['paid_requests'] ?? null);
        if ($paidRequests === null) {
            return $empty;
        }
    } elseif (($raw['paid_requests'] ?? null) !== null) {
        return $empty;
    }

    $paidCost = null;
    if ($counts['paid_cost_known_attempts'] === $observedAttempts) {
        $rawPaidCost = $raw['paid_cost_usd'] ?? null;
        if (!is_string($rawPaidCost) || !preg_match('/^\d+\.\d{6}$/D', $rawPaidCost)) {
            return $empty;
        }
        $paidCost = $rawPaidCost;
    } elseif (($raw['paid_cost_usd'] ?? null) !== null) {
        return $empty;
    }

    return array_merge(
        ['reported' => true],
        $counts,
        $rates,
        [
            'paid_requests' => $paidRequests,
            'paid_cost_usd' => $paidCost,
        ]
    );
}

function analytics_normalize_verified_completeness(
    $raw,
    int $allParserAttempts,
    bool $parentCountsConsistent,
    string $parentMeasurementStatus
): array {
    $empty = analytics_empty_verified_completeness();
    if (!$parentCountsConsistent || !is_array($raw) || !is_array($raw['states'] ?? null)) {
        return $empty;
    }

    $instrumentedSources = analytics_reliability_exact_count(
        $raw['instrumented_sources'] ?? null
    );
    $catalogSources = analytics_reliability_exact_count($raw['catalog_sources'] ?? null);
    $sourceCatalogCoverage = analytics_reliability_percentage(
        $raw['source_catalog_coverage_pct'] ?? null
    );
    $observedInstrumentedSources = analytics_reliability_exact_count(
        $raw['observed_instrumented_sources'] ?? null
    );
    $instrumentedObservationCoverage = analytics_reliability_percentage(
        $raw['instrumented_source_observation_coverage_pct'] ?? null
    );
    $sourcesMeetingTarget = analytics_reliability_exact_count(
        $raw['sources_meeting_target'] ?? null
    );
    $sourcesBelowTarget = analytics_reliability_exact_count(
        $raw['sources_below_target'] ?? null
    );
    $sourcesWithoutObservations = analytics_reliability_exact_count(
        $raw['sources_without_observations'] ?? null
    );
    $sourceTargetAttainment = analytics_reliability_percentage(
        $raw['source_target_attainment_pct'] ?? null
    );
    $macroCompleteFreshRate = analytics_reliability_percentage(
        $raw['macro_complete_fresh_rate_pct'] ?? null
    );
    $macroTargetMet = is_bool($raw['macro_target_met'] ?? null)
        ? $raw['macro_target_met']
        : null;
    $worstObservedSourceRate = analytics_reliability_percentage(
        $raw['worst_observed_source_rate_pct'] ?? null
    );
    $trackedAttempts = analytics_reliability_exact_count($raw['tracked_attempts'] ?? null);
    $completeFresh = analytics_reliability_exact_count($raw['complete_fresh'] ?? null);
    $complete = analytics_reliability_exact_count($raw['states']['complete'] ?? null);
    $incomplete = analytics_reliability_exact_count($raw['states']['incomplete'] ?? null);
    $unknown = analytics_reliability_exact_count($raw['states']['unknown'] ?? null);
    $coverage = analytics_reliability_percentage(
        $raw['coverage_of_all_parser_attempts_pct'] ?? null
    );
    $completeFreshRate = analytics_reliability_percentage(
        $raw['complete_fresh_rate_pct'] ?? null
    );
    $target = analytics_reliability_percentage($raw['target_rate_pct'] ?? null);
    $objective = (string)($raw['objective_status'] ?? '');

    if (
        $instrumentedSources === null
        || $catalogSources === null
        || $observedInstrumentedSources === null
        || $sourcesMeetingTarget === null
        || $sourcesBelowTarget === null
        || $sourcesWithoutObservations === null
        || $macroTargetMet === null
        || $trackedAttempts === null
        || $completeFresh === null
        || $complete === null
        || $incomplete === null
        || $unknown === null
        || $target === null
        || abs($target - 99.0) > 0.001
        || !in_array($objective, ['collecting', 'met', 'miss'], true)
        || !in_array($parentMeasurementStatus, ['collecting', 'observed'], true)
        || $instrumentedSources > $catalogSources
        || $observedInstrumentedSources > $instrumentedSources
        || $sourcesMeetingTarget + $sourcesBelowTarget !== $observedInstrumentedSources
        || $sourcesWithoutObservations !== $instrumentedSources - $observedInstrumentedSources
        || $trackedAttempts > $allParserAttempts
        || $completeFresh > $complete
        || $sourcesMeetingTarget > $completeFresh
        || ($completeFresh === $trackedAttempts && $sourcesBelowTarget > 0)
        || $complete + $incomplete + $unknown !== $trackedAttempts
        || (
            $macroTargetMet
            && ($macroCompleteFreshRate === null || $macroCompleteFreshRate + 0.011 < 99.0)
        )
    ) {
        return $empty;
    }

    if ($catalogSources === 0) {
        if ($instrumentedSources !== 0 || $sourceCatalogCoverage !== null) {
            return $empty;
        }
    } else {
        $expectedCatalogCoverage = round(($instrumentedSources / $catalogSources) * 100, 2);
        if (
            $sourceCatalogCoverage === null
            || abs($sourceCatalogCoverage - $expectedCatalogCoverage) > 0.011
        ) {
            return $empty;
        }
    }

    if ($instrumentedSources === 0) {
        if (
            $observedInstrumentedSources !== 0
            || $instrumentedObservationCoverage !== null
            || $sourceTargetAttainment !== null
            || $macroCompleteFreshRate !== null
        ) {
            return $empty;
        }
    } else {
        $expectedObservationCoverage = round(
            ($observedInstrumentedSources / $instrumentedSources) * 100,
            2
        );
        if (
            $instrumentedObservationCoverage === null
            || abs($instrumentedObservationCoverage - $expectedObservationCoverage) > 0.011
        ) {
            return $empty;
        }
        $expectedTargetAttainment = round(
            ($sourcesMeetingTarget / $instrumentedSources) * 100,
            2
        );
        if (
            $sourceTargetAttainment === null
            || abs($sourceTargetAttainment - $expectedTargetAttainment) > 0.011
            || $macroCompleteFreshRate === null
        ) {
            return $empty;
        }

        $macroLowerBound = ($sourcesMeetingTarget * 99.0) / $instrumentedSources;
        $macroUpperBound = (
            ($sourcesMeetingTarget * 100.0) + ($sourcesBelowTarget * 99.0)
        ) / $instrumentedSources;
        if (
            $macroCompleteFreshRate + 0.011 < $macroLowerBound
            || $macroCompleteFreshRate - 0.011 > $macroUpperBound
            || $macroCompleteFreshRate - 0.011 > $instrumentedObservationCoverage
        ) {
            return $empty;
        }
    }

    if ($observedInstrumentedSources === 0) {
        $expectedEmptyMacro = $instrumentedSources === 0 ? null : 0.0;
        if (
            $worstObservedSourceRate !== null
            || $macroCompleteFreshRate !== $expectedEmptyMacro
        ) {
            return $empty;
        }
    } elseif ($worstObservedSourceRate === null) {
        return $empty;
    } else {
        $observedMean = ($macroCompleteFreshRate * $instrumentedSources)
            / $observedInstrumentedSources;
        if ($worstObservedSourceRate - 0.011 > $observedMean) {
            return $empty;
        }
        if (
            ($sourcesBelowTarget === 0 && $worstObservedSourceRate < 99.0)
            || ($sourcesBelowTarget > 0 && $worstObservedSourceRate > 99.0)
        ) {
            return $empty;
        }
    }

    if ($allParserAttempts === 0) {
        if ($trackedAttempts !== 0 || $coverage !== null) {
            return $empty;
        }
    } else {
        $expectedCoverage = round(($trackedAttempts / $allParserAttempts) * 100, 2);
        if ($coverage === null || abs($coverage - $expectedCoverage) > 0.011) {
            return $empty;
        }
    }

    if ($trackedAttempts === 0) {
        if ($completeFresh !== 0 || $completeFreshRate !== null || $objective !== 'collecting') {
            return $empty;
        }
    } else {
        $expectedRate = round(($completeFresh / $trackedAttempts) * 100, 2);
        if ($completeFreshRate === null || abs($completeFreshRate - $expectedRate) > 0.011) {
            return $empty;
        }
        $allCoverageGatesMet = $catalogSources > 0
            && $instrumentedSources * 100 >= 99 * $catalogSources
            && $instrumentedSources > 0
            && $observedInstrumentedSources * 100 >= 99 * $instrumentedSources
            && $allParserAttempts > 0
            && $trackedAttempts * 100 >= 99 * $allParserAttempts;
        $sourceTargetGateMet = $instrumentedSources > 0
            && $sourcesMeetingTarget * 100 >= 99 * $instrumentedSources;
        $expectedObjective = $parentMeasurementStatus !== 'observed' || !$allCoverageGatesMet
            ? 'collecting'
            : (
                $completeFresh * 100 >= 99 * $trackedAttempts
                    && $sourceTargetGateMet
                    && $macroTargetMet
                    ? 'met'
                    : 'miss'
            );
        if ($objective !== $expectedObjective) {
            return $empty;
        }
    }

    return [
        'reported' => true,
        'instrumented_sources' => $instrumentedSources,
        'catalog_sources' => $catalogSources,
        'source_catalog_coverage_pct' => $sourceCatalogCoverage,
        'observed_instrumented_sources' => $observedInstrumentedSources,
        'instrumented_source_observation_coverage_pct' => $instrumentedObservationCoverage,
        'sources_meeting_target' => $sourcesMeetingTarget,
        'sources_below_target' => $sourcesBelowTarget,
        'sources_without_observations' => $sourcesWithoutObservations,
        'source_target_attainment_pct' => $sourceTargetAttainment,
        'macro_complete_fresh_rate_pct' => $macroCompleteFreshRate,
        'macro_target_met' => $macroTargetMet,
        'worst_observed_source_rate_pct' => $worstObservedSourceRate,
        'tracked_attempts' => $trackedAttempts,
        'complete_fresh' => $completeFresh,
        'states' => [
            'complete' => $complete,
            'incomplete' => $incomplete,
            'unknown' => $unknown,
        ],
        'coverage_of_all_parser_attempts_pct' => $coverage,
        'complete_fresh_rate_pct' => $completeFreshRate,
        'target_rate_pct' => $target,
        'objective_status' => $objective,
    ];
}

function analytics_normalize_parsing_reliability(
    ?array $envelope,
    bool $cached = false,
    bool $staleCache = false,
    ?int $cacheAge = null
): array {
    $collecting = [
        'state' => 'collecting',
        'message' => 'Накапливаем статистику',
        'default_window' => '24h',
        'generated_at' => null,
        'coverage_started_at' => null,
        'methodology' => null,
        'cached' => $cached,
        'stale_cache' => $staleCache,
        'cache_age' => $cacheAge,
        'windows' => [],
    ];
    if (
        $staleCache
        && ($cacheAge === null || $cacheAge > ANALYTICS_RELIABILITY_STALE_CACHE_MAX_AGE_SECONDS)
    ) {
        $collecting['message'] = 'Сохранённый отчёт устарел; ждём свежую телеметрию.';
        return $collecting;
    }
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
    $combinedSloReadiness = (string)($methodology['combined_slo_readiness'] ?? '');
    if (
        $methodology === null
        || $rawWindows === null
        || trim((string)($methodology['version'] ?? '')) === ''
        || ($methodology['scope'] ?? '') !== 'observed_scrape_and_pipeline_sources'
        || ($methodology['completeness'] ?? '') !== 'observed_attempts_plus_recorded_run_deficits'
        || !in_array('entirely_missing_scheduled_runs_not_detectable_until_ledger', $limitations, true)
        || !in_array('best_effort_write_gaps_not_detectable', $limitations, true)
        || ($methodology['missing_terminal_method'] ?? '') !== 'sum_positive_expected_minus_distinct_terminal_rows_per_recorded_logical_refresh'
        || ($methodology['coverage_method'] ?? '') !== 'complete_generic_refresh_per_24h_bucket'
        || ($methodology['coverage_scope'] ?? '') !== 'generic_scrape_sources_only'
        || ($methodology['coverage_cohort_method'] ?? '') !== 'current_canonical_scrape_registry_hash'
        || !in_array($combinedSloReadiness, ['collecting_pipeline_schedule_ledger', 'ready'], true)
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
        $observedEligibleAttempts = analytics_reliability_count(
            $rawWindow['observed_eligible_attempts'] ?? 0
        );
        $missingTerminalWindows = analytics_reliability_count(
            $rawWindow['missing_terminal_windows'] ?? 0
        );
        $eligibleAttempts = analytics_reliability_count($rawWindow['eligible_attempts'] ?? 0);
        $upstreamPendingAttempts = analytics_reliability_count(
            $rawWindow['upstream_pending_attempts'] ?? 0
        );
        $endToEndAttempts = analytics_reliability_count(
            $rawWindow['end_to_end_attempts']
                ?? ($eligibleAttempts + $upstreamPendingAttempts)
        );
        $countedTotal = array_sum($counts);
        $countedEligible = $countedTotal - $counts['skipped'];
        $countsConsistent = $totalAttempts === $countedTotal
            && $observedEligibleAttempts === $countedEligible
            && $eligibleAttempts === $observedEligibleAttempts + $missingTerminalWindows
            && $upstreamPendingAttempts <= $counts['skipped']
            && $endToEndAttempts === $eligibleAttempts + $upstreamPendingAttempts;
        $outcomeRecovery = analytics_normalize_outcome_recovery(
            $rawWindow['outcome_recovery'] ?? null,
            $counts
        );

        $coverageRatio = is_numeric($rawWindow['coverage_ratio'] ?? null)
            ? (float)$rawWindow['coverage_ratio']
            : 0.0;
        if (!is_finite($coverageRatio)) {
            $coverageRatio = 0.0;
        }
        $coverageRatio = round(max(0.0, min(1.0, $coverageRatio)), 4);
        $rawMeasurementStatus = (string)($rawWindow['measurement_status'] ?? '');
        $measurementStatusValid = in_array($rawMeasurementStatus, ['collecting', 'observed'], true);
        $measurementStatus = !$staleCache
            && $rawMeasurementStatus === 'observed'
            && $combinedSloReadiness === 'ready'
            ? 'observed'
            : 'collecting';
        $fullFresh = analytics_reliability_percentage($rawWindow['full_fresh_rate_pct'] ?? null);
        $endToEndFresh = analytics_reliability_percentage(
            $rawWindow['end_to_end_fresh_rate_pct'] ?? $fullFresh
        );
        $expectedEndToEndFresh = $endToEndAttempts === 0
            ? null
            : round($counts['fresh_published'] / $endToEndAttempts * 100, 2);
        $acceptedFresh = analytics_reliability_percentage($rawWindow['accepted_fresh_rate_pct'] ?? null);
        $dataAvailable = analytics_reliability_percentage($rawWindow['data_available_rate_pct'] ?? null);
        $ratesAvailable = $measurementStatusValid
            && $eligibleAttempts > 0
            && $countsConsistent
            && $fullFresh !== null
            && $endToEndFresh !== null
            && $expectedEndToEndFresh !== null
            && abs($endToEndFresh - $expectedEndToEndFresh) <= 0.011
            && $acceptedFresh !== null
            && $dataAvailable !== null;
        $ratesObserved = $measurementStatus === 'observed' && $ratesAvailable;

        $freshnessSlo = analytics_normalize_reliability_slo(
            $rawWindow['freshness_slo'] ?? null,
            $eligibleAttempts,
            $measurementStatus,
            $staleCache
        );
        $endToEndFreshnessSlo = analytics_normalize_reliability_slo(
            $rawWindow['end_to_end_freshness_slo'] ?? null,
            $endToEndAttempts,
            $measurementStatus,
            $staleCache
        );

        $verifiedCompleteness = analytics_normalize_verified_completeness(
            $rawWindow['verified_completeness'] ?? null,
            $eligibleAttempts,
            $countsConsistent && $measurementStatusValid,
            $measurementStatus
        );

        $windows[] = [
            'window' => $window,
            'from_at' => isset($rawWindow['from_at']) ? (string)$rawWindow['from_at'] : null,
            'to_at' => isset($rawWindow['to_at']) ? (string)$rawWindow['to_at'] : null,
            'measurement_status' => $measurementStatus,
            'coverage_ratio' => $coverageRatio,
            'total_attempts' => $totalAttempts,
            'observed_eligible_attempts' => $observedEligibleAttempts,
            'missing_terminal_windows' => $missingTerminalWindows,
            'eligible_attempts' => $eligibleAttempts,
            'upstream_pending_attempts' => $upstreamPendingAttempts,
            'end_to_end_attempts' => $endToEndAttempts,
            'counts' => $counts,
            'outcome_recovery' => $outcomeRecovery,
            'full_fresh_rate_pct' => $fullFresh,
            'end_to_end_fresh_rate_pct' => $endToEndFresh,
            'accepted_fresh_rate_pct' => $acceptedFresh,
            'data_available_rate_pct' => $dataAvailable,
            'rates_available' => $ratesAvailable,
            'rates_observed' => $ratesObserved,
            'freshness_slo' => $freshnessSlo,
            'end_to_end_freshness_slo' => $endToEndFreshnessSlo,
            'parsesunix_rollout' => analytics_normalize_parsesunix_rollout(
                $rawWindow['parsesunix_rollout'] ?? null
            ),
            'verified_completeness' => $verifiedCompleteness,
            'scheduled_reliability' => analytics_normalize_scheduled_reliability(
                $rawWindow['scheduled_reliability'] ?? null,
                $staleCache
            ),
        ];
    }

    if ($windows === []) {
        return $collecting;
    }

    $availableWindowKeys = array_column($windows, 'window');
    return [
        'state' => 'available',
        'message' => null,
        'default_window' => in_array('24h', $availableWindowKeys, true) ? '24h' : $availableWindowKeys[0],
        'generated_at' => isset($data['generated_at']) ? (string)$data['generated_at'] : null,
        'coverage_started_at' => isset($data['coverage_started_at']) ? (string)$data['coverage_started_at'] : null,
        'methodology' => [
            'version' => (string)$methodology['version'],
            'unit' => (string)($methodology['unit'] ?? ''),
            'scope' => (string)$methodology['scope'],
            'completeness' => (string)$methodology['completeness'],
            'limitations' => $limitations,
            'coverage_method' => (string)$methodology['coverage_method'],
            'coverage_scope' => (string)$methodology['coverage_scope'],
            'coverage_cohort_method' => (string)$methodology['coverage_cohort_method'],
            'combined_slo_readiness' => $combinedSloReadiness,
            'eligible_outcomes' => $eligibleOutcomes,
            'excluded_outcomes' => $excludedOutcomes,
            'missing_terminal_method' => (string)$methodology['missing_terminal_method'],
        ],
        'cached' => $cached,
        'stale_cache' => $staleCache,
        'cache_age' => $cacheAge,
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
            (bool)$fetch['stale_cache'],
            isset($fetch['cache_age']) ? (int)$fetch['cache_age'] : null
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
            $cached = !empty($source['serving_cached_dataset'])
                || !empty($status['serving_cached_dataset']);
            $operationallyEnabled = !array_key_exists('operationally_enabled', $source)
                || !empty($source['operationally_enabled']);
            $fetchedAt = isset($source['fetched_at']) ? (string)$source['fetched_at'] : null;
            $age = analytics_source_age_label($fetchedAt);
            $hasCanonicalStale = array_key_exists('stale', $source);
            $isStale = $operationallyEnabled && (
                $hasCanonicalStale
                    ? !empty($source['stale'])
                    : ($age['seconds'] === null || $age['seconds'] >= 259200)
            );
            $staleCount += $isStale ? 1 : 0;
            $state = $operationallyEnabled
                ? ($status['effective_state'] ?? $source['state'] ?? 'unknown')
                : 'disabled';
            if (!$operationallyEnabled) {
                $ageLabel = 'Отключён политикой';
                $ageTone = 'neutral';
            } elseif ($isStale) {
                $ageLabel = 'Устарело · ' . $age['label'];
                $ageTone = 'warning';
            } elseif ($age['seconds'] === null) {
                $ageLabel = 'Актуальность подтверждена';
                $ageTone = 'neutral';
            } else {
                $ageLabel = 'Актуально · ' . $age['label'];
                $ageTone = $age['tone'] === 'good' ? 'good' : 'neutral';
            }
            $rows[] = [
                'source' => $source['source_id'] ?? '',
                'site' => $source['site'] ?? '',
                'category' => $source['category'] ?? '',
                'description' => $source['description'] ?? '',
                'state' => $state,
                'fetched_at' => $fetchedAt,
                'age' => $ageLabel,
                'age_tone' => $ageTone,
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
        $operationalTotal = (int)($payload['operational_total'] ?? $total);
        $ok = (int)($payload['ok_count'] ?? 0);
        $problemCount = max(0, $operationalTotal - $ok);
        return analytics_result_shell($module, $definition, $fetch, [
            ['label' => 'Источников', 'value' => $total],
            ['label' => 'Работают', 'value' => $ok, 'tone' => $ok === $operationalTotal ? 'good' : 'warning'],
            ['label' => 'Проблемных', 'value' => $problemCount, 'tone' => $problemCount === 0 ? 'good' : 'bad'],
            ['label' => 'Устарели', 'value' => $staleCount, 'tone' => $staleCount > 0 ? 'warning' : 'good'],
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
        $fetch = analytics_fetch_json('/v1/battlegrounds/heroes', [
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
    $namesFetch = analytics_fetch_json('/v1/battlegrounds/heroes', ['mode' => 'solo', 'limit' => 500, 'offset' => 0], (int)$definition['ttl']);
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
        'heroes' => ['/v1/battlegrounds/heroes', ['q' => $name, 'limit' => 20]],
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
