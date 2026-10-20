<?php
declare(strict_types=1);

const CONSTRUCTED_VIEW_FORMAT_COLUMNS = ['in_format', 'availability_status', 'rotation_year', 'format_changed_at'];

/**
 * `c.*` is expanded when a view is created, so a view only needs recreating
 * when constructed_cards gained or lost columns or its format filter differs.
 * Recreating it on every sync run took metadata locks for up to 8 s.
 */
function constructed_view_is_current(array $tableColumns, array $viewColumns, ?string $definition, string $format): bool
{
    if ($definition === null || $viewColumns !== array_merge($tableColumns, CONSTRUCTED_VIEW_FORMAT_COLUMNS)) {
        return false;
    }
    return preg_match("/`?format_slug`?\\s*=\\s*'" . preg_quote($format, '/') . "'/", $definition) === 1;
}

function ensure_constructed_view(PDO $pdo, string $view, string $format): void
{
    $columns = $pdo->prepare('SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :name ORDER BY ORDINAL_POSITION');
    $columns->execute(['name' => 'constructed_cards']);
    $tableColumns = $columns->fetchAll(PDO::FETCH_COLUMN);
    $columns->execute(['name' => $view]);
    $viewColumns = $columns->fetchAll(PDO::FETCH_COLUMN);
    $definition = $pdo->prepare('SELECT VIEW_DEFINITION FROM INFORMATION_SCHEMA.VIEWS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :name');
    $definition->execute(['name' => $view]);
    $current = $definition->fetchColumn();
    if (constructed_view_is_current($tableColumns, $viewColumns, $current === false ? null : (string)$current, $format)) {
        return;
    }
    $pdo->exec("
        CREATE OR REPLACE VIEW {$view} AS
        SELECT c.*, f.in_format, f.availability_status, f.rotation_year, f.changed_at AS format_changed_at
        FROM constructed_cards c
        INNER JOIN constructed_format_cards f ON f.card_id = c.card_id
        WHERE f.format_slug = '{$format}'
    ");
}
