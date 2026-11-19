<?php
declare(strict_types=1);

/*
 * Behavioural test of `localized.render_url` / `render_media_verified` (E24 #122) on the public card-catalog
 * endpoints, against the render index written by the card-renders loader (#121):
 * var/card-renders/{locale}.index.json under the panel root of the router copy.
 *
 * Harness is the same as card_catalog_locale_test.php (real router under `php -S`, in-memory fake DB).
 *
 * The real panel/api/index.php router runs under `php -S` in a temporary copy of
 * panel/. Only db() is replaced with an in-memory fake database (CI has php-cli
 * only: no MariaDB, no pdo_sqlite). The fake evaluates queries against JSON state
 * (catalog rows, locale rows, "table deployed" flag), so every assertion below is
 * on the HTTP response: status, body, headers. PANEL_SRC overrides the panel
 * source (used once to take the "no locale" golden values from the old code).
 */

function check(bool $ok, string $message): void { if (!$ok) throw new RuntimeException($message); }

$source = getenv('PANEL_SRC') ?: dirname(__DIR__);
$tmp = sys_get_temp_dir() . '/card-catalog-locale-' . bin2hex(random_bytes(4));
mkdir($tmp . '/panel/api', 0777, true);
$copy = static function (string $from, string $to) use (&$copy): void {
    if (is_dir($from)) { @mkdir($to, 0777, true); foreach (scandir($from) as $e) if ($e[0] !== '.') $copy("$from/$e", "$to/$e"); }
    else copy($from, $to);
};
$copy($source . '/lib', $tmp . '/panel/lib');
// Count index reads: the copy of the library logs every open of an index file (one read per request expected).
$libPath = $tmp . '/panel/lib/full_card_catalog.php';
$lib = str_replace("\$handle = @fopen(\$path, 'rb');", "file_put_contents(dirname(__DIR__, 2) . '/reads.log', basename(\$path) . \"\\n\", FILE_APPEND); \$handle = @fopen(\$path, 'rb');", file_get_contents($libPath), $n);
check($n === 1, 'cannot instrument the index reader');
file_put_contents($libPath, $lib);

$index = file_get_contents($source . '/api/index.php');
$patched = preg_replace('/(function db\(array \$config\): PDO\s*\{).*?\n\}/s', '$1 return require getenv("FAKE_DB_FILE"); }', $index, 1, $count);
check($count === 1 && is_string($patched), 'cannot stub db() in the router copy');
file_put_contents($tmp . '/panel/api/index.php', $patched);
// Last-Modified must come from the data, not from the copied router file.
touch($tmp . '/panel/api/index.php', strtotime('2000-01-01 00:00:00 UTC'));
file_put_contents($tmp . '/panel/config.php', "<?php return ['db' => []];\n");
file_put_contents($tmp . '/router.php', "<?php require __DIR__ . '/panel/api/index.php';\n");
$statePath = $tmp . '/state.json';

file_put_contents($tmp . '/fake_db.php', <<<'PHP'
<?php
// In-memory stand-in for MariaDB: answers the catalog queries from state.json.
class FakeStmt extends PDOStatement
{
    private array $bound = [];
    private array $rows = [];
    public function __construct(private string $sql) {}
    public function bindValue(string|int $param, mixed $value, int $type = PDO::PARAM_STR): bool { $this->bound[ltrim((string)$param, ':')] = $value; return true; }
    public function execute(?array $params = null): bool
    {
        $p = ($params ?? []) + $this->bound;
        $state = json_decode(file_get_contents(getenv('FAKE_STATE_FILE')), true);
        $joined = str_contains($this->sql, 'hearthstone_card_localizations');
        $locRows = [];
        foreach ($state['locales'] as $l) $locRows[$l['card_id'] . '|' . $l['locale']] = $l;
        $match = [];
        foreach ($state['catalog'] as $c) {
            $loc = $joined ? ($locRows[$c['card_id'] . '|' . ($p['locale'] ?? '')] ?? null) : null;
            if (isset($p['value'])) {
                preg_match('/(card_id|dbf) = :value/', $this->sql, $k);
                if ((string)$c[$k[1]] !== (string)$p['value']) continue;
            }
            foreach (['card_type', 'card_set'] as $col) if (isset($p[$col === 'card_set' ? 'set' : $col]) && $c[$col] !== $p[$col === 'card_set' ? 'set' : $col]) continue 2;
            if (isset($p['id'])) {
                $q = trim($p['id'], '%');
                $hit = stripos($c['card_id'], $q) !== false || stripos($c['name_ru'], $q) !== false || stripos($c['name_en'], $q) !== false
                    || (isset($p['loc']) && $loc && stripos((string)$loc['name'], $q) !== false);
                if (!$hit) continue;
            }
            $match[] = [$c, $loc];
        }
        usort($match, static fn ($a, $b) => strcmp($a[0]['card_id'], $b[0]['card_id']));
        if (str_contains($this->sql, 'COUNT(')) {
            $row = ['total' => (string)count($match), 'updated_at' => $match ? max(array_column(array_column($match, 0), 'updated_at')) : null];
            if ($joined) $row['loc_updated_at'] = ($times = array_filter(array_column(array_column($match, 1), 'updated_at'))) ? max($times) : null;
            $this->rows = [$row];
        } else {
            $match = array_slice($match, (int)($p['offset'] ?? 0), isset($p['limit']) ? (int)$p['limit'] : 1);
            $this->rows = array_map(static function ($m) use ($joined) {
                [$c, $loc] = $m;
                unset($c['card_set_x']);
                return $joined ? $c + ['loc_payload' => $loc['payload'] ?? null, 'loc_updated_at' => $loc['updated_at'] ?? null] : $c;
            }, $match);
        }
        return true;
    }
    public function fetch(int $mode = PDO::FETCH_DEFAULT, int $o = PDO::FETCH_ORI_NEXT, int $off = 0): mixed { return array_shift($this->rows) ?? false; }
    public function fetchAll(int $mode = PDO::FETCH_DEFAULT, mixed ...$args): array { return $this->rows; }
}
return new class extends PDO {
    public function __construct() {}
    public function prepare(string $query, array $options = []): PDOStatement|false
    {
        $state = json_decode(file_get_contents(getenv('FAKE_STATE_FILE')), true);
        if (!$state['table'] && str_contains($query, 'hearthstone_card_localizations')) {
            $e = new PDOException("Table 'hearthstone_card_localizations' doesn't exist");
            $e->errorInfo = ['42S02', 1146, 'missing'];
            throw $e;
        }
        return new FakeStmt($query);
    }
};
PHP);

function catalog_row(string $id, int $dbf, string $ru, string $en, string $type = 'MINION'): array
{
    return ['card_id' => $id, 'dbf' => (string)$dbf, 'collectible' => '0', 'card_type' => $type, 'card_set' => 'BE',
        'name_ru' => $ru, 'name_en' => $en, 'updated_at' => '2026-10-15 00:00:00',
        'payload_ru' => json_encode(['id' => $id, 'name' => $ru, 'text' => 'ru text', 'type' => $type, 'set' => 'BE', 'attack' => 8], JSON_UNESCAPED_UNICODE),
        'payload_en' => json_encode(['id' => $id, 'name' => $en, 'text' => 'en text', 'flavor' => 'en flavor', 'type' => $type, 'set' => 'BE', 'attack' => 8])];
}
function locale_row(string $id, string $locale, string $name, string $updated = '2026-11-01 00:00:00'): array
{
    return ['card_id' => $id, 'locale' => $locale, 'dbf' => 130406, 'name' => $name, 'updated_at' => $updated,
        'payload' => json_encode(['id' => $id, 'name' => $name, 'text' => "text $locale", 'flavor' => "flavor $locale",
            'collectionText' => "collection $locale", 'howToEarn' => "earn $locale", 'howToEarnGolden' => "golden $locale",
            'targetingArrowText' => "arrow $locale"], JSON_UNESCAPED_UNICODE)];
}
$names = ['ruRU' => 'Безднокрыл', 'enUS' => 'Voidwing', 'plPL' => 'Pustoskrzydły', 'esES' => 'Alavacío'];
$state = ['table' => true,
    'catalog' => [catalog_row('BE_222t', 130406, 'Безднокрыл', 'Voidwing'), catalog_row('X_1', 1, 'Икс', 'Ex', 'SPELL')],
    // esES intentionally has no row for BE_222t; X_1 has no locale rows at all.
    'locales' => [locale_row('BE_222t', 'ruRU', $names['ruRU']), locale_row('BE_222t', 'enUS', $names['enUS']),
        locale_row('BE_222t', 'plPL', $names['plPL'])]];
$save = static function () use (&$state, $statePath): void { file_put_contents($statePath, json_encode($state, JSON_UNESCAPED_UNICODE)); };
$save();

$probe = stream_socket_server('tcp://127.0.0.1:0', $errno, $errstr);
$port = (int)substr(strrchr((string)stream_socket_get_name($probe, false), ':'), 1);
fclose($probe);
$server = proc_open([PHP_BINARY, '-S', "127.0.0.1:$port", '-t', $tmp, $tmp . '/router.php'], [1 => ['file', '/dev/null', 'w'], 2 => ['file', '/dev/null', 'w']], $pipes,
    $tmp, ['FAKE_DB_FILE' => $tmp . '/fake_db.php', 'FAKE_STATE_FILE' => $statePath, 'PATH' => getenv('PATH') ?: '/usr/bin:/bin']);
register_shutdown_function(static function () use ($server, $tmp): void {
    proc_terminate($server);
    proc_close($server);
    exec('rm -rf ' . escapeshellarg($tmp));
});
for ($i = 0; $i < 100 && !@fsockopen('127.0.0.1', $port); $i++) usleep(50000);

/** @return array{0:int,1:string,2:array<string,string>} */
function get(int $port, string $path, array $headers = []): array
{
    $lines = '';
    foreach ($headers as $k => $v) $lines .= "$k: $v\r\n";
    $body = @file_get_contents("http://127.0.0.1:$port$path", false, stream_context_create(['http' => ['ignore_errors' => true, 'header' => $lines]]));
    $out = [];
    $status = 0;
    foreach ($http_response_header ?? [] as $h) {
        if (preg_match('~^HTTP/\S+ (\d+)~', $h, $m)) $status = (int)$m[1];
        elseif (str_contains($h, ':')) { [$k, $v] = explode(':', $h, 2); $out[strtolower($k)] = trim($v); }
    }
    return [$status, (string)$body, $out];
}
function json_of(string $body): array { return json_decode($body, true, 512, JSON_THROW_ON_ERROR); }


const OURS = 'https://api.kolodahearthstone.com/uploads/card-renders/';
const HSJ = 'https://art.hearthstonejson.com/v1/render/latest/';
$indexDir = $tmp . '/panel/var/card-renders';
$writeIndex = static function (string $locale, array $data, ?int $mtime = null) use ($indexDir): void {
    @mkdir($indexDir, 0777, true);
    file_put_contents("$indexDir/$locale.index.json", json_encode($data + ['version' => 1, 'locale' => $locale, 'size' => '512x',
        'updated_at' => '2026-11-18T00:00:00Z', 'skip_types' => ['ENCHANTMENT', 'PET'], 'renders' => [], 'missing' => []]));
    if ($mtime !== null) touch("$indexDir/$locale.index.json", $mtime);
};
$loc = static fn (array $card): array => $card['localized'];
$reads = static fn (): int => is_file($GLOBALS['tmp'] . '/reads.log') ? count(file($GLOBALS['tmp'] . '/reads.log')) : 0;

$state['catalog'] = [
    catalog_row('BE_222t', 130406, 'Безднокрыл', 'Voidwing'),
    catalog_row('CS2_029', 1, 'Огненный шар', 'Fireball', 'SPELL'),
    catalog_row('ENCH_1', 2, 'Чары', 'Ench', 'ENCHANTMENT'),
    catalog_row('TRK_1', 3, 'Тринкет', 'Trinket', 'BATTLEGROUND_TRINKET'),
    catalog_row('MIS_1', 4, 'Пропавшая', 'Gone', 'MINION'),
    catalog_row('WE_1', 5, 'Оружие', 'Weapon', 'WEAPON'),
];
$state['locales'] = [];
$save();

// 1. No index directory at all: HSJ link of the requested locale for the six types, null otherwise; 200, no leakage of warnings.
[$s, $b] = get($port, '/api/v1/card-catalog/BE_222t?locale=plPL');
check($s === 200 && $loc(json_of($b)['data']) ['render_url'] === HSJ . 'plPL/512x/BE_222t.png', 'no index: HSJ link in requested locale');
check($loc(json_of($b)['data'])['render_media_verified'] === false, 'no index: not verified');
check(!str_contains($b, 'Warning') && !str_contains($b, 'Notice'), 'no index: no PHP warnings in the body');
$l = $loc(json_of(get($port, '/api/v1/card-catalog/TRK_1?locale=plPL')[1])['data']);
check($l['render_url'] === null && $l['render_media_verified'] === false, 'no index: trinket outside the six types is null');
$l = $loc(json_of(get($port, '/api/v1/card-catalog/ENCH_1?locale=plPL')[1])['data']);
check($l['render_url'] === null && $l['render_media_verified'] === false, 'no index: ENCHANTMENT is null');

// 2. Downloaded: our URL with ?v=, verified. Locale of the render is the requested one, also when texts fall back (no locale rows here).
$writeIndex('plPL', ['renders' => ['BE_222t' => 'a1b2c3d4e5f6', 'TRK_1' => '0123456789ab'], 'missing' => ['MIS_1']]);
foreach (['/api/v1/card-catalog/BE_222t?locale=plPL', '/api/v1/card-catalog/dbf/130406?locale=plPL'] as $path) {
    $card = json_of(get($port, $path)[1])['data'];
    check($loc($card)['fallback'] === true, 'texts fall back to enUS (no locale row)');
    check($loc($card)['render_url'] === OURS . 'plPL/512x/BE_222t.webp?v=a1b2c3d4e5f6' && $loc($card)['render_media_verified'] === true, "downloaded render at $path");
}
$l = $loc(json_of(get($port, '/api/v1/card-catalog/TRK_1?locale=plPL')[1])['data']);
check($l['render_url'] === OURS . 'plPL/512x/TRK_1.webp?v=0123456789ab' && $l['render_media_verified'] === true, 'downloaded trinket: our URL although HSJ link is not built for this type');
// Index of one locale does not leak into another one.
$l = $loc(json_of(get($port, '/api/v1/card-catalog/BE_222t?locale=esES')[1])['data']);
check($l['render_url'] === HSJ . 'esES/512x/BE_222t.png' && $l['render_media_verified'] === false, 'other locale without index');

// 3. Not downloaded yet (index exists, id absent): HSJ link, not verified. 404 and skip_types: null.
$l = $loc(json_of(get($port, '/api/v1/card-catalog/CS2_029?locale=plPL')[1])['data']);
check($l['render_url'] === HSJ . 'plPL/512x/CS2_029.png' && $l['render_media_verified'] === false, 'not downloaded: HSJ link');
$l = $loc(json_of(get($port, '/api/v1/card-catalog/MIS_1?locale=plPL')[1])['data']);
check($l['render_url'] === null && $l['render_media_verified'] === false, 'missing (HSJ 404): null');
$l = $loc(json_of(get($port, '/api/v1/card-catalog/ENCH_1?locale=plPL')[1])['data']);
check($l['render_url'] === null && $l['render_media_verified'] === false, 'skip type: null');
// Even a render id listed for a skipped type must not produce a link.
$writeIndex('plPL', ['renders' => ['ENCH_1' => 'aaaaaaaaaaaa'], 'skip_types' => ['ENCHANTMENT']]);
$l = $loc(json_of(get($port, '/api/v1/card-catalog/ENCH_1?locale=plPL')[1])['data']);
check($l['render_url'] === null && $l['render_media_verified'] === false, 'skip type wins over renders');

// 4. Broken or foreign index: same as no index, status 200.
foreach (['{not json', '', '[]', '{"version":2,"renders":{"BE_222t":"a1b2c3d4e5f6"}}', '{"version":1,"locale":"enUS","renders":{"BE_222t":"a1b2c3d4e5f6"}}',
    '{"version":1,"locale":"plPL","renders":"oops","missing":7,"skip_types":null}'] as $junk) {
    file_put_contents("$indexDir/plPL.index.json", $junk);
    [$s, $b] = get($port, '/api/v1/card-catalog/BE_222t?locale=plPL');
    $l = $loc(json_of($b)['data']);
    check($s === 200 && $l['render_url'] === HSJ . 'plPL/512x/BE_222t.png' && $l['render_media_verified'] === false, 'broken index: ' . substr($junk, 0, 20));
}
file_put_contents("$indexDir/plPL.index.json", '{"version":1,"locale":"plPL","renders":"oops","missing":7,"skip_types":null}');
check(get($port, '/api/v1/card-catalog?locale=plPL')[0] === 200, 'broken index: listing is 200');

// 5. Unsafe values: v must be 12 lower-case hex, id is URL-encoded.
$writeIndex('plPL', ['renders' => ['BE_222t' => 'a1b2c3d4e5f6/../x', 'CS2_029' => 'A1B2C3D4E5F6', 'WE_1' => 'abc']]);
foreach (['BE_222t', 'CS2_029', 'WE_1'] as $id) {
    $l = $loc(json_of(get($port, "/api/v1/card-catalog/$id?locale=plPL")[1])['data']);
    check($l['render_url'] === HSJ . "plPL/512x/$id.png" && $l['render_media_verified'] === false && !str_contains(json_encode($l), 'card-renders'), "bad v for $id is ignored");
}
$state['catalog'][] = catalog_row('A B/c?d', 9, 'Икс', 'Ex', 'MINION');
$save();
$writeIndex('plPL', ['renders' => ['A B/c?d' => '0123456789ab']]);
$l = $loc(json_of(get($port, '/api/v1/card-catalog/' . rawurlencode('A B/c?d') . '?locale=plPL')[1])['data']);
check($l['render_url'] === OURS . 'plPL/512x/A%20B%2Fc%3Fd.webp?v=0123456789ab', 'id is rawurlencoded: ' . var_export($l['render_url'], true));
$l = $loc(json_of(get($port, '/api/v1/card-catalog/' . rawurlencode('A B/c?d') . '?locale=esES')[1])['data']);
check($l['render_url'] === HSJ . 'esES/512x/A%20B%2Fc%3Fd.png', 'id is rawurlencoded in the HSJ link');
array_pop($state['catalog']);
$save();

// 6. Listing esES per_page=200: fields on every card, the index is read once per request.
$writeIndex('esES', ['renders' => ['CS2_029' => 'fedcba987654'], 'missing' => ['MIS_1']]);
@unlink($tmp . '/reads.log');
[$s, $b] = get($port, '/api/v1/card-catalog?locale=esES&per_page=200');
$cards = json_of($b)['data'];
check($s === 200 && count($cards) === 6 && $reads() === 1, 'listing reads the index once, reads: ' . $reads());
foreach ($cards as $c) check(array_key_exists('render_url', $c['localized']) && is_bool($c['localized']['render_media_verified']), 'listing field on ' . $c['card_id']);
$by = array_column($cards, null, 'card_id');
check($by['CS2_029']['localized']['render_url'] === OURS . 'esES/512x/CS2_029.webp?v=fedcba987654'
    && $by['MIS_1']['localized']['render_url'] === null && $by['ENCH_1']['localized']['render_url'] === null
    && $by['WE_1']['localized']['render_url'] === HSJ . 'esES/512x/WE_1.png' && $by['TRK_1']['localized']['render_url'] === null, 'listing values');

// 7. Without ?locale= nothing changes: no render fields, images.card and the top-level flag as before, even with indexes present.
foreach (['/api/v1/card-catalog/CS2_029', '/api/v1/card-catalog/dbf/1', '/api/v1/card-catalog?per_page=200'] as $path) {
    [$s, $b] = get($port, $path);
    check($s === 200 && !str_contains($b, 'render_url') && !str_contains($b, 'render_media_verified') && !str_contains($b, 'card-renders'), "no locale: $path has no render fields");
}
$plain = json_of(get($port, '/api/v1/card-catalog/CS2_029')[1])['data'];
check($plain['images']['card'] === HSJ . 'ruRU/512x/CS2_029.png' && $plain['media_verified'] === false, 'no locale: images.card and media_verified unchanged');
$snapshot = [get($port, '/api/v1/card-catalog/CS2_029')[1], get($port, '/api/v1/card-catalog?per_page=200')[1], get($port, '/api/v1/card-catalog/dbf/1')[1]];
@unlink("$indexDir/esES.index.json");
@unlink("$indexDir/plPL.index.json");
check($snapshot === [get($port, '/api/v1/card-catalog/CS2_029')[1], get($port, '/api/v1/card-catalog?per_page=200')[1], get($port, '/api/v1/card-catalog/dbf/1')[1]],
    'no locale: bodies byte-identical with and without indexes');
check(get($port, '/api/v1/card-catalog/CS2_029')[2]['last-modified'] === 'Thu, 15 Oct 2026 00:00:00 GMT', 'no locale: Last-Modified ignores the index');

// 8. Index change moves ETag and Last-Modified of ?locale= responses (a new render must not be answered with 304).
$writeIndex('plPL', ['renders' => ['CS2_029' => '111111111111']], strtotime('2026-11-10 12:00:00 UTC'));
[$s, , $h1] = get($port, '/api/v1/card-catalog/CS2_029?locale=plPL');
check($s === 200 && $h1['last-modified'] === 'Tue, 10 Nov 2026 12:00:00 GMT', 'Last-Modified follows index mtime: ' . $h1['last-modified']);
check(get($port, '/api/v1/card-catalog/CS2_029?locale=plPL', ['If-None-Match' => $h1['etag']])[0] === 304, 'same index: 304');
$writeIndex('plPL', ['renders' => ['CS2_029' => '222222222222']], strtotime('2026-11-18 09:30:00 UTC'));
[$s, $b, $h2] = get($port, '/api/v1/card-catalog/CS2_029?locale=plPL', ['If-None-Match' => $h1['etag'], 'If-Modified-Since' => $h1['last-modified']]);
check($s === 200 && $h2['etag'] !== $h1['etag'] && $h2['last-modified'] === 'Wed, 18 Nov 2026 09:30:00 GMT', 'new index: new ETag and Last-Modified');
check(str_contains($b, '?v=222222222222'), 'new index: new v in the body');
// Index mtime older than the data does not lower Last-Modified.
$writeIndex('plPL', ['renders' => []], strtotime('2020-01-01 00:00:00 UTC'));
check(get($port, '/api/v1/card-catalog/CS2_029?locale=plPL')[2]['last-modified'] === 'Thu, 15 Oct 2026 00:00:00 GMT', 'old index mtime does not lower Last-Modified');
[, , $hl] = get($port, '/api/v1/card-catalog?locale=plPL');
check($hl['last-modified'] === 'Thu, 15 Oct 2026 00:00:00 GMT', 'listing Last-Modified with old index');
$writeIndex('plPL', ['renders' => []], strtotime('2026-11-19 01:00:00 UTC'));
check(get($port, '/api/v1/card-catalog?locale=plPL')[2]['last-modified'] === 'Thu, 19 Nov 2026 01:00:00 GMT', 'listing Last-Modified follows index mtime');

echo "OK: localized render_url (downloaded, not downloaded, 404/skip, no/broken index, no-locale snapshot, ETag)\n";
