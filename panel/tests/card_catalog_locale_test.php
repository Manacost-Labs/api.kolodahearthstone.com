<?php
declare(strict_types=1);
require __DIR__ . '/../lib/full_card_catalog.php';
set_error_handler(static function (int $severity, string $message): never { throw new RuntimeException($message); });

function check(bool $ok, string $message): void { if (!$ok) throw new RuntimeException($message); }

/** PDO without a driver: statements answer from a handler and every query is recorded. */
class LocaleFakeStatement extends PDOStatement
{
    private array $bound = [];
    private array $rows = [];
    public function __construct(private LocaleFakePDO $owner, private string $sql) {}
    public function bindValue(string|int $param, mixed $value, int $type = PDO::PARAM_STR): bool { $this->bound[ltrim((string)$param, ':')] = $value; return true; }
    public function execute(?array $params = null): bool
    {
        $this->owner->queries[] = $this->sql;
        $this->rows = ($this->owner->handler)($this->sql, ($params ?? []) + $this->bound);
        return true;
    }
    public function fetch(int $mode = PDO::FETCH_DEFAULT, int $cursorOrientation = PDO::FETCH_ORI_NEXT, int $cursorOffset = 0): mixed { return $this->rows[0] ?? false; }
    public function fetchAll(int $mode = PDO::FETCH_DEFAULT, mixed ...$args): array { return $this->rows; }
}
class LocaleFakePDO extends PDO
{
    public array $queries = [];
    public $handler;
    public function __construct(callable $handler, private bool $hasLocalizations = true) { $this->handler = $handler; }
    public function prepare(string $query, array $options = []): PDOStatement|false
    {
        if (!$this->hasLocalizations && str_contains($query, 'hearthstone_card_localizations')) {
            $error = new PDOException('Table doesn\'t exist');
            $error->errorInfo = ['42S02', 1146, 'Table doesn\'t exist'];
            throw $error;
        }
        return new LocaleFakeStatement($this, $query);
    }
}

function catalog_row(string $id, int $dbf, string $ru, string $en): array
{
    return ['card_id' => $id, 'dbf' => (string)$dbf, 'collectible' => '0', 'updated_at' => '2026-10-15 00:00:00',
        'name_ru' => $ru, 'name_en' => $en,
        'payload_ru' => json_encode(['id' => $id, 'name' => $ru, 'text' => 'ru text', 'type' => 'MINION', 'set' => 'BE'], JSON_UNESCAPED_UNICODE),
        'payload_en' => json_encode(['id' => $id, 'name' => $en, 'text' => 'en text', 'flavor' => 'en flavor', 'type' => 'MINION', 'set' => 'BE'])];
}
$locales = [
    'ruRU' => ['Безднокрыл', 'ru text'], 'enUS' => ['Voidwing', 'en text'],
    'plPL' => ['Pustoskrzydły', 'pl text'], 'esES' => ['Alavacío', 'es text'],
];
function locale_payload(string $locale, array $texts): string
{
    return json_encode(['id' => 'BE_222t', 'name' => $texts[0], 'text' => $texts[1], 'howToEarn' => 'earn ' . $locale,
        'targetingArrowText' => 'arrow ' . $locale], JSON_UNESCAPED_UNICODE);
}
$handler = static function (string $sql, array $params) use ($locales): array {
    $base = catalog_row('BE_222t', 130406, 'Безднокрыл', 'Voidwing');
    $localeRow = [];
    if (isset($params['locale']) && isset($locales[$params['locale']]) && $params['locale'] !== 'esES') {
        $localeRow = ['loc_payload' => locale_payload($params['locale'], $locales[$params['locale']]), 'loc_updated_at' => '2026-11-20 10:00:00'];
    } elseif (str_contains($sql, 'loc_payload')) {
        $localeRow = ['loc_payload' => null, 'loc_updated_at' => null];
    }
    if (str_contains($sql, 'COUNT(')) {
        return [['total' => '1', 'updated_at' => '2026-10-15 00:00:00', 'loc_updated_at' => $localeRow['loc_updated_at'] ?? null]];
    }
    return [$base + $localeRow];
};

// Contract: no locale -> no `localized`, and the response body is byte-identical to the pre-feature code
// (the digests below were taken from the implementation before the locale parameter existed).
$pdo = new LocaleFakePDO($handler);
$plain = json_encode(['data' => full_catalog_find($pdo, 'card_id', 'BE_222t')], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
$plainList = json_encode(full_catalog_listing($pdo, []), JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
check(!str_contains($plain . $plainList, 'localized'), 'localized leaked without locale');
check(!str_contains(implode($pdo->queries), 'hearthstone_card_localizations'), 'localizations table touched without locale');
check(hash('sha256', $plain) === '769c17ae63ad589e2789e70213596169e2f41ad51db89b2f228375d22f8b987d', 'detail body changed without locale: ' . hash('sha256', $plain));
check(hash('sha256', $plainList) === '7002be96ac43eb354d9fb9904efa7df75d1965f74396d94a508ea565d471cb90', 'listing body changed without locale: ' . hash('sha256', $plainList));

// Four locales on detail and listing: requested == returned, no fallback, texts from the locale payload.
foreach (['ruRU', 'enUS', 'plPL'] as $locale) {
    foreach ([
        full_catalog_find($pdo, 'card_id', 'BE_222t', $locale),
        full_catalog_find($pdo, 'dbf', '130406', $locale),
        full_catalog_listing($pdo, ['locale' => $locale])['data'][0],
    ] as $card) {
        $l = $card['localized'];
        check($l['locale'] === $locale && $l['requested_locale'] === $locale && $l['fallback'] === false, "bad block for $locale");
        check($l['name'] === $locales[$locale][0] && $l['text'] === $locales[$locale][1], "bad texts for $locale");
        check($l['how_to_earn'] === 'earn ' . $locale && $l['targeting_arrow_text'] === 'arrow ' . $locale, "bad snake_case keys for $locale");
        check($l['flavor'] === null && $l['collection_text'] === null && $l['how_to_earn_golden'] === null, 'absent HSJ fields must be null');
        check(array_keys($l) === ['locale', 'requested_locale', 'fallback', 'name', 'text', 'flavor', 'collection_text',
            'how_to_earn', 'how_to_earn_golden', 'targeting_arrow_text'], 'block shape');
        check($card['name'] === ['ru' => 'Безднокрыл', 'en' => 'Voidwing'], 'legacy fields changed');
    }
}

// esES has no row for this card: enUS texts from payload_en, fallback true.
$card = full_catalog_find($pdo, 'card_id', 'BE_222t', 'esES');
check($card['localized']['locale'] === 'enUS' && $card['localized']['requested_locale'] === 'esES'
    && $card['localized']['fallback'] === true, 'row fallback block');
check($card['localized']['name'] === 'Voidwing' && $card['localized']['flavor'] === 'en flavor', 'row fallback texts');

// Last-Modified is max(catalog, locale row).
$modified = null;
full_catalog_find($pdo, 'card_id', 'BE_222t', 'plPL', $modified);
check($modified === '2026-11-20 10:00:00', 'detail modified must include locale row');
full_catalog_find($pdo, 'card_id', 'BE_222t', 'esES', $modified);
check($modified === '2026-10-15 00:00:00', 'fallback keeps catalog modified');
check(full_catalog_listing($pdo, ['locale' => 'plPL'])['last_updated'] === '2026-11-20 10:00:00', 'listing modified');

// One query pair per page, regardless of page size (no N+1).
$pdo = new LocaleFakePDO($handler);
full_catalog_listing($pdo, ['locale' => 'plPL', 'per_page' => '200']);
check(count($pdo->queries) === 2, 'listing must use COUNT + one page query, got ' . count($pdo->queries));
check(count(array_filter($pdo->queries, static fn ($q) => str_contains($q, 'LEFT JOIN hearthstone_card_localizations'))) === 2, 'join expected');

// q with locale also searches the locale name.
$pdo = new LocaleFakePDO($handler);
full_catalog_listing($pdo, ['locale' => 'plPL', 'q' => 'Pusto']);
check(str_contains($pdo->queries[1], 'l.name LIKE :loc'), 'locale name must be searchable');
$pdo = new LocaleFakePDO($handler);
full_catalog_listing($pdo, ['q' => 'Pusto']);
check(!str_contains($pdo->queries[1], 'l.name'), 'no locale name search without locale');

// Invalid locale: absent key is fine, anything else is rejected (case-sensitive).
foreach (['esMX', '', 'pl', 'plpl', 'PLPL', ['plPL'], null, 0] as $bad) {
    try { full_catalog_locale(['locale' => $bad]); throw new RuntimeException('Accepted locale ' . json_encode($bad)); }
    catch (InvalidArgumentException $e) {
        check($e->getMessage() === 'Unsupported locale; expected one of ruRU, enUS, plPL, esES', 'error message');
    }
    try { full_catalog_listing($pdo, ['locale' => $bad]); throw new RuntimeException('Listing accepted locale'); }
    catch (InvalidArgumentException $e) {}
}
check(full_catalog_locale([]) === null, 'absent locale');

// Table not deployed yet (42S02): detail and listing answer with enUS fallback, not an error.
$pdo = new LocaleFakePDO($handler, false);
$card = full_catalog_find($pdo, 'card_id', 'BE_222t', 'plPL');
check($card !== null && $card['localized']['fallback'] === true && $card['localized']['locale'] === 'enUS'
    && $card['localized']['requested_locale'] === 'plPL' && $card['localized']['name'] === 'Voidwing', 'missing table detail');
$list = full_catalog_listing($pdo, ['locale' => 'plPL', 'q' => 'Void']);
check($list['data'][0]['localized']['fallback'] === true && $list['pagination']['total'] === 1, 'missing table listing');
check(!str_contains(implode($pdo->queries), 'l.name'), 'fallback query must not reference the missing table');
check(full_catalog_find($pdo, 'card_id', 'BE_222t') !== null, 'no locale still works without table');

// A broken locale payload degrades to the fallback instead of failing.
$broken = new LocaleFakePDO(static fn ($sql, $p) => str_contains($sql, 'COUNT(')
    ? [['total' => '1', 'updated_at' => 'x', 'loc_updated_at' => null]]
    : [catalog_row('BE_222t', 130406, 'Безднокрыл', 'Voidwing') + ['loc_payload' => '{oops', 'loc_updated_at' => null]]);
check(full_catalog_find($broken, 'card_id', 'BE_222t', 'plPL')['localized']['fallback'] === true, 'broken payload fallback');

echo "OK: card catalog locales, fallback, validation, byte-stable default response\n";
