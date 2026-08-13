<?php
declare(strict_types=1);

$tokenScopeCatalog = panel_api_token_scope_catalog();
$tokenManagerId = is_array($apiTokenManagerConfig) ? (string)$apiTokenManagerConfig['token_id'] : '';
$tokenNow = time();
$tokenActiveCount = 0;
$tokenRevokedCount = 0;
foreach ($apiTokens as $tokenSummary) {
    $isRevoked = !empty($tokenSummary['revoked_at']);
    $isExpired = !$isRevoked && strtotime((string)$tokenSummary['expires_at']) <= $tokenNow;
    if ($isRevoked) {
        $tokenRevokedCount++;
    } elseif (!$isExpired) {
        $tokenActiveCount++;
    }
}
$tokenFormatDate = static function ($value): string {
    if (!is_string($value) || $value === '' || strtotime($value) === false) {
        return '—';
    }
    return gmdate('d.m.Y H:i', (int)strtotime($value)) . ' UTC';
};
$tokenFormName = $issuedApiToken === null ? trim((string)($_POST['name'] ?? '')) : '';
$tokenFormScopes = $issuedApiToken === null && is_array($_POST['scopes'] ?? null)
    ? $_POST['scopes']
    : ['database:read'];
$tokenFormExpiry = $issuedApiToken === null ? (string)($_POST['expires_in_days'] ?? '90') : '90';
$tokenFormRate = $issuedApiToken === null ? (string)($_POST['rate_limit_per_minute'] ?? '600') : '600';
$tokenFormQuota = $issuedApiToken === null ? (string)($_POST['monthly_quota'] ?? '1000000') : '1000000';
?>
<section class="token-workspace" data-token-page aria-labelledby="api-token-heading">
    <header class="token-page-head">
        <div>
            <span class="eyebrow">Доступ к api.kolodahearthstone.com/v1</span>
            <h2 id="api-token-heading">Управление API-токенами</h2>
            <p>Выпускайте отдельный токен для каждого сервиса и выдавайте только необходимые права.</p>
        </div>
        <?php if ($apiTokenManagerConfig !== null): ?>
            <span class="token-connection is-ready"><i aria-hidden="true"></i>Подключено</span>
        <?php else: ?>
            <span class="token-connection is-error"><i aria-hidden="true"></i>Не настроено</span>
        <?php endif; ?>
    </header>

    <?php if ($issuedApiToken !== null): ?>
        <section class="token-secret-card" aria-labelledby="new-token-heading" role="status" aria-live="polite">
            <div class="token-secret-copy">
                <span class="eyebrow">Показывается один раз</span>
                <h3 id="new-token-heading">Скопируйте новый токен</h3>
                <p>После ухода со страницы восстановить секрет нельзя. При потере отзовите токен и выпустите новый.</p>
            </div>
            <div class="token-secret-value">
                <code data-token-secret tabindex="0"><?= h($issuedApiToken['token']) ?></code>
                <button class="button" type="button" data-copy-token>Копировать</button>
            </div>
            <p class="token-copy-status" data-copy-token-status aria-live="polite"></p>
        </section>
    <?php endif; ?>

    <?php if ($apiTokenManagerConfig === null): ?>
        <div class="token-empty-state" role="alert">
            <h3>Панель ещё не подключена к управлению токенами</h3>
            <p>Нужен отдельный серверный токен со scope <code>tokens:manage</code>. Он хранится вне сайта и никогда не передаётся браузеру.</p>
        </div>
    <?php else: ?>
        <div class="token-layout">
            <section class="panel token-issue-panel" aria-labelledby="issue-token-heading">
                <div class="token-section-head">
                    <div>
                        <span class="eyebrow">Новый доступ</span>
                        <h3 id="issue-token-heading">Выпустить токен</h3>
                    </div>
                    <span class="token-manager-expiry">Ключ панели до <?= h($tokenFormatDate($apiTokenManagerConfig['expires_at'])) ?></span>
                </div>

                <form method="post" action="/?action=api_tokens" class="token-issue-form">
                    <input type="hidden" name="csrf" value="<?= h(csrf()) ?>">
                    <input type="hidden" name="form_nonce" value="<?= h($apiTokenIssueNonce) ?>">
                    <input type="hidden" name="action" value="issue_api_token">

                    <label class="token-field">
                        <span>Название</span>
                        <input name="name" value="<?= h($tokenFormName) ?>" maxlength="80" required autocomplete="off" placeholder="Например, telegram-bot">
                        <small>Один понятный токен на один сервис.</small>
                    </label>

                    <fieldset class="token-scopes">
                        <legend>Права доступа</legend>
                        <?php foreach ($tokenScopeCatalog as $scope => $scopeInfo): ?>
                            <label class="token-scope-option">
                                <input type="checkbox" name="scopes[]" value="<?= h($scope) ?>"<?= in_array($scope, $tokenFormScopes, true) ? ' checked' : '' ?>>
                                <span>
                                    <b><?= h($scopeInfo['label']) ?></b>
                                    <code><?= h($scope) ?></code>
                                    <small><?= h($scopeInfo['description']) ?></small>
                                </span>
                            </label>
                        <?php endforeach; ?>
                    </fieldset>

                    <label class="token-field token-expiry-field">
                        <span>Срок действия</span>
                        <select name="expires_in_days" required>
                            <?php foreach ([30 => '30 дней', 90 => '90 дней', 180 => '180 дней', 365 => '1 год'] as $days => $daysLabel): ?>
                                <option value="<?= $days ?>"<?= $tokenFormExpiry === (string)$days ? ' selected' : '' ?>><?= h($daysLabel) ?></option>
                            <?php endforeach; ?>
                        </select>
                    </label>

                    <label class="token-field">
                        <span>Запросов в минуту</span>
                        <input type="number" name="rate_limit_per_minute" value="<?= h($tokenFormRate) ?>" min="1" max="100000" required inputmode="numeric">
                        <small>Защищает API от резких всплесков нагрузки.</small>
                    </label>

                    <label class="token-field">
                        <span>Запросов в месяц</span>
                        <input type="number" name="monthly_quota" value="<?= h($tokenFormQuota) ?>" min="1" max="1000000000" required inputmode="numeric">
                        <small>Общая квота токена, обнуляется в начале месяца UTC.</small>
                    </label>

                    <div class="token-form-actions">
                        <button class="button" type="submit">Выпустить токен</button>
                        <p>Секрет появится только в следующем ответе и не сохраняется панелью.</p>
                    </div>
                </form>
            </section>

            <aside class="token-guidance" aria-labelledby="token-guidance-heading">
                <h3 id="token-guidance-heading">Как выбирать права</h3>
                <dl>
                    <div><dt>Интеграция читает базу</dt><dd><code>database:read</code></dd></div>
                    <div><dt>Запускает обновления</dt><dd><code>admin</code></dd></div>
                    <div><dt>Выпускает другие токены</dt><dd><code>tokens:manage</code></dd></div>
                </dl>
                <p>Не выдавайте <code>admin</code> или <code>tokens:manage</code> обычному клиентскому приложению.</p>
            </aside>
        </div>

        <section class="panel token-list-panel" aria-labelledby="token-list-heading">
            <div class="token-section-head">
                <div>
                    <span class="eyebrow">Реестр доступа</span>
                    <h3 id="token-list-heading">Выпущенные токены</h3>
                </div>
                <div class="token-list-actions">
                    <div class="token-summary" aria-label="Состояние токенов">
                        <span><b><?= $tokenActiveCount ?></b> активных</span>
                        <span><b><?= $tokenRevokedCount ?></b> отозвано</span>
                    </div>
                    <details class="table-column-picker" data-column-picker data-table-target=".token-table" data-storage-key="api-tokens">
                        <summary>Колонки</summary>
                        <div class="column-picker-menu" data-column-picker-menu></div>
                    </details>
                </div>
            </div>

            <?php if ($apiTokenLoadError !== ''): ?>
                <div class="token-inline-error" role="alert"><?= h($apiTokenLoadError) ?></div>
            <?php elseif ($apiTokens === []): ?>
                <div class="token-empty-state" role="status">
                    <h3>Пока нет выпущенных токенов</h3>
                    <p>Создайте первый токен в форме выше.</p>
                </div>
            <?php else: ?>
                <?php $tableNavigationTarget = '.token-table-shell'; $tableNavigationLabel = 'Реестр токенов'; require __DIR__ . '/table-navigation.php'; ?>
                <div class="token-table-shell" tabindex="0" aria-label="Таблица API-токенов, доступна горизонтальная прокрутка">
                    <table class="token-table">
                        <thead>
                        <tr>
                            <th>Название</th>
                            <th>Права</th>
                            <th>Создан</th>
                            <th>Истекает</th>
                            <th>Последнее использование</th>
                            <th>Лимиты</th>
                            <th>Использование за месяц</th>
                            <th>Статус</th>
                            <th>Действие</th>
                        </tr>
                        </thead>
                        <tbody>
                        <?php foreach ($apiTokens as $token): ?>
                            <?php
                            $isManagerToken = $tokenManagerId !== '' && hash_equals($tokenManagerId, (string)$token['id']);
                            $isRevokedToken = !empty($token['revoked_at']);
                            $isExpiredToken = !$isRevokedToken && strtotime((string)$token['expires_at']) <= $tokenNow;
                            $tokenStatusClass = $isRevokedToken || $isExpiredToken ? 'is-inactive' : 'is-active';
                            $tokenStatusLabel = $isRevokedToken ? 'Отозван' : ($isExpiredToken ? 'Истёк' : 'Активен');
                            ?>
                            <tr>
                                <td>
                                    <strong><?= h($token['name']) ?></strong>
                                    <code class="token-id"><?= h($token['id']) ?></code>
                                    <?php if ($isManagerToken): ?><span class="token-manager-badge">Токен панели</span><?php endif; ?>
                                </td>
                                <td><div class="token-scope-list"><?php foreach ($token['scopes'] as $scope): ?><code><?= h($scope) ?></code><?php endforeach; ?></div></td>
                                <td><?= h($tokenFormatDate($token['created_at'])) ?></td>
                                <td><?= h($tokenFormatDate($token['expires_at'])) ?></td>
                                <td><?= h($tokenFormatDate($token['last_used_at'])) ?></td>
                                <td>
                                    <strong><?= h(number_format((int)$token['rate_limit_per_minute'], 0, ',', ' ')) ?>/мин</strong>
                                    <small><?= h(number_format((int)$token['monthly_quota'], 0, ',', ' ')) ?>/мес</small>
                                </td>
                                <td>
                                    <strong><?= h(number_format((int)$token['usage']['request_count'], 0, ',', ' ')) ?> запросов</strong>
                                    <small><?= h(number_format((int)$token['usage']['error_count'], 0, ',', ' ')) ?> ошибок · <?= h($token['usage']['month']) ?></small>
                                </td>
                                <td><span class="token-status <?= h($tokenStatusClass) ?>"><i aria-hidden="true"></i><?= h($tokenStatusLabel) ?></span></td>
                                <td>
                                    <?php if ($isManagerToken): ?>
                                        <button class="button ghost token-revoke" type="button" disabled>Служебный</button>
                                    <?php elseif ($isRevokedToken): ?>
                                        <button class="button ghost token-revoke" type="button" disabled>Отозван</button>
                                    <?php else: ?>
                                        <form method="post" action="/?action=api_tokens" onsubmit="return confirm('Отозвать этот токен? Доступ закроется сразу.');">
                                            <input type="hidden" name="csrf" value="<?= h(csrf()) ?>">
                                            <input type="hidden" name="action" value="revoke_api_token">
                                            <input type="hidden" name="token_id" value="<?= h($token['id']) ?>">
                                            <button class="button ghost token-revoke" type="submit" aria-label="Отозвать токен <?= h($token['name']) ?>">Отозвать</button>
                                        </form>
                                    <?php endif; ?>
                                </td>
                            </tr>
                        <?php endforeach; ?>
                        </tbody>
                    </table>
                </div>
            <?php endif; ?>
        </section>
    <?php endif; ?>
</section>
