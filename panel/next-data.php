<?php
declare(strict_types=1);

// Authenticated adapter during the incremental frontend migration.
// This endpoint accepts only the existing panel's fixed actions.
$method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
$rawAction = $_POST['action'] ?? $_GET['action'] ?? 'list';
$action = is_string($rawAction) ? $rawAction : '';
$allowed = $method === 'GET'
    ? ['session', 'list', 'edit', 'new', 'api_tokens', 'wiki_terms', 'analytics_registry', 'card_history']
    : ($method === 'POST' ? ['save', 'delete', 'issue_api_token', 'revoke_api_token', 'save_wiki_terms'] : []);
require __DIR__ . '/lib/auth.php';
require __DIR__ . '/lib/next_panel.php';
$user = panel_require_auth(true);
if (!in_array($action, $allowed, true)) {
    http_response_code(405);
    header('Allow: GET, POST');
    panel_next_json(['ok' => false, 'message' => 'Действие не поддерживается.']);
}
if ($action === 'card_history') {
    require_once __DIR__ . '/lib/card_history.php';
    $config = require __DIR__ . '/config.php';
    $pdo = new PDO($config['db']['dsn'], $config['db']['user'], $config['db']['password'], [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
    try {
        $entity = (string)($_GET['entity'] ?? '');
        $id = (string)($_GET['card_id'] ?? '');
        $legacy = ($_GET['legacy'] ?? '') === '1';
        if ($legacy && $entity !== 'card') throw new InvalidArgumentException('Legacy BG only');
        $history = $legacy
            ? catalog_legacy_bg_history($pdo, $id, (int)($_GET['page'] ?? 1), 20)
            : catalog_card_history($pdo, $entity, $id, (int)($_GET['page'] ?? 1), 20);
        panel_next_json(['ok' => true, 'data' => $history]);
    } catch (InvalidArgumentException $e) {
        http_response_code(400);
        panel_next_json(['ok' => false, 'message' => 'Некорректный идентификатор истории.']);
    } catch (PDOException $e) {
        if (($e->errorInfo[0] ?? '') !== '42S02') throw $e;
        http_response_code(503);
        panel_next_json(['ok' => false, 'message' => 'Запись истории ещё не установлена.']);
    }
}
if ($action === 'session') {
    require __DIR__ . '/lib/parser_control.php';
    panel_next_json(['ok' => true, 'user' => ['id' => (int)$user['id'], 'login' => (string)$user['login']], 'logoutCsrf' => panel_logout_csrf_token(), 'parserCsrf' => panel_parser_control_csrf_token()]);
}
if ($action === 'analytics_registry') {
    require __DIR__ . '/lib/analytics.php';
    $modules = [];
    foreach (analytics_module_registry() as $key => $definition) {
        $modules[$key] = array_intersect_key($definition, array_flip(['title', 'description', 'params']));
    }
    panel_next_json(['ok' => true, 'modules' => $modules]);
}
define('KOLODA_NEXT_JSON', true);
// index.php owns the established SQL queries, validation and mutation budgets.
require __DIR__ . '/index.php';
