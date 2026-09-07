<?php declare(strict_types=1); require_once __DIR__ . '/../lib/panel_shell.php'; ?>
<header class="topbar">
    <div class="topbar-copy">
        <span class="topbar-context"><?= h($workspaceSection) ?></span>
        <div>
            <?php if (in_array($action, ['parsers', 'list'], true)): ?><span class="topbar-title">Управление данными</span>
            <?php else: ?><h1><?= h($workspaceTitle) ?></h1><?php endif; ?>
        </div>
    </div>
    <div class="topbar-actions">
        <button class="topbar-command" type="button" data-command-open aria-label="Быстрый переход" aria-haspopup="dialog" aria-keyshortcuts="Control+K Meta+K">
            <?= panel_icon('search') ?><span>Быстрый переход</span><kbd>Ctrl K</kbd>
        </button>
        <div class="panel-account" aria-label="Аккаунт администратора">
            <span class="panel-account-name" title="<?= h($panelUser['login']) ?>">GitHub · <?= h($panelUser['login']) ?></span>
            <form action="/auth/logout" method="post">
                <input type="hidden" name="csrf" value="<?= h(panel_logout_csrf_token()) ?>">
                <button class="panel-logout" type="submit" aria-label="Выйти из панели">Выйти</button>
            </form>
        </div>
    </div>
</header>
