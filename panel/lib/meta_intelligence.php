<?php
declare(strict_types=1);

const PANEL_META_INTELLIGENCE_TOKEN_PATH = '/var/lib/koloda/db-panel-auth/meta-intelligence.json';
const PANEL_META_INTELLIGENCE_BASE_URL = 'http://127.0.0.1:18081';
const PANEL_META_INTELLIGENCE_MAX_RESPONSE_BYTES = 4 * 1024 * 1024;

final class PanelMetaIntelligenceHttpException extends RuntimeException
{
    private int $httpStatus;

    public function __construct(string $message, int $httpStatus)
    {
        parent::__construct($message);
        $this->httpStatus = $httpStatus;
    }

    public function httpStatus(): int
    {
        return $this->httpStatus;
    }
}

function panel_meta_intelligence_token_path(): string
{
    $override = getenv('PANEL_META_INTELLIGENCE_TOKEN_PATH');
    return is_string($override) && trim($override) !== ''
        ? trim($override)
        : PANEL_META_INTELLIGENCE_TOKEN_PATH;
}

function panel_meta_intelligence_token(): string
{
    $path = panel_meta_intelligence_token_path();
    if (!is_file($path) || !is_readable($path)) {
        throw new RuntimeException('Сервисный доступ Meta Lab не настроен.');
    }
    $permissions = @fileperms($path);
    if (is_int($permissions) && (($permissions & 0777) & 0077) !== 0) {
        throw new RuntimeException('Небезопасные права файла доступа Meta Lab.');
    }
    $raw = file_get_contents($path);
    if (!is_string($raw) || $raw === '' || strlen($raw) > 4096) {
        throw new RuntimeException('Файл доступа Meta Lab повреждён.');
    }
    $config = json_decode($raw, true);
    $token = is_array($config) ? ($config['token'] ?? null) : null;
    if (!is_string($token) || preg_match('/^khs_v1_[A-Za-z0-9_-]{12}_[A-Za-z0-9_-]{40,64}$/D', $token) !== 1) {
        throw new RuntimeException('Сервисный токен Meta Lab имеет неверный формат.');
    }

    return $token;
}

function panel_meta_intelligence_scope(array $input): array
{
    $format = trim((string)($input['format'] ?? 'standard'));
    if (!in_array($format, ['standard', 'wild'], true)) {
        $format = 'standard';
    }
    $scope = ['format' => $format];
    foreach (['rank' => 40, 'period' => 60, 'patch' => 60] as $name => $maximum) {
        $value = trim((string)($input[$name] ?? ($name === 'rank' ? 'legend' : ($name === 'period' ? 'past_week' : 'unknown'))));
        if ($value === '' || strlen($value) > $maximum || preg_match('/^[A-Za-z0-9_.:-]+$/D', $value) !== 1) {
            throw new InvalidArgumentException('Некорректный параметр среза: ' . $name . '.');
        }
        $scope[$name] = $value;
    }

    return $scope;
}

function panel_meta_intelligence_text(array $input, string $name, int $maximum, bool $required = false): string
{
    $value = trim((string)($input[$name] ?? ''));
    if (($required && $value === '') || mb_strlen($value, 'UTF-8') > $maximum || preg_match('/[\x00-\x1F\x7F]/u', $value) === 1) {
        throw new InvalidArgumentException('Некорректный параметр: ' . $name . '.');
    }

    return $value;
}

function panel_meta_intelligence_token_value(array $input, string $name, int $maximum = 128): string
{
    $value = panel_meta_intelligence_text($input, $name, $maximum, true);
    if (preg_match('/^[A-Za-z0-9_.:-]+$/D', $value) !== 1) {
        throw new InvalidArgumentException('Некорректный идентификатор: ' . $name . '.');
    }

    return $value;
}

function panel_meta_intelligence_limit(array $input, string $name, int $default, int $maximum): int
{
    $raw = $input[$name] ?? $default;
    if (filter_var($raw, FILTER_VALIDATE_INT) === false) {
        return $default;
    }

    return max(0, min($maximum, (int)$raw));
}

function panel_meta_intelligence_request(string $route, array $input): array
{
    $route = trim($route);
    if ($route === 'health' || $route === 'budget') {
        return ['/v1/meta-intelligence/' . $route, []];
    }

    if (in_array($route, ['archetypes', 'cards'], true)) {
        $query = panel_meta_intelligence_scope($input);
        $query['q'] = panel_meta_intelligence_text($input, 'q', 120);
        if ($route === 'archetypes') {
            $query['limit'] = max(1, panel_meta_intelligence_limit($input, 'limit', 100, 500));
            $query['offset'] = panel_meta_intelligence_limit($input, 'offset', 0, 10000);
        }
        return ['/v1/meta-intelligence/' . $route, $query];
    }

    if (in_array($route, ['archetype', 'timeline', 'compare'], true)) {
        $archetype = panel_meta_intelligence_text($input, 'archetype', 120, true);
        $path = '/v1/meta-intelligence/archetypes/' . rawurlencode($archetype);
        $query = panel_meta_intelligence_scope($input);
        if ($route === 'timeline') {
            $path .= '/timeline';
            $query['limit'] = max(1, panel_meta_intelligence_limit($input, 'limit', 180, 1000));
        } elseif ($route === 'compare') {
            $path .= '/compare';
            foreach (['left', 'right'] as $name) {
                $value = panel_meta_intelligence_text($input, $name, 64, true);
                if (preg_match('/^[a-f0-9]{64}$/D', $value) !== 1) {
                    throw new InvalidArgumentException('Некорректный идентификатор сборки.');
                }
                $query[$name] = $value;
            }
        }
        return [$path, $query];
    }

    if ($route === 'signals') {
        $query = panel_meta_intelligence_scope($input);
        $state = trim((string)($input['state'] ?? ''));
        if ($state !== '' && !in_array($state, ['observing', 'early', 'confirmed', 'rejected'], true)) {
            throw new InvalidArgumentException('Некорректное состояние сигнала.');
        }
        $kind = panel_meta_intelligence_text($input, 'kind', 40);
        $allowedKinds = ['popularity_rise', 'popularity_drop', 'winrate_shift', 'matchup_shift', 'deck_variant_detected', 'streamer_burst'];
        if ($kind !== '' && !in_array($kind, $allowedKinds, true)) {
            throw new InvalidArgumentException('Некорректный тип сигнала.');
        }
        if ($state !== '') {
            $query['state'] = $state;
        }
        if ($kind !== '') {
            $query['kind'] = $kind;
        }
        $query['limit'] = max(1, panel_meta_intelligence_limit($input, 'limit', 100, 500));
        return ['/v1/meta-intelligence/signals', $query];
    }

    if ($route === 'signal' || $route === 'snapshot') {
        $name = $route === 'signal' ? 'signal_id' : 'snapshot_id';
        $identifier = panel_meta_intelligence_token_value($input, $name);
        $collection = $route === 'signal' ? 'signals' : 'snapshots';
        return ['/v1/meta-intelligence/' . $collection . '/' . rawurlencode($identifier), []];
    }

    throw new InvalidArgumentException('Неизвестный маршрут Meta Lab.');
}

function panel_meta_intelligence_fetch(string $route, array $input): array
{
    [$path, $query] = panel_meta_intelligence_request($route, $input);
    $baseUrl = rtrim((string)(getenv('HS_DATA_API_INTERNAL_URL') ?: PANEL_META_INTELLIGENCE_BASE_URL), '/');
    $url = $baseUrl . $path . ($query ? '?' . http_build_query($query, '', '&', PHP_QUERY_RFC3986) : '');
    $body = '';
    $handle = curl_init($url);
    if ($handle === false) {
        throw new RuntimeException('Не удалось открыть соединение с Meta Lab.');
    }
    curl_setopt_array($handle, [
        CURLOPT_RETURNTRANSFER => false,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 2,
        CURLOPT_TIMEOUT => 15,
        CURLOPT_HTTPHEADER => [
            'Accept: application/json',
            'X-API-Key: ' . panel_meta_intelligence_token(),
        ],
        CURLOPT_USERAGENT => 'api.kolodahearthstone.com-panel-meta-lab/1.0',
        CURLOPT_WRITEFUNCTION => static function ($curl, string $chunk) use (&$body): int {
            if (strlen($body) + strlen($chunk) > PANEL_META_INTELLIGENCE_MAX_RESPONSE_BYTES) {
                return 0;
            }
            $body .= $chunk;
            return strlen($chunk);
        },
    ]);
    $ok = curl_exec($handle);
    $status = (int)curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
    $error = curl_error($handle);
    curl_close($handle);

    if ($ok === false || $status < 200 || $status >= 300) {
        $safeStatus = in_array($status, [401, 403, 404, 422, 429, 503], true) ? $status : 502;
        $message = $status === 404
            ? 'Для выбранного среза данных пока нет.'
            : ($status === 503 ? 'Meta Lab временно недоступен.' : 'Не удалось загрузить данные Meta Lab.');
        if ($status === 0 && $error !== '') {
            $message = 'Сервис Meta Lab не отвечает.';
        }
        throw new PanelMetaIntelligenceHttpException($message, $safeStatus);
    }

    $payload = json_decode($body, true);
    if (!is_array($payload)) {
        throw new RuntimeException('Meta Lab вернул неожиданный формат данных.');
    }

    return $payload;
}
