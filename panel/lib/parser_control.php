<?php
declare(strict_types=1);

/**
 * Narrow, same-origin bridge between the GitHub-authenticated panel and the
 * local parser control API. The browser never receives the admin API token.
 */

const PANEL_PARSER_CONTROL_TOKEN_PATH = '/var/lib/koloda/db-panel-auth/parser-control.json';
const PANEL_PARSER_CONTROL_BASE_URL = 'http://127.0.0.1:18081';
const PANEL_PARSER_CONTROL_MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const PANEL_PARSER_CONTROL_RUN_LIMIT = 8;
const PANEL_PARSER_CONTROL_RUN_WINDOW = 15 * 60;

function panel_parser_control_token_path(): string
{
    $override = getenv('PANEL_PARSER_CONTROL_TOKEN_PATH');
    if (is_string($override) && trim($override) !== '') {
        return $override;
    }

    return PANEL_PARSER_CONTROL_TOKEN_PATH;
}

function panel_parser_control_config(): ?array
{
    $path = panel_parser_control_token_path();
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
    if (!is_string($token)
        || preg_match('/^khs_v1_[A-Za-z0-9_-]{12}_[A-Za-z0-9_-]{40,64}$/D', $token) !== 1
        || !is_string($tokenId)
        || preg_match('/^[A-Za-z0-9_-]{12}$/D', $tokenId) !== 1
        || !is_string($expiresAt)
        || strtotime($expiresAt) === false
        || strtotime($expiresAt) <= time()) {
        return null;
    }

    if (preg_match('/^khs_v1_([A-Za-z0-9_-]{12})_/', $token, $matches) !== 1
        || !hash_equals($tokenId, $matches[1])) {
        return null;
    }

    return ['token' => $token, 'token_id' => $tokenId, 'expires_at' => $expiresAt];
}

function panel_parser_control_csrf_token(): string
{
    $token = $_SESSION['parser_control_csrf'] ?? null;
    if (!is_string($token) || strlen($token) !== 64) {
        $token = bin2hex(random_bytes(32));
        $_SESSION['parser_control_csrf'] = $token;
    }

    return $token;
}

function panel_parser_control_require_csrf(?string $supplied): void
{
    $expected = panel_parser_control_csrf_token();
    if (!is_string($supplied) || !hash_equals($expected, $supplied)) {
        throw new RuntimeException('Сессия управления парсерами устарела. Обновите страницу.');
    }
}

function panel_parser_control_consume_run_budget(array &$session, ?int $now = null): bool
{
    $now = $now ?? time();
    $windowStart = $now - PANEL_PARSER_CONTROL_RUN_WINDOW;
    $attempts = $session['parser_control_run_attempts'] ?? [];
    if (!is_array($attempts)) {
        $attempts = [];
    }
    $attempts = array_values(array_filter(
        $attempts,
        static fn($attempt): bool => is_int($attempt) && $attempt > $windowStart && $attempt <= $now
    ));
    if (count($attempts) >= PANEL_PARSER_CONTROL_RUN_LIMIT) {
        $session['parser_control_run_attempts'] = $attempts;
        return false;
    }
    $attempts[] = $now;
    $session['parser_control_run_attempts'] = $attempts;

    return true;
}

function panel_parser_control_identifiers($value, int $maximum = 100): array
{
    if (!is_array($value) || $value === [] || count($value) > $maximum) {
        throw new InvalidArgumentException('Выберите хотя бы один источник или раздел.');
    }
    $items = [];
    foreach ($value as $item) {
        if (!is_string($item)
            || preg_match('/^[a-z0-9][a-z0-9._-]{0,119}$/D', $item) !== 1) {
            throw new InvalidArgumentException('В запросе указан неизвестный идентификатор.');
        }
        $items[$item] = true;
    }

    return array_keys($items);
}

function panel_parser_control_run_payload(array $input, string $requestedBy): array
{
    $sourceIds = isset($input['source_ids']) ? panel_parser_control_identifiers($input['source_ids']) : [];
    $sectionIds = isset($input['section_ids']) ? panel_parser_control_identifiers($input['section_ids'], 30) : [];
    if ($sourceIds === [] && $sectionIds === []) {
        throw new InvalidArgumentException('Выберите источник или раздел для запуска.');
    }
    $reason = trim((string)($input['reason'] ?? 'Ручной запуск из панели'));
    if ($reason === '' || mb_strlen($reason, 'UTF-8') > 500) {
        throw new InvalidArgumentException('Причина запуска должна содержать от 1 до 500 символов.');
    }

    return [
        'sourceIds' => $sourceIds,
        'sectionIds' => $sectionIds,
        'requestedBy' => mb_substr($requestedBy, 0, 120, 'UTF-8'),
        'reason' => $reason,
    ];
}

function panel_parser_control_section_payload(array $input, string $updatedBy): array
{
    $sectionId = panel_parser_control_identifiers([$input['section_id'] ?? null], 1)[0];
    $revision = filter_var($input['revision'] ?? null, FILTER_VALIDATE_INT);
    if ($revision === false || $revision < 1) {
        throw new InvalidArgumentException('Версия настроек устарела. Обновите страницу.');
    }
    if (!is_bool($input['enabled'] ?? null)) {
        throw new InvalidArgumentException('Некорректное состояние раздела.');
    }

    return [
        'expectedRevision' => $revision,
        'sections' => [$sectionId => $input['enabled']],
        'updatedBy' => mb_substr($updatedBy, 0, 120, 'UTF-8'),
    ];
}

function panel_parser_control_error_message(int $status, array $payload): string
{
    if ($status === 401 || $status === 403) {
        return 'Служебный токен панели не имеет права управлять парсерами.';
    }
    if ($status === 409) {
        return 'Состояние парсеров уже изменилось. Обновите данные и повторите действие.';
    }
    if ($status === 422) {
        return 'API отклонил параметры запуска. Обновите панель и повторите действие.';
    }
    if ($status === 429) {
        return 'Достигнут лимит операций API. Повторите позже.';
    }

    return 'Сервис управления парсерами временно недоступен.';
}

function panel_parser_control_request(string $method, string $path, ?array $payload = null): array
{
    $method = strtoupper($method);
    $allowed = ($method === 'GET' && $path === '/admin/parser-control')
        || ($method === 'POST' && $path === '/admin/parser-runs')
        || ($method === 'PATCH' && $path === '/admin/parser-control/sections');
    if (!$allowed) {
        throw new InvalidArgumentException('Недопустимая операция управления парсерами.');
    }

    $config = panel_parser_control_config();
    if ($config === null) {
        throw new RuntimeException('Управление парсерами ещё не подключено к панели.');
    }
    $headers = [
        'Accept: application/json',
        'Authorization: Bearer ' . $config['token'],
        'User-Agent: Koloda-Hearthstone-Parser-Panel/1.0',
    ];
    $body = null;
    if ($payload !== null) {
        $body = json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        if (!is_string($body) || strlen($body) > 32 * 1024) {
            throw new InvalidArgumentException('Слишком большой запрос управления парсерами.');
        }
        $headers[] = 'Content-Type: application/json';
    }

    $handle = curl_init(PANEL_PARSER_CONTROL_BASE_URL . $path);
    if ($handle === false) {
        throw new RuntimeException('Не удалось открыть соединение с сервисом парсеров.');
    }
    $options = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CUSTOMREQUEST => $method,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_CONNECTTIMEOUT => 2,
        CURLOPT_TIMEOUT => 15,
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

    if (!is_string($responseBody) || strlen($responseBody) > PANEL_PARSER_CONTROL_MAX_RESPONSE_BYTES) {
        throw new RuntimeException('Сервис парсеров вернул некорректный ответ.');
    }
    $decoded = json_decode($responseBody, true);
    if (!is_array($decoded)) {
        throw new RuntimeException('Сервис парсеров вернул некорректный ответ.');
    }
    if ($status < 200 || $status >= 300) {
        throw new RuntimeException(panel_parser_control_error_message($status, $decoded));
    }

    return $decoded;
}
