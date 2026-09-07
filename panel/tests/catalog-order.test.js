'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {DatabaseSync} = require('node:sqlite');
const {spawnSync} = require('node:child_process');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const helper = join(__dirname, '../lib/catalog_navigation.php');
const sections = {'': 'battlegrounds_cards', minion:'battlegrounds_cards', spell:'battlegrounds_cards',
    hero:'battlegrounds_heroes', hero_skin:'hero_skins', pet:'hearthstone_pets', coin:'hearthstone_coins',
    timewarped:'battlegrounds_timewarped_cards', constructed:'constructed_cards',
    anomaly:'battlegrounds_library_cards', quest:'battlegrounds_library_cards',
    darkmoon_prize:'battlegrounds_library_cards', reward:'battlegrounds_library_cards', trinket:'battlegrounds_library_cards'};
const schema = readFileSync(join(__dirname, '../sql/schema.mysql.sql'), 'utf8');
const run = spawnSync('php', ['-r', `require $argv[1]; $out=[];
    foreach (json_decode($argv[2],true) as $section) {
        foreach (array_keys(panel_catalog_sort_options()) as $sort) {
            $out[$section][$sort]=[panel_catalog_order($section,$sort),panel_catalog_order($section,$sort,true)];
        }
    } echo json_encode($out);`, helper, JSON.stringify(Object.keys(sections))], {encoding:'utf8'});
assert.equal(run.status, 0, run.stderr);
const orders = JSON.parse(run.stdout);
// Execute the emitted SQL on isolated synthetic rows. This checks columns,
// aliases, null placement and page boundaries, not MySQL collation/performance.
for (const [section, table] of Object.entries(sections)) {
    test(`whole-result SQL ordering and stable pages: ${section || 'bg'}`, () => {
        const block = schema.split(`CREATE TABLE \`${table}\` (`)[1].split(') ENGINE=')[0];
        const columns = [...block.matchAll(/^  `([^`]+)`/gm)].map(match => match[1]);
        const db = new DatabaseSync(':memory:');
        try {
            db.exec(`CREATE TABLE records (${columns.map(name => `"${name}" TEXT`).join(',')})`);
            for (const [sort, [inner, outer]] of Object.entries(orders[section])) {
                db.exec('DELETE FROM records');
                for (const id of ['C','B','A']) {
                    const row = Object.fromEntries(columns.map(column => [column, null]));
                    for (const name of ['name','name_en','name_ru','coin_name_en','variant_name']) {
                        if (name in row) row[name] = sort === 'default' ? 'Same' : (id === 'A' ? 'Alpha' : 'Beta');
                    }
                    row.card_id = id;
                    if ('variant_id' in row) row.variant_id = id === 'A' ? '1' : (id === 'B' ? '2' : '3');
                    if ('id' in row) row.id = id;
                    row.updated_at = id === 'B' ? '2026-09-03' : (id === 'C' ? '2026-09-02' : null);
                    db.prepare(`INSERT INTO records VALUES (${columns.map(() => '?').join(',')})`).run(...Object.values(row));
                }
                const expected = sort === 'name_desc' || sort === 'updated_desc' ? ['B','C','A'] : ['A','B','C'];
                const suffix = section === 'constructed' ? ' c' : '';
                const all = db.prepare(`SELECT card_id FROM records${suffix} ORDER BY ${inner}`).all().map(r => r.card_id);
                assert.deepEqual(all, expected, sort);
                const paged = [0,1,2].flatMap(offset => db.prepare(`SELECT card_id FROM records${suffix} ORDER BY ${inner} LIMIT 1 OFFSET ?`).all(offset).map(r => r.card_id));
                assert.deepEqual(paged, all, 'No repeated or missing rows across stable pages');
                if (section === 'constructed') {
                    const rows = db.prepare(`SELECT card_id FROM (SELECT c.card_id,c.name_ru,c.name_en,c.updated_at FROM records c ORDER BY ${inner} LIMIT 2) page ORDER BY ${outer}`).all();
                    assert.deepEqual(rows.map(r=>r.card_id), expected.slice(0,2), 'Outer query preserves page ordering');
                }
            }
        } finally { db.close(); }
    });
}
