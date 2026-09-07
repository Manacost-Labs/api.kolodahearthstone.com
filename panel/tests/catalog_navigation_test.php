<?php
declare(strict_types=1);
require __DIR__ . '/../lib/catalog_navigation.php';
function check_navigation(bool $ok, string $message): void {
    if (!$ok) throw new RuntimeException($message);
}
foreach (['', 'drop table', [], null, 42] as $value) {
    check_navigation(panel_catalog_sort($value) === 'default', 'Untrusted sort falls back safely');
}
foreach (array_keys(panel_catalog_sort_options()) as $sort) {
    check_navigation(panel_catalog_sort($sort) === $sort, 'Known sort accepted');
}
$query = ['q'=>'<искать>', 'pool'=>'0', 'sort'=>'updated_desc', 'per_page'=>'25',
    'page'=>'7', 'action'=>'delete', 'id'=>'42', 'token'=>'must-not-propagate', 'tier'=>['bad']];
check_navigation(panel_catalog_page_fields($query) === ['q'=>'<искать>', 'pool'=>'0', 'per_page'=>'25', 'sort'=>'updated_desc'],
    'Page jump preserves scalar view fields, zero filters, and excludes actions/secrets/page/arrays');
check_navigation(panel_catalog_order('hero', 'name_asc') === panel_catalog_order('hero', 'name_asc', true), 'Outer alias applies only to constructed cards');
check_navigation(strpos(panel_catalog_order('constructed', 'updated_desc', true), 'page.updated_at') !== false, 'Outer order matches paged projection');
echo "OK: catalogue navigation input contracts\n";
