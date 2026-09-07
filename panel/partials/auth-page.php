<?php declare(strict_types=1); ?>
<!doctype html>
<html lang="ru" data-theme="light">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="robots" content="noindex,nofollow">
    <title><?= panel_html($title) ?> · HS Data</title>
    <link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='6' fill='%23175cff'/%3E%3C/svg%3E">
    <link rel="stylesheet" href="/assets/auth.css?v=1">
    <script src="/assets/workspace.js?v=1" defer></script>
</head>
<body>
    <header class="auth-brand"><span class="auth-mark" aria-hidden="true">HS</span><span>HS Data<small>Панель управления</small></span></header>
    <main class="auth-card" aria-labelledby="auth-title">
        <span class="auth-caption"><?= $status >= 400 ? 'Действие не выполнено' : 'Защищённый доступ' ?></span>
        <h1 id="auth-title"><?= panel_html($title) ?></h1>
        <p class="auth-message"><?= panel_html($message) ?></p>
        <?php if ($bodyHtml !== ''): ?>
            <?php // Trusted action markup assembled by auth route handlers, not user HTML. ?>
            <div class="auth-actions"><?= $bodyHtml ?></div>
        <?php endif; ?>
        <div class="auth-note">
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6Z"/><path d="m8 12 3 3 5-6"/></svg>
            <p>Доступ разрешён только GitHub-аккаунту <strong><?= panel_html(PANEL_AUTH_ALLOWED_LOGIN) ?></strong>. Репозитории и личные данные не запрашиваются.</p>
        </div>
    </main>
    <footer class="auth-footer">api.kolodahearthstone.com</footer>
</body>
</html>
