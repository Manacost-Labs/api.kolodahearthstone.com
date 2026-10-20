<?php
declare(strict_types=1);

require_once __DIR__ . '/auth.php';
require __DIR__ . '/api_tokens.php';
require __DIR__ . '/parser_control.php';
require_once __DIR__ . '/../scripts/lib/battleground_card_overrides.php';

$panelUser = panel_require_auth(true);

$config = require __DIR__ . '/../config.php';

function db(array $config): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) {
        return $pdo;
    }

    $pdo = new PDO($config['db']['dsn'], $config['db']['user'], $config['db']['password'], [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);

    return $pdo;
}

function panel_absolute_asset_url($url, $version = null): ?string
{
    $url = trim((string)$url);
    if ($url === '') {
        return null;
    }
    if (!preg_match('~^https?://~i', $url)) {
        $url = 'https://api.kolodahearthstone.com/' . ltrim($url, '/');
    }

    $version = trim((string)$version);
    if ($version !== '') {
        $url .= (strpos($url, '?') === false ? '?' : '&') . 'v=' . rawurlencode($version);
    }

    return $url;
}

function panel_attach_horizontal_art(PDO $pdo, array $rows, string $entityType, callable $entityId): array
{
    if (!$rows) {
        return [];
    }

    $ids = [];
    foreach ($rows as $row) {
        $id = trim((string)$entityId($row));
        if ($id !== '') {
            $ids[$id] = true;
        }
    }
    if (!$ids) {
        return $rows;
    }

    $params = ['entity_type' => $entityType];
    $placeholders = [];
    foreach (array_keys($ids) as $index => $id) {
        $name = 'horizontal_panel_id_' . $index;
        $placeholders[] = ':' . $name;
        $params[$name] = $id;
    }

    try {
        $stmt = $pdo->prepare(
            'SELECT entity_id, local_image_url, generated_at '
            . 'FROM horizontal_art_assets '
            . "WHERE status = 'ready' AND entity_type = :entity_type "
            . 'AND entity_id IN (' . implode(',', $placeholders) . ')'
        );
        $stmt->execute($params);
        $assets = [];
        foreach ($stmt->fetchAll() as $asset) {
            $assets[(string)$asset['entity_id']] = panel_absolute_asset_url(
                $asset['local_image_url'] ?? null,
                $asset['generated_at'] ?? null
            );
        }
    } catch (Throwable $e) {
        // The catalogue remains usable while an older database is being migrated.
        $assets = [];
    }

    foreach ($rows as &$row) {
        $id = trim((string)$entityId($row));
        $row['horizontal_image_url'] = $assets[$id] ?? null;
    }
    unset($row);

    return $rows;
}

function csrf(): string
{
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
    }

    return $_SESSION['csrf'];
}

function require_csrf(): void
{
    $sent = $_POST['csrf'] ?? '';
    if (!is_string($sent) || !hash_equals(csrf(), $sent)) {
        throw new RuntimeException('Сессия формы устарела. Обновите страницу и отправьте карту еще раз.');
    }
}

function int_or_null($value): ?int
{
    if ($value === null || $value === '') {
        return null;
    }

    return (int)$value;
}

function creature_types(): array
{
    return [
        'aberration' => 'Аберрация',
        'all' => 'Общие',
        'undead' => 'Нежить',
        'dragon' => 'Дракон',
        'mech' => 'Механизм',
        'murloc' => 'Мурлок',
        'demon' => 'Демон',
        'quilboar' => 'Свинобраз',
        'naga' => 'Нага',
        'pirate' => 'Пират',
        'beast' => 'Зверь',
        'elemental' => 'Элементаль',
    ];
}

function card_types(): array
{
    return [
        'minion' => 'Существо',
        'spell' => 'Заклинание',
    ];
}

function filter_card_types(): array
{
    return card_types() + ['hero' => 'Герой', 'hero_skin' => 'Скины героев', 'pet' => 'Питомцы', 'coin' => 'Монетки', 'timewarped' => 'Хрономальные', 'constructed' => 'Стандарт/Вольный'] + library_types();
}

function constructed_format_label(string $format): string
{
    return [
        'standard' => 'Стандартный',
        'wild' => 'Вольный',
        'all' => 'Стандартный и Вольный',
    ][$format] ?? $format;
}

function library_types(): array
{
    return [
        'anomaly' => 'Аномалии',
        'quest' => 'Квесты',
        'darkmoon_prize' => 'Призы Ярмарки',
        'reward' => 'Награды',
        'trinket' => 'Аксессуары',
    ];
}

function library_type_label($type): string
{
    $types = library_types();
    $type = (string)$type;

    return $types[$type] ?? $type;
}

function card_type_or_default($value): string
{
    $value = trim((string)$value);
    if ($value === '') {
        return 'minion';
    }
    if (!array_key_exists($value, card_types())) {
        throw new RuntimeException('Неверный тип карты.');
    }

    return $value;
}

function creature_type_label($type): string
{
    $types = creature_types();
    $type = (string)$type;

    return $types[$type] ?? '';
}

function creature_type_or_null($value): ?string
{
    $value = trim((string)$value);
    if ($value === '') {
        return null;
    }
    if (!array_key_exists($value, creature_types())) {
        throw new RuntimeException('Неверный тип существа.');
    }

    return $value;
}

function ensure_upload_dirs(array $config): void
{
    foreach (['cards', 'golden', 'art', 'framed'] as $dir) {
        $path = $config['upload_dir'] . '/' . $dir;
        if (!is_dir($path)) {
            mkdir($path, 0755, true);
        }
    }
}

function upload_image(array $config, string $field, string $kind, ?string $current): ?string
{
    if (empty($_FILES[$field]) || !is_array($_FILES[$field]) || ($_FILES[$field]['error'] ?? UPLOAD_ERR_NO_FILE) === UPLOAD_ERR_NO_FILE) {
        return $current;
    }

    $file = $_FILES[$field];
    $error = (int)($file['error'] ?? UPLOAD_ERR_OK);
    if ($error !== UPLOAD_ERR_OK) {
        $messages = [
            UPLOAD_ERR_INI_SIZE => 'Файл слишком большой для текущего лимита PHP.',
            UPLOAD_ERR_FORM_SIZE => 'Файл слишком большой для формы.',
            UPLOAD_ERR_PARTIAL => 'Файл загрузился не полностью. Попробуйте еще раз.',
            UPLOAD_ERR_NO_TMP_DIR => 'На сервере не найдена временная папка для загрузок.',
            UPLOAD_ERR_CANT_WRITE => 'Сервер не смог записать временный файл.',
            UPLOAD_ERR_EXTENSION => 'PHP-расширение остановило загрузку файла.',
        ];
        throw new RuntimeException($messages[$error] ?? 'Не удалось загрузить файл.');
    }
    if (($file['size'] ?? 0) > $config['max_upload_bytes']) {
        throw new RuntimeException('Файл слишком большой. Максимум 16 МБ.');
    }

    $tmp = (string)$file['tmp_name'];
    $info = @getimagesize($tmp);
    if (!$info || empty($info['mime'])) {
        throw new RuntimeException('Файл должен быть изображением.');
    }

    $extensions = [
        'image/png' => 'png',
        'image/jpeg' => 'jpg',
        'image/webp' => 'webp',
    ];
    if (!isset($extensions[$info['mime']])) {
        throw new RuntimeException('Поддерживаются только PNG, JPG и WEBP.');
    }

    ensure_upload_dirs($config);
    $name = date('Ymd-His') . '-' . bin2hex(random_bytes(6)) . '.' . $extensions[$info['mime']];
    $relative = '/' . $kind . '/' . $name;
    $target = $config['upload_dir'] . $relative;
    if (!move_uploaded_file($tmp, $target)) {
        if (!is_writable(dirname($target))) {
            throw new RuntimeException('Папка загрузок недоступна для записи.');
        }
        throw new RuntimeException('Не удалось сохранить файл.');
    }
    chmod($target, 0644);

    return $config['upload_url'] . $relative;
}

function find_card(PDO $pdo, int $id): ?array
{
    $stmt = $pdo->prepare('SELECT * FROM battlegrounds_cards WHERE id = ?');
    $stmt->execute([$id]);
    $row = $stmt->fetch();

    return $row ?: null;
}

function json_array($value, array $default = []): array
{
    if ($value === null || $value === '') {
        return $default;
    }
    if (is_array($value)) {
        return $value;
    }

    $decoded = json_decode((string)$value, true);

    return is_array($decoded) ? $decoded : $default;
}

function compact_text($value): string
{
    if ($value === null || is_bool($value)) {
        return $value === true ? 'true' : ($value === false ? 'false' : '');
    }
    if (is_scalar($value)) {
        return trim((string)$value);
    }
    if (!is_array($value)) {
        return '';
    }

    $parts = [];
    foreach ($value as $item) {
        $text = compact_text($item);
        if ($text !== '') {
            $parts[] = $text;
        }
    }

    return implode(' ', $parts);
}

function ensure_wiki_term_schema(PDO $pdo): void
{
    $pdo->exec("
        CREATE TABLE IF NOT EXISTS battlegrounds_wiki_term_translations (
            term_type VARCHAR(32) NOT NULL,
            term_en VARCHAR(255) NOT NULL,
            term_ru VARCHAR(255) DEFAULT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            PRIMARY KEY (term_type, term_en),
            KEY idx_term_type (term_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    ");
    try {
        $pdo->exec("
            ALTER TABLE battlegrounds_wiki_term_translations
            MODIFY term_en VARCHAR(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL
        ");
    } catch (Throwable $e) {
        // Older MySQL variants can reject no-op collation changes; the table still works.
    }
}

function wiki_term_type_labels(): array
{
    return [
        'mechanic' => 'Wiki mechanics',
        'tag' => 'Wiki tags',
        'full_tag' => 'Full tags',
    ];
}

function collect_wiki_terms_from_column(PDO $pdo, string $table, string $column, string $termType, array &$seen, ?string $where = null): void
{
    $whereSql = $where ? ' WHERE ' . $where : '';
    try {
        $rows = $pdo->query("SELECT `$column` AS terms_json FROM `$table`$whereSql")->fetchAll();
    } catch (Throwable $e) {
        return;
    }

    foreach ($rows as $row) {
        foreach (json_array($row['terms_json'] ?? null) as $term) {
            $term = trim((string)$term);
            if ($term !== '') {
                $seen[$termType][$term] = true;
            }
        }
    }
}

function sync_wiki_terms(PDO $pdo): void
{
    ensure_wiki_term_schema($pdo);
    $seen = array_fill_keys(array_keys(wiki_term_type_labels()), []);
    collect_wiki_terms_from_column($pdo, 'battlegrounds_card_wiki_meta', 'wiki_mechanics_json', 'mechanic', $seen, "status = 'ok'");
    collect_wiki_terms_from_column($pdo, 'battlegrounds_card_wiki_meta', 'wiki_tags_json', 'tag', $seen, "status = 'ok'");
    collect_wiki_terms_from_column($pdo, 'battlegrounds_timewarped_cards', 'wiki_mechanics_json', 'mechanic', $seen, "status = 'ok'");
    collect_wiki_terms_from_column($pdo, 'battlegrounds_timewarped_cards', 'wiki_tags_json', 'tag', $seen, "status = 'ok'");
    collect_wiki_terms_from_column($pdo, 'battlegrounds_timewarped_cards', 'full_tags_json', 'full_tag', $seen, "status = 'ok'");
    collect_wiki_terms_from_column($pdo, 'constructed_card_wiki_meta', 'wiki_mechanics_json', 'mechanic', $seen, "status = 'ok'");
    collect_wiki_terms_from_column($pdo, 'constructed_card_wiki_meta', 'wiki_tags_json', 'tag', $seen, "status = 'ok'");

    $stmt = $pdo->prepare("
        INSERT IGNORE INTO battlegrounds_wiki_term_translations (term_type, term_en)
        VALUES (:term_type, :term_en)
    ");
    foreach ($seen as $type => $terms) {
        foreach (array_keys($terms) as $term) {
            $stmt->execute([
                'term_type' => $type,
                'term_en' => $term,
            ]);
        }
    }
}

function wiki_term_groups(PDO $pdo): array
{
    sync_wiki_terms($pdo);
    $groups = array_fill_keys(array_keys(wiki_term_type_labels()), []);
    $stmt = $pdo->query("
        SELECT term_type, term_en, term_ru, updated_at
        FROM battlegrounds_wiki_term_translations
        ORDER BY term_type, term_en
    ");
    foreach ($stmt->fetchAll() as $row) {
        $type = (string)$row['term_type'];
        if (!isset($groups[$type])) {
            $groups[$type] = [];
        }
        $groups[$type][] = $row;
    }

    return $groups;
}

function load_wiki_meta_map(PDO $pdo, array $cards): array
{
    $cardIds = array_values(array_unique(array_filter(array_map(
        static fn(array $card): string => (string)($card['card_id'] ?? ''),
        $cards
    ))));
    if (!$cardIds) {
        return [];
    }

    $placeholders = implode(',', array_fill(0, count($cardIds), '?'));
    $stmt = $pdo->prepare('SELECT * FROM battlegrounds_card_wiki_meta WHERE card_id IN (' . $placeholders . ')');
    $stmt->execute($cardIds);

    $variants = panel_attach_horizontal_art(
        $pdo,
        $stmt->fetchAll(),
        'battleground_card',
        static fn(array $row): string => (string)$row['card_id']
    );
    $map = [];
    foreach ($variants as $row) {
        $map[(string)$row['card_id']] = $row;
    }

    return $map;
}

/**
 * Return the official golden/tripled row for every base card on the current page.
 * Golden cards remain separate database records because their stats and text can
 * differ, but the admin catalogue presents them as variants of the base card.
 */
function load_golden_variant_map(PDO $pdo, array $cards): array
{
    $baseDbfs = array_values(array_unique(array_filter(array_map(
        static fn(array $card): ?int => $card['dbf'] !== null ? (int)$card['dbf'] : null,
        $cards
    ))));
    if (!$baseDbfs) {
        return [];
    }

    $placeholders = implode(',', array_fill(0, count($baseDbfs), '?'));
    $stmt = $pdo->prepare(
        "SELECT * FROM battlegrounds_cards WHERE variant_kind = 'golden' AND base_dbf IN ($placeholders)"
    );
    $stmt->execute($baseDbfs);

    $map = [];
    foreach ($stmt->fetchAll() as $row) {
        $map[(int)$row['base_dbf']] = $row;
    }
    return $map;
}

function load_constructed_wiki_meta_map(PDO $pdo, array $cards): array
{
    $cardIds = array_values(array_unique(array_filter(array_map(
        static fn(array $card): string => (string)($card['card_id'] ?? ''),
        $cards
    ))));
    if (!$cardIds) {
        return [];
    }

    $placeholders = implode(',', array_fill(0, count($cardIds), '?'));
    $stmt = $pdo->prepare('SELECT * FROM constructed_card_wiki_meta WHERE card_id IN (' . $placeholders . ')');
    $stmt->execute($cardIds);

    $map = [];
    foreach ($stmt->fetchAll() as $row) {
        $map[(string)$row['card_id']] = $row;
    }

    return $map;
}

function load_constructed_related_card_map(PDO $pdo, array $wikiMetaMap): array
{
    $cardIds = [];
    foreach ($wikiMetaMap as $meta) {
        foreach (json_array($meta['related_card_ids_json'] ?? null) as $cardId) {
            $cardId = trim((string)$cardId);
            if ($cardId !== '') {
                $cardIds[$cardId] = true;
            }
        }
    }
    if (!$cardIds) {
        return [];
    }

    $ids = array_keys($cardIds);
    $placeholders = implode(',', array_fill(0, count($ids), '?'));
    $stmt = $pdo->prepare('SELECT * FROM constructed_cards WHERE card_id IN (' . $placeholders . ')');
    $stmt->execute($ids);

    $map = [];
    foreach ($stmt->fetchAll() as $row) {
        $map[(string)$row['card_id']] = $row;
    }

    return $map;
}

function bind_statement_params(PDOStatement $stmt, array $params): void
{
    foreach ($params as $key => $value) {
        $stmt->bindValue(':' . $key, $value, is_int($value) ? PDO::PARAM_INT : PDO::PARAM_STR);
    }
}

function query_url(array $overrides = []): string
{
    $query = $_GET;
    unset($query['action'], $query['id']);
    $query = array_merge($query, $overrides);

    foreach ($query as $key => $value) {
        if ($value === null || $value === '' || ($key === 'page' && (int)$value <= 1)) {
            unset($query[$key]);
        }
    }

    $queryString = http_build_query($query);

    return '/' . ($queryString === '' ? '' : '?' . $queryString);
}

$pdo = db($config);
$action = $_POST['action'] ?? $_GET['action'] ?? 'list';
$message = '';
$error = '';
$issuedApiToken = null;
$apiTokens = [];
$apiTokenLoadError = '';
$apiTokenManagerConfig = null;
$apiTokenIssueNonce = '';

try {
    if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST') {
        require_csrf();
        if ($action === 'issue_api_token') {
            $action = 'api_tokens';
            $formNonce = $_POST['form_nonce'] ?? '';
            if (!panel_consume_state($_SESSION, 'api_token_issue', $formNonce, null, 30 * 60)) {
                throw new RuntimeException('Форма выпуска уже использована или устарела. Обновите страницу.');
            }
            $issuePayload = panel_api_token_normalize_issue_input([
                'name' => $_POST['name'] ?? '',
                'scopes' => $_POST['scopes'] ?? [],
                'expires_in_days' => $_POST['expires_in_days'] ?? '',
            ]);
            if (!panel_api_token_consume_issue_budget($_SESSION)) {
                throw new RuntimeException('За короткое время выпущено слишком много токенов. Повторите через 15 минут.');
            }
            $issuedApiToken = panel_api_token_issue($issuePayload);
            panel_auth_audit('api_token_issued', [
                'user_id' => $panelUser['id'] ?? 0,
                'token_id' => $issuedApiToken['id'],
                'scopes' => $issuedApiToken['scopes'],
                'expires_at' => $issuedApiToken['expires_at'],
            ]);
            $message = 'Токен выпущен. Скопируйте секрет сейчас — повторно он не показывается.';
        } elseif ($action === 'revoke_api_token') {
            $action = 'api_tokens';
            $tokenId = trim((string)($_POST['token_id'] ?? ''));
            panel_api_token_revoke($tokenId);
            panel_auth_audit('api_token_revoked', [
                'user_id' => $panelUser['id'] ?? 0,
                'token_id' => $tokenId,
            ]);
            $message = 'Токен отозван и больше не даёт доступ к API.';
        } elseif ($action === 'save') {
            $id = int_or_null($_POST['id'] ?? null);
            $current = $id ? find_card($pdo, $id) : null;
            $cardImage = upload_image($config, 'card_image_file', 'cards', $current['card_image'] ?? null);
            $goldenImage = upload_image($config, 'golden_image_file', 'golden', $current['golden_image'] ?? null);
            $artImage = upload_image($config, 'art_image_file', 'art', $current['art_image'] ?? null);
            $framedImage = upload_image($config, 'framed_image_file', 'framed', $current['framed_image'] ?? null);

            $data = [
                'name' => trim((string)($_POST['name'] ?? '')),
                'name_en' => trim((string)($_POST['name_en'] ?? '')),
                'card_id' => trim((string)($_POST['card_id'] ?? '')),
                'dbf' => int_or_null($_POST['dbf'] ?? null),
                'card_type' => card_type_or_default($_POST['card_type'] ?? 'minion'),
                'tavern_tier' => int_or_null($_POST['tavern_tier'] ?? null),
                'creature_type' => creature_type_or_null($_POST['creature_type'] ?? null),
                'attack' => int_or_null($_POST['attack'] ?? null),
                'health' => int_or_null($_POST['health'] ?? null),
                'in_pool' => isset($_POST['in_pool']) ? 1 : 0,
                'duos_only' => isset($_POST['duos_only']) ? 1 : 0,
                'card_image' => $cardImage,
                'golden_image' => $goldenImage,
                'art_image' => $artImage,
                'framed_image' => $framedImage,
                'notes' => trim((string)($_POST['notes'] ?? '')),
            ];

            if ($data['name'] === '' || $data['card_id'] === '') {
                throw new RuntimeException('Название карты и card_id обязательны.');
            }
            if ($data['tavern_tier'] !== null && ($data['tavern_tier'] < 1 || $data['tavern_tier'] > 7)) {
                throw new RuntimeException('Уровень таверны должен быть от 1 до 7.');
            }

            // DDL commits implicitly in MySQL, so it must precede the transaction.
            bg_card_overrides_ensure_schema($pdo);
            $author = (string)($panelUser['login'] ?? '');
            $protectedMessage = ' Изменённые поля не перезапишутся импортом, пока источник не обновит их сам.';
            if ($id) {
                $pdo->beginTransaction();
                try {
                    $changes = $current
                        ? bg_card_override_changes($current, $data, bg_card_overrides_load($pdo, (string)$current['card_id']))
                        : null;
                    $stmt = $pdo->prepare(
                        'UPDATE battlegrounds_cards
                         SET name=:name, name_en=:name_en, card_id=:card_id, dbf=:dbf, tavern_tier=:tavern_tier,
                             card_type=:card_type, creature_type=:creature_type, attack=:attack, health=:health,
                             in_pool=:in_pool, duos_only=:duos_only,
                             card_image=:card_image, golden_image=:golden_image, art_image=:art_image, framed_image=:framed_image, notes=:notes
                         WHERE id=:id'
                    );
                    $stmt->execute($data + ['id' => $id]);
                    if ($changes !== null) {
                        bg_card_overrides_record_edit($pdo, (string)$current['card_id'], $data['card_id'], $changes, $author);
                    }
                    $pdo->commit();
                } catch (Throwable $e) {
                    $pdo->rollBack();
                    throw $e;
                }
                $message = 'Карта обновлена.' . ($changes !== null && $changes['upsert'] !== [] ? $protectedMessage : '');
            } else {
                $stmt = $pdo->prepare('SELECT * FROM battlegrounds_cards WHERE card_id = ? LIMIT 1');
                $stmt->execute([$data['card_id']]);
                $existing = $stmt->fetch();

                if ($existing) {
                    $data['card_image'] = $data['card_image'] ?: $existing['card_image'];
                    $data['golden_image'] = $data['golden_image'] ?: $existing['golden_image'];
                    $data['art_image'] = $data['art_image'] ?: $existing['art_image'];
                    $data['framed_image'] = $data['framed_image'] ?: $existing['framed_image'];
                    $pdo->beginTransaction();
                    try {
                        $changes = bg_card_override_changes($existing, $data, bg_card_overrides_load($pdo, $data['card_id']));
                        $stmt = $pdo->prepare(
                            'UPDATE battlegrounds_cards
                             SET name=:name, name_en=:name_en, dbf=:dbf, tavern_tier=:tavern_tier,
                                 card_type=:card_type, creature_type=:creature_type, attack=:attack, health=:health,
                                 in_pool=:in_pool, duos_only=:duos_only,
                                 card_image=:card_image, golden_image=:golden_image, art_image=:art_image, framed_image=:framed_image, notes=:notes
                             WHERE card_id=:card_id'
                        );
                        $stmt->execute($data);
                        bg_card_overrides_record_edit($pdo, $data['card_id'], $data['card_id'], $changes, $author);
                        $pdo->commit();
                    } catch (Throwable $e) {
                        $pdo->rollBack();
                        throw $e;
                    }
                    $message = 'Карта с таким card_id уже была в базе, я обновил существующую запись.' . ($changes['upsert'] !== [] ? $protectedMessage : '');
                } else {
                    $stmt = $pdo->prepare(
                        'INSERT INTO battlegrounds_cards
                         (name, name_en, card_id, dbf, card_type, tavern_tier, creature_type, attack, health, in_pool, duos_only, card_image, golden_image, art_image, framed_image, notes)
                         VALUES (:name, :name_en, :card_id, :dbf, :card_type, :tavern_tier, :creature_type, :attack, :health, :in_pool, :duos_only, :card_image, :golden_image, :art_image, :framed_image, :notes)'
                    );
                    $stmt->execute($data);
                    $message = 'Карта добавлена.';
                }
            }
            $action = 'list';
        } elseif ($action === 'delete') {
            $id = (int)($_POST['id'] ?? 0);
            $deleted = find_card($pdo, $id);
            bg_card_overrides_ensure_schema($pdo);
            $pdo->beginTransaction();
            try {
                $stmt = $pdo->prepare('DELETE FROM battlegrounds_cards WHERE id = ?');
                $stmt->execute([$id]);
                if ($deleted) {
                    $pdo->prepare('DELETE FROM battlegrounds_card_overrides WHERE card_id = ?')->execute([(string)$deleted['card_id']]);
                }
                $pdo->commit();
            } catch (Throwable $e) {
                $pdo->rollBack();
                throw $e;
            }
            $message = 'Карта удалена.';
            $action = 'list';
        } elseif ($action === 'save_wiki_terms') {
            ensure_wiki_term_schema($pdo);
            $terms = $_POST['terms'] ?? [];
            if (!is_array($terms)) {
                throw new RuntimeException('Некорректный список терминов.');
            }
            $stmt = $pdo->prepare("
                UPDATE battlegrounds_wiki_term_translations
                SET term_ru = :term_ru
                WHERE term_type = :term_type AND term_en = :term_en
            ");
            $allowedTermTypes = array_keys(wiki_term_type_labels());
            foreach ($terms as $type => $items) {
                if (!in_array($type, $allowedTermTypes, true) || !is_array($items)) {
                    continue;
                }
                foreach ($items as $termEn => $termPayload) {
                    if (is_array($termPayload)) {
                        $termEn = trim((string)($termPayload['en'] ?? ''));
                        $termRu = $termPayload['ru'] ?? '';
                    } else {
                        $termEn = trim((string)$termEn);
                        $termRu = $termPayload;
                    }
                    if ($termEn === '') {
                        continue;
                    }
                    $termRu = trim((string)$termRu);
                    $stmt->execute([
                        'term_type' => $type,
                        'term_en' => $termEn,
                        'term_ru' => $termRu === '' ? null : $termRu,
                    ]);
                }
            }
            $message = 'Переводы Wiki terms сохранены.';
            $action = 'wiki_terms';
        }
    }
} catch (Throwable $e) {
    $error = $e->getMessage();
    if (in_array($action, ['issue_api_token', 'revoke_api_token'], true)) {
        $action = 'api_tokens';
    }
}

if ($action === 'api_tokens') {
    $apiTokenManagerConfig = panel_api_token_manager_config();
    if ($apiTokenManagerConfig !== null) {
        try {
            $apiTokens = panel_api_token_list();
        } catch (Throwable $exception) {
            $apiTokenLoadError = $exception->getMessage();
        }
    }
    $apiTokenIssueNonce = panel_issue_state($_SESSION, 'api_token_issue');
}

$editCard = null;
if ($action === 'edit') {
    $editCard = find_card($pdo, (int)($_GET['id'] ?? 0));
}

$q = trim((string)($_GET['q'] ?? ''));
$cardType = trim((string)($_GET['card_type'] ?? ''));
$constructedFormat = trim(strtolower((string)($_GET['constructed_format'] ?? 'all')));
$tier = trim((string)($_GET['tier'] ?? ''));
$creatureType = trim((string)($_GET['creature_type'] ?? ''));
$pool = trim((string)($_GET['pool'] ?? ''));
$duos = trim((string)($_GET['duos'] ?? ''));
$media = trim((string)($_GET['media'] ?? ''));
$skinRarity = strtolower(trim((string)($_GET['rarity'] ?? '')));
$perPage = (int)($_GET['per_page'] ?? ($cardType === '' ? 8 : 50));
if (!in_array($perPage, [8, 12, 15, 25, 50, 100, 150], true)) {
    $perPage = $cardType === '' ? 8 : 50;
}
$where = [];
$params = [];
if ($cardType !== '') {
    if (!array_key_exists($cardType, filter_card_types())) {
        $cardType = '';
    }
}
$showHeroes = $cardType === 'hero';
$showHeroSkins = $cardType === 'hero_skin';
$showPets = $cardType === 'pet';
$showCoins = $cardType === 'coin';
$showTimewarped = $cardType === 'timewarped';
$showConstructed = $cardType === 'constructed';
$showLibrary = array_key_exists($cardType, library_types());
$libraryType = $showLibrary ? $cardType : '';
if (!in_array($constructedFormat, ['all', 'standard', 'wild'], true)) {
    $constructedFormat = 'all';
}

if ($q !== '') {
    if ($showHeroSkins) {
        $where[] = '(name_en LIKE :q_skin_name OR card_id LIKE :q_skin_card_id OR dbf LIKE :q_skin_dbf OR character_name LIKE :q_skin_character OR actor LIKE :q_skin_actor OR artist LIKE :q_skin_artist OR rarity_slug LIKE :q_skin_rarity OR rarity_name_en LIKE :q_skin_rarity_en OR rarity_name_ru LIKE :q_skin_rarity_ru OR primary_category_en LIKE :q_skin_category OR primary_category_ru LIKE :q_skin_category_ru OR tags_json LIKE :q_skin_tags)';
        $params['q_skin_name'] = '%' . $q . '%';
        $params['q_skin_card_id'] = '%' . $q . '%';
        $params['q_skin_dbf'] = '%' . $q . '%';
        $params['q_skin_character'] = '%' . $q . '%';
        $params['q_skin_actor'] = '%' . $q . '%';
        $params['q_skin_artist'] = '%' . $q . '%';
        $params['q_skin_rarity'] = '%' . $q . '%';
        $params['q_skin_rarity_en'] = '%' . $q . '%';
        $params['q_skin_rarity_ru'] = '%' . $q . '%';
        $params['q_skin_category'] = '%' . $q . '%';
        $params['q_skin_category_ru'] = '%' . $q . '%';
        $params['q_skin_tags'] = '%' . $q . '%';
    } elseif ($showHeroes) {
        $where[] = '(name_en LIKE :q_hero_name_en OR name_ru LIKE :q_hero_name_ru OR card_id LIKE :q_hero_card_id OR dbf LIKE :q_hero_dbf OR artist LIKE :q_hero_artist OR as_hero LIKE :q_hero_as_hero OR hero_description LIKE :q_hero_description OR hero_power_json LIKE :q_hero_power OR buddy_json LIKE :q_hero_buddy)';
        $params['q_hero_name_en'] = '%' . $q . '%';
        $params['q_hero_name_ru'] = '%' . $q . '%';
        $params['q_hero_card_id'] = '%' . $q . '%';
        $params['q_hero_dbf'] = '%' . $q . '%';
        $params['q_hero_artist'] = '%' . $q . '%';
        $params['q_hero_as_hero'] = '%' . $q . '%';
        $params['q_hero_description'] = '%' . $q . '%';
        $params['q_hero_power'] = '%' . $q . '%';
        $params['q_hero_buddy'] = '%' . $q . '%';
    } elseif ($showTimewarped) {
        $where[] = '(name_en LIKE :q_tw_name_en OR name_ru LIKE :q_tw_name_ru OR card_id LIKE :q_tw_card_id OR dbf LIKE :q_tw_dbf OR text_en LIKE :q_tw_text_en OR text_ru LIKE :q_tw_text_ru OR artist LIKE :q_tw_artist OR minion_type LIKE :q_tw_minion_type OR race LIKE :q_tw_race OR wiki_mechanics_json LIKE :q_tw_mechanics OR wiki_tags_json LIKE :q_tw_tags)';
        $params['q_tw_name_en'] = '%' . $q . '%';
        $params['q_tw_name_ru'] = '%' . $q . '%';
        $params['q_tw_card_id'] = '%' . $q . '%';
        $params['q_tw_dbf'] = '%' . $q . '%';
        $params['q_tw_text_en'] = '%' . $q . '%';
        $params['q_tw_text_ru'] = '%' . $q . '%';
        $params['q_tw_artist'] = '%' . $q . '%';
        $params['q_tw_minion_type'] = '%' . $q . '%';
        $params['q_tw_race'] = '%' . $q . '%';
        $params['q_tw_mechanics'] = '%' . $q . '%';
        $params['q_tw_tags'] = '%' . $q . '%';
    } elseif ($showConstructed) {
        $where[] = '(c.name_ru LIKE :q_name_ru OR c.name_en LIKE :q_name_en OR c.card_id LIKE :q_card_id OR c.dbf LIKE :q_dbf OR c.text_ru LIKE :q_text_ru OR c.text_en LIKE :q_text_en OR c.flavor_ru LIKE :q_flavor_ru OR c.flavor_en LIKE :q_flavor_en OR c.artist LIKE :q_artist OR c.card_set LIKE :q_card_set OR c.class_slug LIKE :q_class OR c.minion_type LIKE :q_minion_type OR c.spell_school LIKE :q_spell_school OR c.rarity LIKE :q_rarity OR c.mechanics_json LIKE :q_mechanics OR c.referenced_tags_json LIKE :q_tags)';
        $params['q_name_ru'] = '%' . $q . '%';
        $params['q_name_en'] = '%' . $q . '%';
        $params['q_card_id'] = '%' . $q . '%';
        $params['q_dbf'] = '%' . $q . '%';
        $params['q_text_ru'] = '%' . $q . '%';
        $params['q_text_en'] = '%' . $q . '%';
        $params['q_flavor_ru'] = '%' . $q . '%';
        $params['q_flavor_en'] = '%' . $q . '%';
        $params['q_artist'] = '%' . $q . '%';
        $params['q_card_set'] = '%' . $q . '%';
        $params['q_class'] = '%' . $q . '%';
        $params['q_minion_type'] = '%' . $q . '%';
        $params['q_spell_school'] = '%' . $q . '%';
        $params['q_rarity'] = '%' . $q . '%';
        $params['q_mechanics'] = '%' . $q . '%';
        $params['q_tags'] = '%' . $q . '%';
    } elseif ($showPets) {
        $where[] = '(pet_name LIKE :q_pet_name OR variant_name LIKE :q_pet_variant OR card_id LIKE :q_pet_card_id OR dbf LIKE :q_pet_dbf OR page_title LIKE :q_pet_page)';
        $params['q_pet_name'] = '%' . $q . '%';
        $params['q_pet_variant'] = '%' . $q . '%';
        $params['q_pet_card_id'] = '%' . $q . '%';
        $params['q_pet_dbf'] = '%' . $q . '%';
        $params['q_pet_page'] = '%' . $q . '%';
    } elseif ($showCoins) {
        $where[] = '(coin_name_en LIKE :q_coin_name OR card_name_ru LIKE :q_coin_name_ru OR card_name_en LIKE :q_coin_name_en OR card_id LIKE :q_coin_card_id OR dbf LIKE :q_coin_dbf OR artist LIKE :q_coin_artist)';
        $params['q_coin_name'] = '%' . $q . '%';
        $params['q_coin_name_ru'] = '%' . $q . '%';
        $params['q_coin_name_en'] = '%' . $q . '%';
        $params['q_coin_card_id'] = '%' . $q . '%';
        $params['q_coin_dbf'] = '%' . $q . '%';
        $params['q_coin_artist'] = '%' . $q . '%';
    } elseif ($showLibrary) {
        $where[] = '(name_en LIKE :q_lib_name_en OR name_ru LIKE :q_lib_name_ru OR card_id LIKE :q_lib_card_id OR dbf LIKE :q_lib_dbf OR text_en LIKE :q_lib_text_en OR text_ru LIKE :q_lib_text_ru OR artist LIKE :q_lib_artist OR group_name_ru LIKE :q_lib_group OR tier_name_ru LIKE :q_lib_tier OR card_type LIKE :q_lib_card_type)';
        $params['q_lib_name_en'] = '%' . $q . '%';
        $params['q_lib_name_ru'] = '%' . $q . '%';
        $params['q_lib_card_id'] = '%' . $q . '%';
        $params['q_lib_dbf'] = '%' . $q . '%';
        $params['q_lib_text_en'] = '%' . $q . '%';
        $params['q_lib_text_ru'] = '%' . $q . '%';
        $params['q_lib_artist'] = '%' . $q . '%';
        $params['q_lib_group'] = '%' . $q . '%';
        $params['q_lib_tier'] = '%' . $q . '%';
        $params['q_lib_card_type'] = '%' . $q . '%';
    } else {
        $where[] = '(name LIKE :q_name OR name_en LIKE :q_name_en OR card_id LIKE :q_card_id OR dbf LIKE :q_dbf OR card_type LIKE :q_card_type OR creature_type LIKE :q_creature_type OR notes LIKE :q_notes OR EXISTS (
            SELECT 1
            FROM battlegrounds_cards variant_search
            WHERE variant_search.variant_kind = \'golden\'
              AND variant_search.base_dbf = battlegrounds_cards.dbf
              AND (variant_search.name LIKE :q_variant_name OR variant_search.name_en LIKE :q_variant_name_en OR variant_search.card_id LIKE :q_variant_card_id OR variant_search.dbf LIKE :q_variant_dbf OR variant_search.notes LIKE :q_variant_notes)
        ))';
        $params['q_name'] = '%' . $q . '%';
        $params['q_name_en'] = '%' . $q . '%';
        $params['q_card_id'] = '%' . $q . '%';
        $params['q_dbf'] = '%' . $q . '%';
        $params['q_card_type'] = '%' . $q . '%';
        $params['q_creature_type'] = '%' . $q . '%';
        $params['q_notes'] = '%' . $q . '%';
        $params['q_variant_name'] = '%' . $q . '%';
        $params['q_variant_name_en'] = '%' . $q . '%';
        $params['q_variant_card_id'] = '%' . $q . '%';
        $params['q_variant_dbf'] = '%' . $q . '%';
        $params['q_variant_notes'] = '%' . $q . '%';
    }
}

if (!$showHeroes && !$showHeroSkins && !$showPets && !$showCoins && !$showTimewarped && !$showConstructed && !$showLibrary) {
    // Golden/tripled cards are variants, not independent catalogue entries.
    $where[] = "variant_kind = 'base'";
    if ($cardType !== '') {
        $where[] = 'card_type = :card_type';
        $params['card_type'] = $cardType;
    }
    if ($tier !== '') {
        $where[] = 'tavern_tier = :tier';
        $params['tier'] = (int)$tier;
    }
    if ($creatureType !== '') {
        if (!array_key_exists($creatureType, creature_types())) {
            $creatureType = '';
        } else {
            $where[] = 'creature_type = :creature_type';
            $params['creature_type'] = $creatureType;
        }
    }
    if ($pool !== '') {
        if (!in_array($pool, ['0', '1'], true)) {
            $pool = '';
        } else {
            $where[] = 'in_pool = :in_pool';
            $params['in_pool'] = (int)$pool;
        }
    }
    if ($duos !== '') {
        if (!in_array($duos, ['0', '1'], true)) {
            $duos = '';
        } else {
            $where[] = 'duos_only = :duos_only';
            $params['duos_only'] = (int)$duos;
        }
    }
} elseif ($showHeroSkins) {
    if ($media !== '') {
        $skinMediaFilters = [
            'animated' => "animated_image_url IS NOT NULL AND animated_image_url <> ''",
            'gallery' => "COALESCE(JSON_LENGTH(gallery_json), 0) > 0",
            'sounds' => "COALESCE(JSON_LENGTH(sounds_json), 0) > 0",
            'partial' => "status = 'partial'",
        ];
        if (isset($skinMediaFilters[$media])) {
            $where[] = $skinMediaFilters[$media];
        } else {
            $media = '';
        }
    }
    if ($skinRarity !== '') {
        if (!in_array($skinRarity, ['basic', 'lite', 'full', 'diamond', 'legendary', 'mythic', 'unknown'], true)) {
            $skinRarity = '';
        } else {
            $where[] = 'rarity_slug = :skin_rarity';
            $params['skin_rarity'] = $skinRarity;
        }
    }
    $where[] = "status IN ('ok', 'partial')";
    if ($tier !== '' || $creatureType !== '' || $pool !== '' || $duos !== '') {
        $tier = '';
        $creatureType = '';
        $pool = '';
        $duos = '';
    }
} elseif ($showPets) {
    if ($media !== '') {
        $petMediaFilters = [
            'gallery' => "COALESCE(JSON_LENGTH(gallery_json), 0) > 0",
            'background' => "end_screen_background_url IS NOT NULL AND end_screen_background_url <> ''",
        ];
        if (isset($petMediaFilters[$media])) {
            $where[] = $petMediaFilters[$media];
        } else {
            $media = '';
        }
    }
    if ($tier !== '') {
        if (!in_array($tier, ['1', '2', '3', '4'], true)) {
            $tier = '';
        } else {
            $where[] = 'level = :level';
            $params['level'] = (int)$tier;
        }
    }
    $where[] = "status IN ('ok', 'partial')";
    if ($creatureType !== '' || $pool !== '' || $duos !== '') {
        $creatureType = '';
        $pool = '';
        $duos = '';
    }
} elseif ($showCoins) {
    if ($tier !== '' || $creatureType !== '' || $pool !== '' || $duos !== '' || $media !== '') {
        $tier = '';
        $creatureType = '';
        $pool = '';
        $duos = '';
        $media = '';
    }
} elseif ($showHeroes) {
    if ($media !== '') {
        $heroMediaFilters = [
            'has_buddy' => 'buddy_dbf IS NOT NULL',
            'no_buddy' => 'buddy_dbf IS NULL',
            'hero_power_art' => "COALESCE(JSON_LENGTH(JSON_EXTRACT(hero_power_json, '$.gallery')), 0) > 0",
            'buddy_art' => "buddy_dbf IS NOT NULL AND COALESCE(JSON_LENGTH(JSON_EXTRACT(buddy_json, '$.gallery')), 0) > 0",
            'buddy_sounds' => "buddy_dbf IS NOT NULL AND COALESCE(JSON_LENGTH(JSON_EXTRACT(buddy_json, '$.sounds')), 0) > 0",
            'wiki_error' => "(JSON_EXTRACT(hero_power_json, '$.wiki_fetch_error') IS NOT NULL OR JSON_EXTRACT(buddy_json, '$.wiki_fetch_error') IS NOT NULL)",
        ];
        if (isset($heroMediaFilters[$media])) {
            $where[] = $heroMediaFilters[$media];
        } else {
            $media = '';
        }
    }
    if ($tier !== '' || $creatureType !== '' || $pool !== '' || $duos !== '') {
        $tier = '';
        $creatureType = '';
        $pool = '';
        $duos = '';
    }
} elseif ($showTimewarped) {
    if ($tier !== '') {
        $where[] = 'tavern_tier = :tier';
        $params['tier'] = (int)$tier;
    }
    if ($creatureType !== '') {
        if (!array_key_exists($creatureType, creature_types())) {
            $creatureType = '';
        } else {
            $where[] = '(LOWER(minion_type) = :creature_type_minion OR LOWER(race) = :creature_type_race)';
            $params['creature_type_minion'] = $creatureType;
            $params['creature_type_race'] = $creatureType;
        }
    }
    if ($pool !== '' || $duos !== '') {
        $pool = '';
        $duos = '';
    }
    $where[] = "status = 'ok'";
} elseif ($showConstructed) {
    $where[] = 'EXISTS (SELECT 1 FROM constructed_format_cards active_format WHERE active_format.card_id = c.card_id AND active_format.in_format = 1)';
    if ($constructedFormat !== 'all') {
        $where[] = 'EXISTS (SELECT 1 FROM constructed_format_cards ff WHERE ff.card_id = c.card_id AND ff.format_slug = :constructed_format AND ff.in_format = 1)';
        $params['constructed_format'] = $constructedFormat;
    }
    $constructedMedia = [
        'golden' => "(c.image_gold_url IS NOT NULL AND c.image_gold_url <> '')",
        'signature' => "(c.image_signature_url IS NOT NULL AND c.image_signature_url <> '')",
        'diamond' => "(c.image_diamond_url IS NOT NULL AND c.image_diamond_url <> '')",
        'animated_diamond' => "(c.animated_diamond_url IS NOT NULL AND c.animated_diamond_url <> '')",
    ];
    if ($media !== '') {
        if (!isset($constructedMedia[$media])) {
            $media = '';
        } else {
            $where[] = $constructedMedia[$media];
        }
    }
    if ($tier !== '' || $creatureType !== '' || $pool !== '' || $duos !== '') {
        $tier = '';
        $creatureType = '';
        $pool = '';
        $duos = '';
    }
} else {
    $where[] = 'library = :library';
    $params['library'] = $libraryType;
    if ($libraryType === 'darkmoon_prize' && $tier !== '') {
        if (!in_array($tier, ['1', '2', '3', '4'], true)) {
            $tier = '';
        } else {
            $where[] = 'tier_value = :tier';
            $params['tier'] = (int)$tier;
        }
    }
    if ($pool !== '') {
        if (!in_array($pool, ['0', '1'], true)) {
            $pool = '';
        } else {
            $where[] = 'in_pool = :in_pool';
            $params['in_pool'] = (int)$pool;
        }
    }
    if (($libraryType !== 'darkmoon_prize' && $tier !== '') || $creatureType !== '' || $duos !== '') {
        if ($libraryType !== 'darkmoon_prize') {
            $tier = '';
        }
        $creatureType = '';
        $duos = '';
    }
}
$page = max(1, (int)($_GET['page'] ?? 1));
$whereSql = $where ? ' WHERE ' . implode(' AND ', $where) : '';

$heroes = [];
$heroSkins = [];
$pets = [];
$coins = [];
$timewarpedCards = [];
$constructedCards = [];
$libraryCards = [];
$cards = [];
$goldenVariantMap = [];
$wikiMetaMap = [];
$constructedWikiMetaMap = [];
$constructedRelatedCardMap = [];
if ($showHeroSkins) {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM hero_skins' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM hero_skins' . $whereSql
        . ' ORDER BY class_name_en IS NULL, class_name_en ASC, name_en ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $heroSkins = $stmt->fetchAll();
    $heroSkins = panel_attach_horizontal_art(
        $pdo,
        $heroSkins,
        'hero_skin',
        static fn(array $row): string => (string)$row['card_id']
    );
} elseif ($showPets) {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM hearthstone_pets' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM hearthstone_pets' . $whereSql
        . ' ORDER BY pet_id ASC, level IS NULL, level ASC, variant_id ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $pets = $stmt->fetchAll();
    $pets = panel_attach_horizontal_art(
        $pdo,
        $pets,
        'pet',
        static fn(array $row): string => (string)($row['card_id'] ?: 'variant:' . $row['variant_id'])
    );
} elseif ($showCoins) {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM hearthstone_coins' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM hearthstone_coins' . $whereSql
        . ' ORDER BY cosmetic_sort_order IS NULL, cosmetic_sort_order ASC, coin_name_en ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $coins = $stmt->fetchAll();
    $coins = panel_attach_horizontal_art(
        $pdo,
        $coins,
        'coin',
        static fn(array $row): string => (string)$row['card_id']
    );
} elseif ($showHeroes) {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM battlegrounds_heroes' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM battlegrounds_heroes' . $whereSql
        . ' ORDER BY name_en ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $heroes = $stmt->fetchAll();
    $heroes = panel_attach_horizontal_art(
        $pdo,
        $heroes,
        'hero',
        static fn(array $row): string => (string)$row['card_id']
    );
} elseif ($showTimewarped) {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM battlegrounds_timewarped_cards' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM battlegrounds_timewarped_cards' . $whereSql
        . ' ORDER BY tavern_tier IS NULL, tavern_tier ASC, card_type ASC, name_en ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $timewarpedCards = $stmt->fetchAll();
    $timewarpedCards = panel_attach_horizontal_art(
        $pdo,
        $timewarpedCards,
        'timewarped_card',
        static fn(array $row): string => (string)$row['card_id']
    );
} elseif ($showConstructed) {
    $fromSql = ' FROM constructed_cards c';
    $countStmt = $pdo->prepare('SELECT COUNT(*)' . $fromSql . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT card.*,
                (SELECT GROUP_CONCAT(DISTINCT active_format.format_slug ORDER BY active_format.format_slug)
                 FROM constructed_format_cards active_format
                 WHERE active_format.card_id = card.card_id AND active_format.in_format = 1) AS formats
            FROM (
                SELECT c.card_id, c.name_ru, c.name_en'
        . $fromSql . $whereSql
        . ' ORDER BY c.name_ru IS NULL, c.name_ru ASC, c.name_en ASC, c.card_id ASC
                LIMIT :limit OFFSET :offset
            ) page
            INNER JOIN constructed_cards card ON card.card_id = page.card_id
            ORDER BY page.name_ru IS NULL, page.name_ru ASC, page.name_en ASC, page.card_id ASC';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $constructedCards = $stmt->fetchAll();
    $constructedCards = panel_attach_horizontal_art(
        $pdo,
        $constructedCards,
        'constructed_card',
        static fn(array $row): string => (string)$row['card_id']
    );
    $constructedWikiMetaMap = load_constructed_wiki_meta_map($pdo, $constructedCards);
    $constructedRelatedCardMap = load_constructed_related_card_map($pdo, $constructedWikiMetaMap);
} elseif ($showLibrary) {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM battlegrounds_library_cards' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM battlegrounds_library_cards' . $whereSql
        . ' ORDER BY in_pool DESC, sort_order IS NULL, sort_order ASC, name_ru ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $libraryCards = $stmt->fetchAll();
    $libraryCards = panel_attach_horizontal_art(
        $pdo,
        $libraryCards,
        'library_card',
        static fn(array $row): string => (string)$row['library'] . ':' . (string)$row['card_id']
    );
} else {
    $countStmt = $pdo->prepare('SELECT COUNT(*) FROM battlegrounds_cards' . $whereSql);
    bind_statement_params($countStmt, $params);
    $countStmt->execute();
    $filteredTotal = (int)$countStmt->fetchColumn();
    $totalPages = max(1, (int)ceil($filteredTotal / $perPage));
    if ($page > $totalPages) {
        $page = $totalPages;
    }
    $offset = ($page - 1) * $perPage;

    $sql = 'SELECT * FROM battlegrounds_cards' . $whereSql
        . ' ORDER BY in_pool DESC, tavern_tier IS NULL, tavern_tier ASC, name ASC LIMIT :limit OFFSET :offset';
    $stmt = $pdo->prepare($sql);
    bind_statement_params($stmt, $params);
    $stmt->bindValue(':limit', $perPage, PDO::PARAM_INT);
    $stmt->bindValue(':offset', $offset, PDO::PARAM_INT);
    $stmt->execute();
    $cards = $stmt->fetchAll();
    $cards = panel_attach_horizontal_art(
        $pdo,
        $cards,
        'battleground_card',
        static fn(array $row): string => (string)$row['card_id']
    );
    $goldenVariantMap = load_golden_variant_map($pdo, $cards);
    $wikiMetaMap = load_wiki_meta_map($pdo, $cards);
}

$heroSkinRarityTotals = [];
foreach ($pdo->query("SELECT COALESCE(rarity_slug, 'unknown') AS rarity_slug, COUNT(*) AS total FROM hero_skins WHERE status IN ('ok', 'partial') GROUP BY COALESCE(rarity_slug, 'unknown')") as $row) {
    $heroSkinRarityTotals[(string)$row['rarity_slug']] = (int)$row['total'];
}
$pageFrom = $filteredTotal === 0 ? 0 : $offset + 1;
$pageTo = min($offset + ($showHeroSkins ? count($heroSkins) : ($showPets ? count($pets) : ($showCoins ? count($coins) : ($showHeroes ? count($heroes) : ($showTimewarped ? count($timewarpedCards) : ($showConstructed ? count($constructedCards) : ($showLibrary ? count($libraryCards) : count($cards)))))))), $filteredTotal);
$showApiTokens = $action === 'api_tokens';
$mediaLabels = [
    'has_buddy' => 'С компаньоном',
    'no_buddy' => 'Без компаньона',
    'hero_power_art' => 'Есть арт силы',
    'buddy_art' => 'Есть арт компаньона',
    'buddy_sounds' => 'Есть звуки компаньона',
    'wiki_error' => 'Ошибки wiki',
];
$skinMediaLabels = [
    'animated' => 'Есть Animated',
    'gallery' => 'Есть Gallery',
    'sounds' => 'Есть Sounds',
    'partial' => 'Частично разобрано',
];
$skinRarityLabels = [
    'basic' => 'Базовый',
    'lite' => 'Lite',
    'full' => 'Full',
    'diamond' => 'Diamond',
    'legendary' => 'Легендарный',
    'mythic' => 'Мифический',
    'unknown' => 'Не указана',
];
$activeFilters = [];
if ($q !== '') {
    $activeFilters[] = ['label' => 'Поиск: ' . $q, 'href' => query_url(['q' => null, 'page' => null])];
}
if ($tier !== '') {
    $activeFilters[] = ['label' => ($showLibrary && $libraryType === 'darkmoon_prize' ? 'Тир ' : 'Таверна ') . $tier, 'href' => query_url(['tier' => null, 'page' => null])];
}
if ($creatureType !== '') {
    $activeFilters[] = ['label' => creature_type_label($creatureType), 'href' => query_url(['creature_type' => null, 'page' => null])];
}
if ($pool !== '') {
    $activeFilters[] = ['label' => $pool === '1' ? 'В пуле' : 'Не в пуле', 'href' => query_url(['pool' => null, 'page' => null])];
}
if ($duos !== '') {
    $activeFilters[] = ['label' => $duos === '1' ? 'Только дуо' : 'Не только дуо', 'href' => query_url(['duos' => null, 'page' => null])];
}
if ($showHeroes && $media !== '') {
    $activeFilters[] = ['label' => $mediaLabels[$media] ?? $media, 'href' => query_url(['media' => null, 'page' => null])];
}
if ($showHeroSkins && $media !== '') {
    $activeFilters[] = ['label' => $skinMediaLabels[$media] ?? $media, 'href' => query_url(['media' => null, 'page' => null])];
}
if ($showHeroSkins && $skinRarity !== '') {
    $activeFilters[] = ['label' => 'Редкость: ' . ($skinRarityLabels[$skinRarity] ?? $skinRarity), 'href' => query_url(['rarity' => null, 'page' => null])];
}
if ($showPets && $media !== '') {
    $petMediaLabels = ['gallery' => 'Есть Gallery', 'background' => 'Есть End screen'];
    $activeFilters[] = ['label' => $petMediaLabels[$media] ?? $media, 'href' => query_url(['media' => null, 'page' => null])];
}
if ($showConstructed && $constructedFormat !== 'all') {
    $activeFilters[] = ['label' => constructed_format_label($constructedFormat), 'href' => query_url(['constructed_format' => null, 'page' => null])];
}
if ($showConstructed && $media !== '') {
    $constructedMediaLabels = [
        'golden' => 'Есть Golden',
        'signature' => 'Есть Signature',
        'diamond' => 'Есть Diamond',
        'animated_diamond' => 'Есть Animated Diamond',
    ];
    $activeFilters[] = ['label' => $constructedMediaLabels[$media] ?? $media, 'href' => query_url(['media' => null, 'page' => null])];
}
$form = $editCard ?: [
    'id' => '',
    'name' => '',
    'name_en' => '',
    'card_id' => '',
    'dbf' => '',
    'card_type' => 'minion',
    'tavern_tier' => '',
    'creature_type' => '',
    'attack' => '',
    'health' => '',
    'in_pool' => 1,
    'duos_only' => 0,
    'card_image' => '',
    'golden_image' => '',
    'art_image' => '',
    'framed_image' => '',
    'notes' => '',
];
$workspaceTitle = $action === 'parsers'
    ? 'Парсеры'
    : ($action === 'api_tokens'
    ? 'API-токены'
    : ($action === 'analytics'
        ? 'Обзор и мета'
    : ($action === 'wiki_terms'
        ? 'Переводы Wiki'
        : ($action === 'new'
            ? 'Новая карта'
            : ($editCard
                ? 'Редактировать карту'
                : ($showHeroSkins ? 'Скины героев' : ($showPets ? 'Питомцы' : ($showCoins ? 'Монетки' : ($showHeroes ? 'Герои' : ($showTimewarped ? 'Хрономальные карты' : ($showConstructed ? 'Стандартные и вольные карты' : ($showLibrary ? library_type_label($libraryType) : 'Карты Полей сражений'))))))))))));
require_once __DIR__ . '/next_panel.php';
$records = $showHeroes ? $heroes : ($showHeroSkins ? $heroSkins : ($showPets ? $pets : ($showCoins ? $coins : ($showTimewarped ? $timewarpedCards : ($showConstructed ? $constructedCards : ($showLibrary ? $libraryCards : $cards))))));
$extra = [];
$records = panel_next_attach_purchase_costs($pdo, $records, $cardType);
$records = panel_next_enrich_records($records, $cardType, $goldenVariantMap, $showConstructed ? $constructedWikiMetaMap : $wikiMetaMap);
if ($action === 'wiki_terms') {
    $extra['terms'] = wiki_term_groups($pdo);
    $extra['termLabels'] = wiki_term_type_labels();
}
if ($showApiTokens) {
    $extra['tokens'] = $apiTokens;
    $extra['managerId'] = $apiTokenManagerConfig['token_id'] ?? '';
    $extra['tokenConfigured'] = $apiTokenManagerConfig !== null;
    $extra['tokenError'] = $apiTokenLoadError;
    $extra['issueNonce'] = $apiTokenIssueNonce;
    $extra['scopeCatalog'] = panel_api_token_scope_catalog();
    $extra['issuedToken'] = $issuedApiToken;
}
panel_next_json(panel_next_page([
    'action' => $action, 'title' => $workspaceTitle, 'user' => $panelUser,
    'csrf' => csrf(), 'logoutCsrf' => panel_logout_csrf_token(),
    'parserCsrf' => panel_parser_control_csrf_token(),
    'message' => $message, 'error' => $error, 'cardType' => $cardType,
    'records' => $records, 'form' => $form,
    'page' => $page, 'perPage' => $perPage, 'totalPages' => $totalPages,
    'total' => $filteredTotal, 'from' => $pageFrom, 'to' => $pageTo,
    'categories' => filter_card_types(), 'tribes' => creature_types(),
    'mediaLabels' => $showHeroSkins ? $skinMediaLabels : ($showPets ? ['background' => 'Есть end screen', 'gallery' => 'Есть Gallery'] : ($showConstructed ? ['golden' => 'Есть Golden', 'signature' => 'Есть Signature', 'diamond' => 'Есть Diamond', 'animated_diamond' => 'Есть Animated Diamond'] : $mediaLabels)),
    'rarities' => $skinRarityLabels, 'activeFilters' => $activeFilters,
    'extra' => $extra,
]));
