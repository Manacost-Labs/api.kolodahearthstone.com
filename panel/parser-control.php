<?php
declare(strict_types=1);

require __DIR__ . '/lib/auth.php';
require __DIR__ . '/lib/parser_control.php';

$panelUser = panel_require_auth(true);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: private, no-store, max-age=0');
header('Pragma: no-cache');
header('X-Content-Type-Options: nosniff');

function parser_panel_json(int $status, array $payload): never
{
    http_response_code($status);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_INVALID_UTF8_SUBSTITUTE);
    exit;
}

$method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
try {
    if ($method === 'GET') {
        parser_panel_json(200, ['ok' => true, 'data' => panel_parser_control_request('GET', '/admin/parser-control')]);
    }
    if ($method !== 'POST') {
        header('Allow: GET, POST');
        parser_panel_json(405, ['ok' => false, 'message' => 'Метод не поддерживается.']);
    }

    $raw = file_get_contents('php://input');
    if (!is_string($raw) || $raw === '' || strlen($raw) > 32 * 1024) {
        throw new InvalidArgumentException('Некорректный запрос панели.');
    }
    $input = json_decode($raw, true, 64, JSON_THROW_ON_ERROR);
    if (!is_array($input)) {
        throw new InvalidArgumentException('Некорректный запрос панели.');
    }
    panel_parser_control_require_csrf($_SERVER['HTTP_X_CSRF_TOKEN'] ?? null);
    $actor = 'github:' . (string)($panelUser['login'] ?? 'admin');
    $action = trim((string)($input['action'] ?? ''));

    if ($action === 'run') {
        if (!panel_parser_control_consume_run_budget($_SESSION)) {
            parser_panel_json(429, ['ok' => false, 'message' => 'Слишком много ручных запусков. Повторите через 15 минут.']);
        }
        $payload = panel_parser_control_run_payload($input, $actor);
        $result = panel_parser_control_request('POST', '/admin/parser-runs', $payload);
        panel_auth_audit('parser_run_requested', [
            'user_id' => $panelUser['id'] ?? 0,
            'source_ids' => $payload['sourceIds'],
            'section_ids' => $payload['sectionIds'],
        ]);
        parser_panel_json(202, ['ok' => true, 'data' => $result]);
    }
    if ($action === 'section') {
        $payload = panel_parser_control_section_payload($input, $actor);
        $result = panel_parser_control_request('PATCH', '/admin/parser-control/sections', $payload);
        panel_auth_audit('parser_section_updated', [
            'user_id' => $panelUser['id'] ?? 0,
            'section_id' => $input['section_id'] ?? '',
            'enabled' => $input['enabled'] ?? false,
        ]);
        parser_panel_json(200, ['ok' => true, 'data' => $result]);
    }

    throw new InvalidArgumentException('Неизвестное действие панели.');
} catch (JsonException | InvalidArgumentException $exception) {
    parser_panel_json(422, ['ok' => false, 'message' => $exception->getMessage()]);
} catch (Throwable $exception) {
    parser_panel_json(502, ['ok' => false, 'message' => $exception->getMessage()]);
}
