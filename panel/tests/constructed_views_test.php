<?php
declare(strict_types=1);

require __DIR__ . '/../scripts/lib/constructed_views.php';

function check(bool $condition, string $message): void
{
    if (!$condition) { fwrite(STDERR, "FAIL: $message\n"); exit(1); }
}

$table = ['card_id', 'dbf', 'name_ru'];
$view = array_merge($table, CONSTRUCTED_VIEW_FORMAT_COLUMNS);
$mariadb = "select `c`.`card_id` AS `card_id` from (`generator`.`constructed_cards` `c` join `generator`.`constructed_format_cards` `f` on(`f`.`card_id` = `c`.`card_id`)) where `f`.`format_slug` = 'standard'";

check(constructed_view_is_current($table, $view, $mariadb, 'standard'), 'unchanged view is kept');
check(!constructed_view_is_current($table, $view, $mariadb, 'wild'), 'a view filtering another format is recreated');
check(!constructed_view_is_current(array_merge($table, ['spell_school']), $view, $mariadb, 'standard'), 'a new table column recreates the view');
check(!constructed_view_is_current($table, [], null, 'standard'), 'a missing view is created');
echo "constructed view tests passed\n";
