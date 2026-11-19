<?php
declare(strict_types=1);

/*
 * Behavioural test of `?locale=` on the public card-catalog endpoints.
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

// 1. No locale: no `localized`, body/ETag/Last-Modified unchanged versus the pre-feature code.
//    Golden digests were taken from the router before the parameter existed (PANEL_SRC=<old panel>).
[$s, $body, $h] = get($port, '/api/v1/card-catalog/BE_222t');
[$sl, $bodyList, $hl] = get($port, '/api/v1/card-catalog?per_page=10');
check($s === 200 && $sl === 200, 'default responses must be 200');
check(!str_contains($body . $bodyList, 'localized'), 'localized leaked without locale');
check($h['last-modified'] === 'Thu, 15 Oct 2026 00:00:00 GMT', 'default Last-Modified: ' . $h['last-modified']);
$golden = ['a59f0050f8dfcf9246ae9424b50cae7103506982df8ba8ef525fb8e0555e5e29', '5ef100df119d43dc1f89c2d7ed8c09e483a76f31580aa072a40b4a3ccc4d7e00'];
check(hash('sha256', $body) === $golden[0], 'default detail body changed: ' . hash('sha256', $body));
check(hash('sha256', $bodyList) === $golden[1], 'default listing body changed: ' . hash('sha256', $bodyList));
check($h['etag'] === '"' . $golden[0] . '"', 'default ETag changed');

// 2. Each locale on detail, dbf and listing: requested locale, no fallback, texts from that locale's data.
foreach (['ruRU', 'enUS', 'plPL'] as $locale) {
    $cards = [
        json_of(get($port, "/api/v1/card-catalog/BE_222t?locale=$locale")[1])['data'],
        json_of(get($port, "/api/v1/card-catalog/dbf/130406?locale=$locale")[1])['data'],
        json_of(get($port, "/api/v1/card-catalog?locale=$locale&q=BE_222")[1])['data'][0],
    ];
    foreach ($cards as $card) {
        // render_url / render_media_verified are covered by card_catalog_render_test.php.
        unset($card['localized']['render_url'], $card['localized']['render_media_verified']);
        check($card['localized'] === ['locale' => $locale, 'requested_locale' => $locale, 'fallback' => false,
            'name' => $names[$locale], 'text' => "text $locale", 'flavor' => "flavor $locale",
            'collection_text' => "collection $locale", 'how_to_earn' => "earn $locale",
            'how_to_earn_golden' => "golden $locale", 'targeting_arrow_text' => "arrow $locale"], "localized block for $locale");
        check($card['name'] === ['ru' => 'Безднокрыл', 'en' => 'Voidwing'] && $card['images']['card'] !== null, 'legacy fields changed');
    }
}

// 3. No row for the locale: enUS texts, fallback true, still 200 (detail, dbf, listing).
foreach (['/api/v1/card-catalog/BE_222t?locale=esES', '/api/v1/card-catalog/dbf/130406?locale=esES'] as $path) {
    [$s, $b] = get($port, $path);
    $l = json_of($b)['data']['localized'];
    check($s === 200 && $l['locale'] === 'enUS' && $l['requested_locale'] === 'esES' && $l['fallback'] === true
        && $l['name'] === 'Voidwing' && $l['text'] === 'en text' && $l['flavor'] === 'en flavor' && $l['how_to_earn'] === null, "row fallback $path");
}
$list = json_of(get($port, '/api/v1/card-catalog?locale=plPL')[1])['data'];
check($list[0]['localized']['fallback'] === false && $list[0]['localized']['name'] === $names['plPL'], 'listing: card with row');
check($list[1]['card_id'] === 'X_1' && $list[1]['localized']['fallback'] === true && $list[1]['localized']['name'] === 'Ex', 'listing: card without row falls back');

// 4. Invalid locale -> 400 invalid_parameter from the router, on all three endpoints.
foreach (['esMX', 'ruru', 'pl', '', '%5B%5D=plPL'] as $bad) {
    $query = str_starts_with($bad, '%5B') ? 'locale' . $bad : 'locale=' . $bad;
    foreach (['/api/v1/card-catalog', '/api/v1/card-catalog/BE_222t', '/api/v1/card-catalog/dbf/130406'] as $path) {
        [$s, $b] = get($port, "$path?$query");
        $e = json_of($b)['error'] ?? [];
        check($s === 400 && ($e['code'] ?? '') === 'invalid_parameter'
            && ($e['message'] ?? '') === 'Unsupported locale; expected one of ruRU, enUS, plPL, esES', "400 for $path?$query got $s");
    }
}

// 5. q + locale finds a card by its localized name; without locale the Polish name finds nothing.
$found = json_of(get($port, '/api/v1/card-catalog?locale=plPL&q=' . rawurlencode('Pustoskrz'))[1]);
check(count($found['data']) === 1 && $found['data'][0]['card_id'] === 'BE_222t' && $found['pagination']['total'] === 1, 'q by localized name');
check(json_of(get($port, '/api/v1/card-catalog?q=' . rawurlencode('Pustoskrz'))[1])['pagination']['total'] === 0, 'no locale: Polish name must not match');

// 6. Conditional requests follow the locale data: a changed translation changes ETag and Last-Modified.
[$s, , $h1] = get($port, '/api/v1/card-catalog/BE_222t?locale=plPL');
check($s === 200 && $h1['last-modified'] === 'Sun, 01 Nov 2026 00:00:00 GMT', 'locale Last-Modified: ' . $h1['last-modified']);
check(get($port, '/api/v1/card-catalog/BE_222t?locale=plPL', ['If-None-Match' => $h1['etag']])[0] === 304, 'unchanged ETag must give 304');
$state['locales'][2] = locale_row('BE_222t', 'plPL', 'Pustoskrzydły (nowy)', '2026-11-20 10:00:00');
$save();
[$s, $b, $h2] = get($port, '/api/v1/card-catalog/BE_222t?locale=plPL', ['If-None-Match' => $h1['etag']]);
check($s === 200 && json_of($b)['data']['localized']['name'] === 'Pustoskrzydły (nowy)', 'stale ETag must not give 304');
check($h2['etag'] !== $h1['etag'] && $h2['last-modified'] === 'Fri, 20 Nov 2026 10:00:00 GMT', 'ETag/Last-Modified must follow the locale row');
check(get($port, '/api/v1/card-catalog/BE_222t?locale=plPL', ['If-Modified-Since' => $h1['last-modified']])[0] === 200, 'old If-Modified-Since must not give 304');
[, , $hl2] = get($port, '/api/v1/card-catalog?locale=plPL');
check($hl2['last-modified'] === 'Fri, 20 Nov 2026 10:00:00 GMT', 'listing Last-Modified follows locale row');
$other = get($port, '/api/v1/card-catalog/BE_222t');
check($other[2]['etag'] === '"' . $golden[0] . '"', 'default ETag unaffected by a locale change');

// 7. Table not deployed yet (42S02): 200 with enUS fallback, not 500; default responses keep working.
$state['table'] = false;
$save();
[$s, $b] = get($port, '/api/v1/card-catalog/BE_222t?locale=plPL');
$l = json_of($b)['data']['localized'];
check($s === 200 && $l['locale'] === 'enUS' && $l['requested_locale'] === 'plPL' && $l['fallback'] === true && $l['name'] === 'Voidwing', 'missing table: detail');
[$s, $b] = get($port, '/api/v1/card-catalog?locale=esES&q=Void');
check($s === 200 && json_of($b)['data'][0]['localized']['fallback'] === true, 'missing table: listing with q');
check(get($port, '/api/v1/card-catalog/BE_222t')[1] === $body, 'missing table: default body unchanged');

echo "OK: card catalog locale endpoints (4 locales, fallback, 400, q, conditional requests, missing table)\n";
