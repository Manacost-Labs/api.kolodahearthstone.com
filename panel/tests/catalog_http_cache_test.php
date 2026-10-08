<?php
declare(strict_types=1);
require __DIR__ . '/../lib/catalog_http_cache.php';
$modified = 'Thu, 08 Oct 2026 10:00:00 GMT';
$fixtures = [
    [[], false],
    [['HTTP_IF_NONE_MATCH'=>'"current"'], true],
    [['HTTP_IF_NONE_MATCH'=>'W/"current"'], true],
    [['HTTP_IF_NONE_MATCH'=>'"old", "current"'], true],
    [['HTTP_IF_NONE_MATCH'=>'*'], true],
    [['HTTP_IF_NONE_MATCH'=>'"old"','HTTP_IF_MODIFIED_SINCE'=>$modified], false],
    [['HTTP_IF_MODIFIED_SINCE'=>$modified], true],
    [['HTTP_IF_MODIFIED_SINCE'=>'Wed, 07 Oct 2026 10:00:00 GMT'], false],
    [['HTTP_IF_MODIFIED_SINCE'=>'invalid'], false],
];
foreach ($fixtures as [$server, $expected]) {
    if (catalog_not_modified('"current"', $modified, $server) !== $expected) throw new RuntimeException('Incorrect conditional cache validation');
}
echo "OK: catalog conditional caching\n";
