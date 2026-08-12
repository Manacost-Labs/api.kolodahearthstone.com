<?php
declare(strict_types=1);

const PANEL_API_TOKEN_CONFIG_PATH = '/var/lib/koloda/db-panel-auth/api-token-manager.json';
const PANEL_API_TOKEN_BASE_URL = 'http://127.0.0.1:18081';
const PANEL_API_TOKEN_ISSUE_LIMIT = 10;
const PANEL_API_TOKEN_ISSUE_WINDOW = 15 * 60;
const PANEL_API_TOKEN_MAX_RESPONSE_BYTES = 256 * 1024;

function panel_api_token_config_path(): string
{
    $override = getenv('PANEL_API_TOKEN_CONFIG_PATH');
    if (is_string($override) && trim($override) !== '') {
        return $override;
    }

    return PANEL_API_TOKEN_CONFIG_PATH;
}

function panel_api_token_scope_catalog(): array
{
    return [
        'database:read' => [
            'label' => 'Чтение полной базы',
            'description' => 'Доступ к collections и records в GraphQL.',
        ],
        'admin' => [
            'label' => 'Управление API',
            'description' => 'Запуск обновлений и закрытые служебные endpoints.',
        ],
        'tokens:manage' => [
            'label' => 'Управление токенами',
            'description' => 'Выпуск, просмотр и отзыв других API-токенов.',
        ],
    ];
}

function panel_api_token_id_is_valid($tokenId): bool
{
    return is_string($tokenId) && preg_match('/^[A-Za-z0-9_-]{12}$/D', $tokenId) === 1;
}

function panel_api_token_value_is_valid($token): bool
{
    return is_string($token)
        && preg_match('/^khs_v1_[A-Za-z0-9_-]{12}_[A-Za-z0-9_-]{40,64}$/D', $token) === 1;
}

function panel_api_token_id_from_value(string $token): ?string
{
    if (preg_match('/^khs_v1_([A-Za-z0-9_-]{12})_[A-Za-z0-9_-]{40,64}$/D', $token, $matches) !== 1) {
        return null;
    }

    return $matches[1];
}

function panel_api_token_manager_config(): ?array
{
    $path = panel_api_token_config_path();
    if (!is_file($path) || !is_readable($path)) {
        return null;
    }

    $permissions = @fileperms($path);
    if (is_int($permissions) && (($permissions & 0777) & 0077) !== 0) {
        return null;
    }

    $raw = file_get_contents($path);
    if (!is_string($raw) || $raw === '' || strlen($raw) > 4096) {
        return null;
    }

    $config = json_decode($raw, true);
    if (!is_array($config)) {
        return null;
    }

    $token = $config['token'] ?? null;
    $tokenId = $config['token_id'] ?? null;
    $expiresAt = $config['expires_at'] ?? null;
    if (!panel_api_token_value_is_valid($token)
        || !panel_api_token_id_is_valid($tokenId)
        || !is_string($expiresAt)
        || strlen($expiresAt) > 64
        || strtotime($expiresAt) === false) {
        return null;
    }

    $embeddedTokenId = panel_api_token_id_from_value($token);
    if ($embeddedTokenId === null || !hash_equals($tokenId, $embeddedTokenId)) {
        return null;
    }

    return [
        'token' => $token,
        'token_id' => $tokenId,
        'expires_at' => $expiresAt,
    ];
}

function panel_api_token_normalize_issue_input(array $input): array
{
    $name = trim((string)($input['name'] ?? ''));
    if ($name === ''
        || mb_strlen($name, 'UTF-8') > 80
        || preg_match('/[\x00-\x1F\x7F]/u', $name) === 1) {
        throw new InvalidArgumentException('Укажите название длиной до 80 символов.');
    }

    $rawScopes = $input['scopes'] ?? null;
    if (!is_array($rawScopes)) {
        throw new InvalidArgumentException('Выберите хотя бы одно право доступа.');
    }
    $allowedScopes = array_keys(panel_api_token_scope_catalog());
    $scopes = [];
    foreach ($rawScopes as $scope) {
        if (!is_string($scope) || !in_array($scope, $allowedScopes, true)) {
            throw new InvalidArgumentException('Выбрано неизвестное право доступа.');
        }
        $scopes[$scope] = true;
    }
    $scopes = array_keys($scopes);
    sort($scopes, SORT_STRING);
    if ($scopes === []) {
        throw new InvalidArgumentException('Выберите хотя бы одно право доступа.');
    }

    $expiresInDays = filter_var($input['expires_in_days'] ?? null, FILTER_VALIDATE_INT);
    if ($expiresInDays === false || $expiresInDays < 1 || $expiresInDays > 365) {
        throw new InvalidArgumentException('Срок токена должен быть от 1 до 365 дней.');
    }
    $rateLimit = filter_var(
        $input['rate_limit_per_minute'] ?? 600,
        FILTER_VALIDATE_INT
    );
    if ($rateLimit === false || $rateLimit < 1 || $rateLimit > 100000) {
        throw new InvalidArgumentException('Минутный лимит должен быть от 1 до 100 000 запросов.');
    }
    $monthlyQuota = filter_var(
        $input['monthly_quota'] ?? 1000000,
        FILTER_VALIDATE_INT
    );
    if ($monthlyQuota === false || $monthlyQuota < 1 || $monthlyQuota > 1000000000) {
        throw new InvalidArgumentException('Месячная квота должна быть от 1 до 1 000 000 000 запросов.');
    }

    return [
        'name' => $name,
        'scopes' => $scopes,
        'expires_in_days' => $expiresInDays,
        'rate_limit_per_minute' => $rateLimit,
        'monthly_quota' => $monthlyQuota,
    ];
}

function panel_api_token_consume_issue_budget(array &$session, ?int $now = null): bool
{
    $now = $now ?? time();
    $windowStart = $now - PANEL_API_TOKEN_ISSUE_WINDOW;
    $attempts = $session['panel_api_token_issue_attempts'] ?? [];
    if (!is_array($attempts)) {
        $attempts = [];
    }
    $attempts = array_values(array_filter($attempts, static function ($attempt) use ($windowStart, $now): bool {
        return is_int($attempt) && $attempt > $windowStart && $attempt <= $now;
    }));
    if (count($attempts) >= PANEL_API_TOKEN_ISSUE_LIMIT) {
        $session['panel_api_token_issue_attempts'] = $attempts;
        return false;
    }

    $attempts[] = $now;
    $session['panel_api_token_issue_attempts'] = $attempts;
    return true;
}

function panel_api_token_error_message(int $status, array $payload): string
{
    $detail = $payload['detail'] ?? null;
    $code = is_array($detail) ? (string)($detail['code'] ?? '') : '';
    $messages = [
        'INVALID_NAME' => 'Проверьте название токена.',
        'INVALID_SCOPES' => 'Проверьте выбранные права доступа.',
        'INVALID_EXPIRY' => 'Проверьте срок действия токена.',
        'INVALID_RATE_LIMIT' => 'Проверьте минутный лимит токена.',
        'INVALID_MONTHLY_QUOTA' => 'Проверьте месячную квоту токена.',
        'TOKEN_NOT_FOUND' => 'Токен уже удалён или не существует.',
        'TOKEN_REVOKED' => 'Служебный токен панели был отозван.',
        'TOKEN_EXPIRED' => 'Служебный токен панели истёк.',
        'INSUFFICIENT_SCOPE' => 'У панели нет права управлять токенами.',
    ];
    if (isset($messages[$code])) {
        return $messages[$code];
    }
    if ($status === 401 || $status === 403) {
        return 'Панель не авторизована для управления токенами.';
    }
    if ($status === 429) {
        return 'Слишком много операций. Повторите через несколько минут.';
    }

    return 'Сервис токенов временно недоступен.';
}

function panel_api_token_request(string $method, string $path, ?array $payload = null): array
{
    $method = strtoupper($method);
    $allowedPath = $path === '/admin/api-tokens'
        || $path === '/v1/auth/token'
        || preg_match('#^/admin/api-tokens/[A-Za-z0-9_-]{12}$#D', $path) === 1;
    if (!in_array($method, ['GET', 'POST', 'DELETE'], true) || !$allowedPath) {
        throw new InvalidArgumentException('Недопустимая операция с API-токеном.');
    }

    $manager = panel_api_token_manager_config();
    if ($manager === null) {
        throw new RuntimeException('Управление токенами ещё не подключено к панели.');
    }

    $headers = [
        'Accept: application/json',
        'Authorization: Bearer ' . $manager['token'],
        'User-Agent: Koloda-Hearthstone-Token-Panel/1.0',
    ];
    $body = null;
    if ($payload !== null) {
        $body = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if (!is_string($body) || strlen($body) > 16 * 1024) {
            throw new InvalidArgumentException('Некорректные данные токена.');
        }
        $headers[] = 'Content-Type: application/json';
    }

    $handle = curl_init(PANEL_API_TOKEN_BASE_URL . $path);
    if ($handle === false) {
        throw new RuntimeException('Сервис токенов временно недоступен.');
    }
    $options = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CUSTOMREQUEST => $method,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_CONNECTTIMEOUT => 2,
        CURLOPT_TIMEOUT => 12,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_MAXREDIRS => 0,
        CURLOPT_NOSIGNAL => true,
        CURLOPT_PROTOCOLS => CURLPROTO_HTTP,
    ];
    if ($body !== null) {
        $options[CURLOPT_POSTFIELDS] = $body;
    }
    curl_setopt_array($handle, $options);
    $responseBody = curl_exec($handle);
    $status = (int)curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
    curl_close($handle);

    if (!is_string($responseBody) || strlen($responseBody) > PANEL_API_TOKEN_MAX_RESPONSE_BYTES) {
        throw new RuntimeException('Сервис токенов вернул некорректный ответ.');
    }
    if ($status === 204) {
        return ['status' => $status, 'data' => []];
    }

    $decoded = json_decode($responseBody, true);
    if (!is_array($decoded)) {
        throw new RuntimeException('Сервис токенов вернул некорректный ответ.');
    }
    if ($status < 200 || $status >= 300) {
        throw new RuntimeException(panel_api_token_error_message($status, $decoded));
    }

    return ['status' => $status, 'data' => $decoded];
}

function panel_api_token_metadata(array $raw): array
{
    $tokenId = $raw['id'] ?? null;
    $name = $raw['name'] ?? null;
    $scopes = $raw['scopes'] ?? null;
    $createdAt = $raw['created_at'] ?? null;
    $expiresAt = $raw['expires_at'] ?? null;
    $rateLimit = filter_var($raw['rate_limit_per_minute'] ?? null, FILTER_VALIDATE_INT);
    $monthlyQuota = filter_var($raw['monthly_quota'] ?? null, FILTER_VALIDATE_INT);
    $usage = $raw['usage'] ?? null;
    if (!panel_api_token_id_is_valid($tokenId)
        || !is_string($name)
        || $name === ''
        || !is_array($scopes)
        || !is_string($createdAt)
        || strtotime($createdAt) === false
        || !is_string($expiresAt)
        || strtotime($expiresAt) === false
        || $rateLimit === false
        || $rateLimit < 1
        || $monthlyQuota === false
        || $monthlyQuota < 1
        || !is_array($usage)) {
        throw new RuntimeException('Сервис токенов вернул некорректные метаданные.');
    }
    $usageCount = filter_var($usage['request_count'] ?? null, FILTER_VALIDATE_INT);
    $usageErrors = filter_var($usage['error_count'] ?? null, FILTER_VALIDATE_INT);
    $usageBytes = filter_var($usage['response_bytes'] ?? null, FILTER_VALIDATE_INT);
    $usageMonth = $usage['month'] ?? null;
    if ($usageCount === false || $usageCount < 0
        || $usageErrors === false || $usageErrors < 0
        || $usageBytes === false || $usageBytes < 0
        || !is_string($usageMonth)
        || preg_match('/^\d{4}-(?:0[1-9]|1[0-2])$/D', $usageMonth) !== 1) {
        throw new RuntimeException('Сервис токенов вернул некорректную статистику использования.');
    }

    $allowedScopes = array_keys(panel_api_token_scope_catalog());
    foreach ($scopes as $scope) {
        if (!is_string($scope) || !in_array($scope, $allowedScopes, true)) {
            throw new RuntimeException('Сервис токенов вернул неизвестное право доступа.');
        }
    }

    return [
        'id' => $tokenId,
        'name' => $name,
        'scopes' => array_values(array_unique($scopes)),
        'created_at' => $createdAt,
        'expires_at' => $expiresAt,
        'last_used_at' => is_string($raw['last_used_at'] ?? null) ? $raw['last_used_at'] : null,
        'revoked_at' => is_string($raw['revoked_at'] ?? null) ? $raw['revoked_at'] : null,
        'created_by' => is_string($raw['created_by'] ?? null) ? $raw['created_by'] : '',
        'revoked_by' => is_string($raw['revoked_by'] ?? null) ? $raw['revoked_by'] : null,
        'rate_limit_per_minute' => $rateLimit,
        'monthly_quota' => $monthlyQuota,
        'usage' => [
            'month' => $usageMonth,
            'request_count' => $usageCount,
            'error_count' => $usageErrors,
            'response_bytes' => $usageBytes,
            'last_request_at' => is_string($usage['last_request_at'] ?? null)
                ? $usage['last_request_at']
                : null,
        ],
    ];
}

function panel_api_token_list(): array
{
    $response = panel_api_token_request('GET', '/admin/api-tokens');
    $rows = $response['data']['data'] ?? null;
    if (!is_array($rows)) {
        throw new RuntimeException('Сервис токенов вернул некорректный список.');
    }

    $tokens = [];
    foreach ($rows as $row) {
        if (!is_array($row) || array_key_exists('token', $row)) {
            throw new RuntimeException('Список токенов содержит секретные или некорректные данные.');
        }
        $tokens[] = panel_api_token_metadata($row);
    }
    return $tokens;
}

function panel_api_token_issue(array $input): array
{
    $payload = panel_api_token_normalize_issue_input($input);
    $response = panel_api_token_request('POST', '/admin/api-tokens', $payload);
    $raw = $response['data']['data'] ?? null;
    if (!is_array($raw) || !panel_api_token_value_is_valid($raw['token'] ?? null)) {
        throw new RuntimeException('Сервис не вернул секрет нового токена.');
    }

    $metadata = panel_api_token_metadata($raw);
    $embeddedTokenId = panel_api_token_id_from_value($raw['token']);
    if ($embeddedTokenId === null || !hash_equals($metadata['id'], $embeddedTokenId)) {
        throw new RuntimeException('Сервис вернул несогласованный токен.');
    }

    $metadata['token'] = $raw['token'];
    return $metadata;
}

function panel_api_token_revoke(string $tokenId): void
{
    if (!panel_api_token_id_is_valid($tokenId)) {
        throw new InvalidArgumentException('Некорректный идентификатор токена.');
    }
    $manager = panel_api_token_manager_config();
    if ($manager === null) {
        throw new RuntimeException('Управление токенами ещё не подключено к панели.');
    }
    if (hash_equals($manager['token_id'], $tokenId)) {
        throw new InvalidArgumentException('Служебный токен панели нельзя отозвать из этой панели.');
    }

    panel_api_token_request('DELETE', '/admin/api-tokens/' . rawurlencode($tokenId));
}
