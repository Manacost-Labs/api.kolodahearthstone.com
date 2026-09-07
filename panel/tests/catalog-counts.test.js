'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {DatabaseSync} = require('node:sqlite');
const {spawnSync} = require('node:child_process');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');

// Capture the production SQL without opening a connection or loading index/auth.
const run = spawnSync('php', ['-r', `require $argv[1];
class S extends PDOStatement {
    public function fetch($mode=PDO::FETCH_DEFAULT,$orientation=PDO::FETCH_ORI_NEXT,$offset=0): mixed {return [];}
    public function fetchAll($mode=PDO::FETCH_DEFAULT,...$args): array {return [];}
}
class D extends PDO {
    public $queries=[];
    public function __construct() {}
    public function query($query,$fetchMode=null,...$args): PDOStatement|false {$this->queries[]=$query; return new S();}
}
$out=[]; foreach (['','hero','hero_skin','pet','constructed','coin'] as $type) {
    $db=new D(); panel_catalog_counts($db,'list',$type); $out[$type]=$db->queries;
} echo json_encode($out);`, join(__dirname, '../lib/catalog_read.php')], {encoding:'utf8'});
assert.equal(run.status, 0, run.stderr);
const queries = JSON.parse(run.stdout);
const schema = readFileSync(join(__dirname, '../sql/schema.mysql.sql'), 'utf8');
const fixtures = {
    battlegrounds_cards: {columns:['variant_kind'], rows:[['base'],['base'],['golden'],[null]]},
    battlegrounds_heroes: {
        columns:['status','buddy_dbf','hero_power_json','buddy_json'],
        rows:[['ok',7,'{"gallery":[1]}','{"gallery":[1],"sounds":[]}'],
            ['ok',0,'{"gallery":[],"wiki_fetch_error":null}','{"gallery":[],"sounds":[1]}'],
            ['ok',null,null,'{"gallery":[1],"sounds":[1],"wiki_fetch_error":"failed"}'],
            ['ok',null,'{"gallery":null}',null],
            ['partial',99,'{"gallery":[1]}','{"sounds":[1]}'],['error',88,null,null],[null,77,null,null]],
    },
    hero_skins: {
        columns:['status','animated_image_url','gallery_json','sounds_json','rarity_slug'],
        rows:[['ok','animated','[1]','[]','legendary'],['partial','',null,'[1]',null],
            ['ok',null,'[]',null,''],['error','animated','[1]','[1]','legendary']],
    },
    hearthstone_pets: {
        columns:['status','pet_id','gallery_json','end_screen_background_url'],
        rows:[['ok','A','[1]','bg'],['partial','A','[]',''],['ok','B',null,' '],
            ['ok',null,'[]',null],['error','C','[1]','bg']],
    },
    hearthstone_coins: {
        columns:['cosmetic_sort_order','generated_by_card_ids_json','related_card_ids_json'],
        rows:[[1,'["A","B"]','["C"]'],[2,'[]',null]],
    },
    battlegrounds_timewarped_cards: {columns:['status'], rows:[['ok'],['partial'],['error'],[null]]},
    constructed_cards: {columns:['image_diamond_url','animated_diamond_url'], rows:[[null,''],['',null],['image','animated'],[' ',' ']]},
    constructed_format_cards: {columns:['format_slug','in_format'], rows:[['standard',1],['standard',1],['wild',1],['standard',0],['wild',null],['other',1]]},
    constructed_card_wiki_meta: {columns:['status'], rows:[['ok'],['ok'],['partial'],['error'],[null]]},
};
const nav = {total:2, heroTotal:4, heroSkinsTotal:3, petsTotal:4, coinsTotal:2, timewarpedTotal:1, constructedTotal:4};
const expected = {
    '':nav,
    hero:{...nav, heroBuddyTotal:2, heroPowerGalleryTotal:2, buddyGalleryTotal:1, buddySoundsTotal:1, heroWikiErrorTotal:2},
    hero_skin:{...nav, heroSkinsAnimatedTotal:1, heroSkinsGalleryTotal:1, heroSkinsSoundsTotal:1, heroSkinRarityTotals:{legendary:1,unknown:1,'':1}},
    pet:{...nav, petsGalleryTotal:1, petsBackgroundTotal:2, petFamiliesTotal:2},
    constructed:{...nav, constructedDiamondTotal:2, constructedAnimatedDiamondTotal:2, constructedStandardTotal:2, constructedWildTotal:1, constructedWikiTotal:2},
    coin:{...nav, coinGeneratedByTotal:2, coinRelatedTotal:1},
};

for (const empty of [false, true]) test(`executed catalogue aggregates: ${empty ? 'empty tables' : 'status, JSON null, absent keys, duplicate families'}`, () => {
    const db = new DatabaseSync(':memory:');
    try {
        // Small MySQL JSON compatibility functions: JSON null is not SQL NULL;
        // scalar JSON values have length one. Not a MySQL query-plan benchmark.
        db.function('JSON_EXTRACT', (value, path) => {
            if (value === null) return null;
            const parsed = JSON.parse(value), key = path.slice(2);
            return parsed !== null && Object.hasOwn(parsed, key) ? JSON.stringify(parsed[key]) : null;
        });
        db.function('JSON_LENGTH', value => {
            if (value === null) return null;
            const parsed = JSON.parse(value);
            return parsed && typeof parsed === 'object' ? Object.keys(parsed).length : 1;
        });
        for (const [table, {columns, rows}] of Object.entries(fixtures)) {
            const definition = schema.split(`CREATE TABLE \`${table}\` (`)[1].split(') ENGINE=')[0];
            for (const column of columns) assert.ok(definition.includes('`' + column + '`'), `${table}.${column} exists`);
            db.exec(`CREATE TABLE ${table} (${columns.join(',')})`);
            if (!empty) for (const row of rows) db.prepare(`INSERT INTO ${table} VALUES (${columns.map(()=>'?').join(',')})`).run(...row);
        }
        for (const [section, statements] of Object.entries(queries)) {
            const result = {};
            for (const sql of statements) {
                const rows = db.prepare(sql).all();
                if (sql.includes('GROUP BY')) result.heroSkinRarityTotals = Object.fromEntries(rows.map(r=>[r.rarity_slug,r.total]));
                else if (sql.includes('generated_by_card_ids_json')) {
                    result.coinGeneratedByTotal = JSON.parse(rows[0]?.generated_by_card_ids_json || '[]').length;
                    result.coinRelatedTotal = JSON.parse(rows[0]?.related_card_ids_json || '[]').length;
                } else Object.assign(result, rows[0]);
            }
            const want = empty ? Object.fromEntries(Object.keys(expected[section]).map(key=>[key,key==='heroSkinRarityTotals'?{}:0])) : expected[section];
            assert.deepEqual(result, want, section || 'bg');
        }
    } finally { db.close(); }
});
