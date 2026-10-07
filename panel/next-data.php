<?php
declare(strict_types=1);

// Authenticated adapter during the incremental frontend migration.
// This endpoint accepts only the existing panel's fixed actions.
$method = strtoupper((string)($_SERVER['REQUEST_METHOD'] ?? 'GET'));
$rawAction = $_POST['action'] ?? $_GET['action'] ?? 'list';
$action = is_string($rawAction) ? $rawAction : '';
$allowed = $method === 'GET'
    ? ['session', 'list', 'edit', 'new', 'api_tokens', 'wiki_terms', 'analytics_registry']
    : ($method === 'POST' ? ['save', 'delete', 'issue_api_token', 'revoke_api_token', 'save_wiki_terms'] : []);
require __DIR__ . '/lib/auth.php';
require __DIR__ . '/lib/next_panel.php';
$user = panel_require_auth(true);
if (!in_array($action, $allowed, true)) {
    http_response_code(405);
    header('Allow: GET, POST');
    panel_next_json(['ok' => false, 'message' => 'Действие не поддерживается.']);
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
