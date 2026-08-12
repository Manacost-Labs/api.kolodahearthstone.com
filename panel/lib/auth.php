<?php
declare(strict_types=1);

const PANEL_AUTH_ALLOWED_LOGIN = 'Zulut30';
const PANEL_AUTH_ALLOWED_USER_ID = 243011385;
const PANEL_AUTH_IDLE_TTL = 12 * 60 * 60;
const PANEL_AUTH_ABSOLUTE_TTL = 7 * 24 * 60 * 60;
const PANEL_AUTH_STATE_TTL = 10 * 60;
const PANEL_AUTH_SETUP_STATE_TTL = 60 * 60;
const PANEL_AUTH_CONFIG_PATH = '/var/lib/koloda/db-panel-auth/github-auth.json';
const PANEL_AUTH_CALLBACK_URL = 'https://api.kolodahearthstone.com/auth/github/callback';
const PANEL_AUTH_SETUP_CALLBACK_URL = 'https://api.kolodahearthstone.com/auth/github/setup/callback';

function panel_auth_config_path(): string
{
    $override = getenv('PANEL_AUTH_CONFIG_PATH');
    if (is_string($override) && trim($override) !== '') {
        return $override;
    }

    return PANEL_AUTH_CONFIG_PATH;
}

function panel_start_session(): void
{
    if (session_status() === PHP_SESSION_ACTIVE) {
        return;
    }

    ini_set('session.use_only_cookies', '1');
    ini_set('session.use_strict_mode', '1');
    ini_set('session.cookie_httponly', '1');
    ini_set('session.gc_maxlifetime', (string)PANEL_AUTH_ABSOLUTE_TTL);
    session_name('koloda_admin');
    session_set_cookie_params([
        'lifetime' => PANEL_AUTH_ABSOLUTE_TTL,
        'path' => '/',
        'secure' => true,
        'httponly' => true,
        'samesite' => 'Lax',
    ]);

    if (!session_start()) {
        throw new RuntimeException('Не удалось открыть защищённую сессию.');
    }
}

function panel_security_headers(bool $noStore = true): void
{
    header('X-Content-Type-Options: nosniff');
    header('X-Frame-Options: DENY');
    header('Referrer-Policy: no-referrer');
    header('Permissions-Policy: camera=(), microphone=(), geolocation=()');
    header("Content-Security-Policy: default-src 'self'; base-uri 'none'; frame-ancestors 'none'; object-src 'none'; form-action 'self' https://github.com; img-src 'self' data: https:; font-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'");
    if ($noStore) {
        header('Cache-Control: private, no-store, max-age=0');
        header('Pragma: no-cache');
    }
}

function panel_html($value): string
{
    return htmlspecialchars((string)$value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function panel_safe_return_to($candidate): string
{
    if (!is_string($candidate) || $candidate === '' || preg_match('/[\x00-\x1F\x7F]/', $candidate)) {
        return '/';
    }

    if ($candidate[0] !== '/' || strpos($candidate, '//') === 0 || strpos($candidate, '\\') !== false) {
        return '/';
    }

    $parts = parse_url($candidate);
    if (!is_array($parts) || isset($parts['scheme']) || isset($parts['host']) || isset($parts['user'])) {
        return '/';
    }

    $path = (string)($parts['path'] ?? '');
    if ($path !== '/' && $path !== '/index.php') {
        return '/';
    }

    return $candidate;
}

function panel_random_token(int $bytes = 32): string
{
    return rtrim(strtr(base64_encode(random_bytes($bytes)), '+/', '-_'), '=');
}

function panel_issue_state(array &$session, string $key, ?int $now = null): string
{
    $now = $now ?? time();
    $state = panel_random_token();
    if (!isset($session['panel_states']) || !is_array($session['panel_states'])) {
        $session['panel_states'] = [];
    }
    $session['panel_states'][$key] = [
        'hash' => hash('sha256', $state),
        'created_at' => $now,
    ];

    return $state;
}

function panel_consume_state(array &$session, string $key, $provided, ?int $now = null, int $ttl = PANEL_AUTH_STATE_TTL): bool
{
    $now = $now ?? time();
    $stored = $session['panel_states'][$key] ?? null;
    if (!is_array($stored) || !is_string($provided) || $provided === '') {
        return false;
    }

    $createdAt = (int)($stored['created_at'] ?? 0);
    $hash = (string)($stored['hash'] ?? '');
    if ($createdAt <= 0 || $createdAt > $now || ($now - $createdAt) > $ttl) {
        unset($session['panel_states'][$key]);
        return false;
    }

    if ($hash === '' || !hash_equals($hash, hash('sha256', $provided))) {
        return false;
    }

    unset($session['panel_states'][$key]);
    return true;
}

function panel_user_is_allowed(array $user): bool
{
    $id = filter_var($user['id'] ?? null, FILTER_VALIDATE_INT);
    $login = $user['login'] ?? null;

    return $id === PANEL_AUTH_ALLOWED_USER_ID
        && is_string($login)
        && strcasecmp($login, PANEL_AUTH_ALLOWED_LOGIN) === 0;
}

function panel_authenticated_user(array &$session, ?int $now = null): ?array
{
    $now = $now ?? time();
    $auth = $session['panel_auth'] ?? null;
    if (!is_array($auth) || !panel_user_is_allowed($auth)) {
        unset($session['panel_auth']);
        return null;
    }

    $authenticatedAt = (int)($auth['authenticated_at'] ?? 0);
    $lastSeenAt = (int)($auth['last_seen_at'] ?? 0);
    $invalidTime = $authenticatedAt <= 0
        || $lastSeenAt < $authenticatedAt
        || $authenticatedAt > $now
        || $lastSeenAt > $now;
    $expired = ($now - $lastSeenAt) > PANEL_AUTH_IDLE_TTL
        || ($now - $authenticatedAt) > PANEL_AUTH_ABSOLUTE_TTL;
    if ($invalidTime || $expired) {
        unset($session['panel_auth']);
        return null;
    }

    $session['panel_auth']['last_seen_at'] = $now;
    return $session['panel_auth'];
}

function panel_sign_in(array &$session, array $user, ?int $now = null): void
{
    if (!panel_user_is_allowed($user)) {
        throw new RuntimeException('Доступ для этого аккаунта не разрешён.');
    }

    $now = $now ?? time();
    $session['panel_auth'] = [
        'id' => PANEL_AUTH_ALLOWED_USER_ID,
        'login' => PANEL_AUTH_ALLOWED_LOGIN,
        'authenticated_at' => $now,
        'last_seen_at' => $now,
    ];
}

function panel_current_user(): ?array
{
    panel_start_session();
    return panel_authenticated_user($_SESSION);
}

function panel_require_auth(bool $json = false): array
{
    panel_start_session();
    panel_security_headers(true);
    $user = panel_authenticated_user($_SESSION);
    if ($user !== null) {
        return $user;
    }

    if ($json) {
        http_response_code(401);
        header('Content-Type: application/json; charset=utf-8');
        echo json_encode(['ok' => false, 'message' => 'Требуется вход через GitHub.'], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
        exit;
    }

    $_SESSION['panel_return_to'] = panel_safe_return_to($_SERVER['REQUEST_URI'] ?? '/');
    header('Location: /auth/github', true, 302);
    exit;
}

function panel_take_return_to(): string
{
    panel_start_session();
    $returnTo = panel_safe_return_to($_SESSION['panel_return_to'] ?? '/');
    unset($_SESSION['panel_return_to']);
    return $returnTo;
}

function panel_auth_config(): ?array
{
    $path = panel_auth_config_path();
    if (!is_file($path) || !is_readable($path)) {
        return null;
    }

    $raw = file_get_contents($path);
    if (!is_string($raw) || $raw === '') {
        return null;
    }

    $config = json_decode($raw, true);
    if (!is_array($config)) {
        return null;
    }

    $clientId = $config['client_id'] ?? null;
    $clientSecret = $config['client_secret'] ?? null;
    if (!is_string($clientId) || !preg_match('/^[A-Za-z0-9._-]{10,128}$/', $clientId)) {
        return null;
    }
    if (!is_string($clientSecret) || strlen($clientSecret) < 20 || strlen($clientSecret) > 255) {
        return null;
    }

    return [
        'client_id' => $clientId,
        'client_secret' => $clientSecret,
        'app_id' => (int)($config['app_id'] ?? 0),
        'app_slug' => is_string($config['app_slug'] ?? null) ? $config['app_slug'] : '',
    ];
}

function panel_write_auth_config(array $app): void
{
    $path = panel_auth_config_path();
    $directory = dirname($path);
    if (!is_dir($directory) || !is_writable($directory)) {
        throw new RuntimeException('Каталог конфигурации GitHub недоступен для записи.');
    }
    if (file_exists($path)) {
        throw new RuntimeException('GitHub-вход уже настроен.');
    }

    $payload = [
        'client_id' => (string)($app['client_id'] ?? ''),
        'client_secret' => (string)($app['client_secret'] ?? ''),
        'app_id' => (int)($app['app_id'] ?? 0),
        'app_slug' => (string)($app['app_slug'] ?? ''),
        'created_at' => gmdate('c'),
    ];
    if (!preg_match('/^[A-Za-z0-9._-]{10,128}$/', $payload['client_id'])
        || strlen($payload['client_secret']) < 20
        || strlen($payload['client_secret']) > 255
        || $payload['app_id'] <= 0) {
        throw new RuntimeException('GitHub вернул неполную конфигурацию приложения.');
    }

    $json = json_encode($payload, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
    if (!is_string($json)) {
        throw new RuntimeException('Не удалось подготовить конфигурацию GitHub.');
    }

    $previousUmask = umask(0077);
    $handle = @fopen($path, 'x');
    umask($previousUmask);
    if (!is_resource($handle)) {
        throw new RuntimeException('Не удалось создать конфигурацию GitHub.');
    }

    $written = false;
    try {
        if (flock($handle, LOCK_EX)) {
            $bytes = fwrite($handle, $json . "\n");
            fflush($handle);
            flock($handle, LOCK_UN);
            $written = $bytes === strlen($json) + 1;
        }
    } finally {
        fclose($handle);
    }

    if (!$written) {
        @unlink($path);
        throw new RuntimeException('Не удалось сохранить конфигурацию GitHub.');
    }
    @chmod($path, 0600);
}

function panel_github_json(string $url, string $method = 'GET', array $form = [], string $bearer = ''): array
{
    $parts = parse_url($url);
    $allowedHosts = ['github.com', 'api.github.com'];
    if (!is_array($parts)
        || ($parts['scheme'] ?? '') !== 'https'
        || !in_array($parts['host'] ?? '', $allowedHosts, true)) {
        throw new RuntimeException('Недопустимый адрес GitHub API.');
    }

    $headers = [
        'Accept: application/json',
        'User-Agent: Koloda-Hearthstone-Admin/1.0',
        'X-GitHub-Api-Version: 2022-11-28',
    ];
    if ($bearer !== '') {
        $headers[] = 'Authorization: Bearer ' . $bearer;
    }

    $curl = curl_init($url);
    if ($curl === false) {
        throw new RuntimeException('Не удалось открыть соединение с GitHub.');
    }

    $options = [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 5,
        CURLOPT_TIMEOUT => 12,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_SSL_VERIFYPEER => true,
        CURLOPT_SSL_VERIFYHOST => 2,
    ];
    if (defined('CURLOPT_PROTOCOLS')) {
        $options[CURLOPT_PROTOCOLS] = CURLPROTO_HTTPS;
    }
    if ($method === 'POST') {
        $options[CURLOPT_POST] = true;
        $options[CURLOPT_POSTFIELDS] = http_build_query($form, '', '&', PHP_QUERY_RFC3986);
        $headers[] = 'Content-Type: application/x-www-form-urlencoded';
        $options[CURLOPT_HTTPHEADER] = $headers;
    }
    curl_setopt_array($curl, $options);

    $body = curl_exec($curl);
    $status = (int)curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
    $failed = $body === false;
    curl_close($curl);
    if ($failed || $status < 200 || $status >= 300 || !is_string($body)) {
        throw new RuntimeException('GitHub временно не подтвердил вход.');
    }

    $decoded = json_decode($body, true);
    if (!is_array($decoded)) {
        throw new RuntimeException('GitHub вернул некорректный ответ.');
    }

    return $decoded;
}

function panel_github_user_from_code(string $code, array $config): array
{
    if (!preg_match('/^[A-Za-z0-9_-]{8,255}$/', $code)) {
        throw new RuntimeException('Некорректный одноразовый код GitHub.');
    }

    $tokenResponse = panel_github_json('https://github.com/login/oauth/access_token', 'POST', [
        'client_id' => $config['client_id'],
        'client_secret' => $config['client_secret'],
        'code' => $code,
        'redirect_uri' => PANEL_AUTH_CALLBACK_URL,
    ]);
    $accessToken = $tokenResponse['access_token'] ?? null;
    if (!is_string($accessToken) || $accessToken === '') {
        throw new RuntimeException('GitHub не выдал одноразовый токен входа.');
    }

    try {
        $user = panel_github_json('https://api.github.com/user', 'GET', [], $accessToken);
    } finally {
        $accessToken = '';
        unset($tokenResponse);
    }

    return [
        'id' => (int)($user['id'] ?? 0),
        'login' => (string)($user['login'] ?? ''),
    ];
}

function panel_github_app_from_manifest_code(string $code): array
{
    if (!preg_match('/^[A-Za-z0-9_-]{8,255}$/', $code)) {
        throw new RuntimeException('Некорректный код настройки GitHub.');
    }

    $response = panel_github_json(
        'https://api.github.com/app-manifests/' . rawurlencode($code) . '/conversions',
        'POST'
    );
    $owner = is_array($response['owner'] ?? null) ? $response['owner'] : [];

    return [
        'owner' => [
            'id' => (int)($owner['id'] ?? 0),
            'login' => (string)($owner['login'] ?? ''),
        ],
        'client_id' => (string)($response['client_id'] ?? ''),
        'client_secret' => (string)($response['client_secret'] ?? ''),
        'app_id' => (int)($response['id'] ?? 0),
        'app_slug' => (string)($response['slug'] ?? ''),
    ];
}

function panel_github_manifest(): array
{
    return [
        'name' => 'Koloda HS Admin Zulut30',
        'url' => 'https://api.kolodahearthstone.com/',
        'description' => 'Private sign-in for the Koloda Hearthstone database panel.',
        'hook_attributes' => [
            'url' => 'https://api.kolodahearthstone.com/auth/github/webhook',
            'active' => false,
        ],
        'redirect_url' => PANEL_AUTH_SETUP_CALLBACK_URL,
        'callback_urls' => [PANEL_AUTH_CALLBACK_URL],
        'public' => false,
        'default_events' => [],
        'default_permissions' => (object)[],
        'request_oauth_on_install' => false,
    ];
}

function panel_logout_csrf_token(): string
{
    panel_start_session();
    if (!is_string($_SESSION['panel_logout_csrf'] ?? null) || $_SESSION['panel_logout_csrf'] === '') {
        $_SESSION['panel_logout_csrf'] = panel_random_token();
    }
    return $_SESSION['panel_logout_csrf'];
}

function panel_logout_csrf_is_valid($provided): bool
{
    panel_start_session();
    $expected = $_SESSION['panel_logout_csrf'] ?? null;
    return is_string($expected)
        && is_string($provided)
        && $provided !== ''
        && hash_equals($expected, $provided);
}

function panel_destroy_session(): void
{
    panel_start_session();
    $_SESSION = [];
    if (ini_get('session.use_cookies')) {
        $parameters = session_get_cookie_params();
        setcookie(session_name(), '', [
            'expires' => time() - 42000,
            'path' => $parameters['path'],
            'secure' => $parameters['secure'],
            'httponly' => $parameters['httponly'],
            'samesite' => 'Lax',
        ]);
    }
    session_destroy();
}

function panel_auth_audit(string $event, array $details = []): void
{
    $safeEvent = preg_replace('/[^a-z0-9_.-]/i', '_', $event);
    $record = [
        'event' => $safeEvent,
        'time' => gmdate('c'),
    ];
    if (isset($details['user_id'])) {
        $record['user_id'] = (int)$details['user_id'];
    }
    error_log('koloda_panel_auth ' . json_encode($record, JSON_UNESCAPED_SLASHES));
}

function panel_render_auth_page(string $title, string $message, string $bodyHtml = '', int $status = 200): void
{
    http_response_code($status);
    header('Content-Type: text/html; charset=utf-8');
    panel_security_headers();
    ?>
<!doctype html>
<html lang="ru" data-theme="dark">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex,nofollow">
    <title><?= panel_html($title) ?> · HS Data</title>
    <style>
        :root { color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }
        * { box-sizing: border-box; }
        body { margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 24px; color: #eef4ff; background: radial-gradient(circle at 50% 0%, #17356a 0, #0b152b 42%, #070d1b 100%); }
        .card { width: min(100%, 460px); padding: 34px; border: 1px solid rgba(147, 180, 255, .24); border-radius: 24px; background: rgba(12, 24, 48, .9); box-shadow: 0 24px 70px rgba(0, 0, 0, .38); backdrop-filter: blur(18px); }
        .mark { width: 52px; height: 52px; display: grid; place-items: center; margin-bottom: 24px; border-radius: 16px; color: white; font-weight: 850; letter-spacing: -.04em; background: linear-gradient(135deg, #3b82f6, #7c3aed); box-shadow: 0 12px 30px rgba(59, 130, 246, .28); }
        h1 { margin: 0 0 12px; font-size: clamp(25px, 7vw, 34px); line-height: 1.08; letter-spacing: -.035em; }
        p { margin: 0; color: #aebedb; line-height: 1.65; }
        .actions { display: grid; gap: 12px; margin-top: 26px; }
        .button { display: flex; min-height: 48px; align-items: center; justify-content: center; gap: 10px; padding: 12px 18px; border: 0; border-radius: 13px; color: #0b1020; background: #f5f7fb; font: inherit; font-weight: 750; text-decoration: none; cursor: pointer; }
        .button:hover { background: white; transform: translateY(-1px); }
        .button.secondary { color: #e8efff; border: 1px solid rgba(147, 180, 255, .24); background: rgba(255, 255, 255, .06); }
        .note { display: flex; gap: 10px; margin-top: 24px; padding-top: 20px; border-top: 1px solid rgba(147, 180, 255, .15); color: #8498bb; font-size: 13px; line-height: 1.5; }
        .dot { flex: 0 0 auto; width: 8px; height: 8px; margin-top: 6px; border-radius: 50%; background: #34d399; box-shadow: 0 0 14px rgba(52, 211, 153, .7); }
        form { margin: 0; }
    </style>
</head>
<body>
    <main class="card">
        <div class="mark" aria-hidden="true">HS</div>
        <h1><?= panel_html($title) ?></h1>
        <p><?= panel_html($message) ?></p>
        <?php if ($bodyHtml !== ''): ?>
            <div class="actions"><?= $bodyHtml ?></div>
        <?php endif; ?>
        <div class="note"><span class="dot" aria-hidden="true"></span><span>Доступ разрешён только GitHub-аккаунту <strong><?= panel_html(PANEL_AUTH_ALLOWED_LOGIN) ?></strong>. Репозитории и личные данные не запрашиваются.</span></div>
    </main>
</body>
</html>
    <?php
}
