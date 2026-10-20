#!/usr/bin/env python3
"""Execute the importers' actual SQL against an explicitly isolated PG fixture.

Requires an existing disposable container labelled
manacost.task=incremental-import-20260906, network=none, no volumes/ports,
and tmpfs PGDATA. Never accepts a production database URL or credentials.
"""
import argparse
import csv
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--container', required=True)
parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
args, test_args = parser.parse_known_args()
if args.container != 'hs-incremental-fixture-20260906':
    parser.error('only the explicitly named disposable fixture is accepted')
info = json.loads(subprocess.check_output(
    ['sudo', '-n', 'docker', 'inspect', args.container], text=True))[0]
if not (info['Config'].get('Labels', {}).get('manacost.task') == 'incremental-import-20260906'
        and info['HostConfig']['NetworkMode'] == 'none'
        and not info['Mounts'] and not info['HostConfig'].get('PortBindings')
        and '/var/lib/postgresql/data' in info['HostConfig'].get('Tmpfs', {})
        and info['HostConfig']['Memory'] > 0):
    parser.error('fixture isolation checks failed')
CONTAINER = info['Id']


def psql(sql, fail=False):
    result = subprocess.run(
        ['sudo', '-n', 'docker', 'exec', '-i', CONTAINER,
         'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres'],
        input=sql, text=True, capture_output=True, timeout=60)
    if not fail and result.returncode:
        raise AssertionError(result.stderr)
    if fail and result.returncode == 0:
        raise AssertionError('expected import failure')
    return result.stdout.strip()


def blocks(name):
    return re.findall(r"<<<'SQL'\n(.*?)\nSQL", (args.root / 'scripts' / name).read_text(), re.S)


def copy_rows(rows):
    # Match PHP fputcsv: null is unquoted empty; an empty string is not used
    # by these synthetic fixtures. JSON and embedded line breaks use CSV quoting.
    out = io.StringIO()
    csv.writer(out, lineterminator='\n').writerows(rows)
    return out.getvalue() + '\\.\n'


def raw_row(version='v1', payload=None, fetched='2026-09-06T00:00:00Z', state='ok'):
    return ['fixture-source', version, fetched, state,
            json.dumps({'value': 1} if payload is None else payload)]


def snapshot(key='snapshot-a', version='v1', fetched='2026-09-06T00:00:00Z'):
    return [key, 'fixture-stats', version, 'constructed_cards', 'card',
            'standard', 'legend', 'day', 'default', 'all', 'test-patch',
            'https://example.invalid/stats', fetched, '{}']


def row(key='card-a', snap='snapshot-a', name='Fixture card', rate='51.123456', metrics=None):
    return [snap, key, 'card', key, 123, name, 'Карта', 'MAGE', None,
            1000, rate, None, '15.234567', None, '0.1234567',
            None, None, json.dumps({'x': 1} if metrics is None else metrics)]


def run_raw(rows, fail=False):
    parts = blocks('import-datasets.php')
    assert len(parts) == 2
    return psql(parts[0] + '\n' + copy_rows(rows) + parts[1], fail)


def run_stats(snapshots, rows, fail=False):
    parts = blocks('import-statistics.php')
    assert len(parts) == 3
    return psql(parts[0] + '\n' + copy_rows(snapshots) + parts[1] + '\n'
                + copy_rows(rows) + parts[2], fail)


def result(source):
    return json.loads(psql("SELECT jsonb_build_object('read',rows_read,'written',rows_written,"
                          "'details',details) FROM platform.sync_runs WHERE source_id='"
                          + source + "' ORDER BY id DESC LIMIT 1"))


def state(table, order):
    return json.loads(psql(f'SELECT coalesce(jsonb_agg(t ORDER BY {order}),\'[]\') '
                          f'FROM (SELECT *,xmin::text AS row_version FROM {table}) t'))


class IncrementalImport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        psql('DROP SCHEMA IF EXISTS hub,analytics,raw,platform,catalog CASCADE;')
        psql((args.root / 'sql/001_platform.sql').read_text())
        stats = (args.root / 'sql/004_game_statistics.sql').read_text()
        schema = stats[:stats.index('CREATE OR REPLACE VIEW hub.data_overview')]
        psql('CREATE SCHEMA hub;\n' + schema + 'COMMIT;')
        psql("INSERT INTO platform.data_sources(source_id,source_kind,display_name,target_schema) "
             "VALUES ('statistics-normalized','json','fixture','analytics');")

    def setUp(self):
        psql('TRUNCATE analytics.game_stat_rows,analytics.game_stat_snapshots,raw.datasets,platform.sync_runs RESTART IDENTITY CASCADE;')

    def test_raw_identical_does_not_rewrite_or_fake_write_count(self):
        run_raw([raw_row()])
        before = state('raw.datasets', 'source_id')
        checked = psql("SELECT last_synced_at FROM platform.data_sources WHERE source_id='parser-json'")
        run_raw([raw_row()])
        self.assertEqual(before, state('raw.datasets', 'source_id'))
        self.assertEqual(0, result('parser-json')['written'])
        self.assertEqual(1, result('parser-json')['details']['rows_unchanged'])
        self.assertNotEqual(checked, psql("SELECT last_synced_at FROM platform.data_sources WHERE source_id='parser-json'"))

    def test_raw_same_version_correction_nulls_and_history(self):
        run_raw([raw_row()])
        run_raw([raw_row(payload={'value': 2}, fetched=None, state=None)])
        actual = state('raw.datasets', 'source_id')[0]
        self.assertEqual({'value': 2}, actual['payload'])
        self.assertIsNone(actual['state'])
        self.assertIsNone(actual['fetched_at'])
        self.assertEqual(1, result('parser-json')['written'])
        run_raw([raw_row(version='v2')])
        self.assertEqual(2, len(state('raw.datasets', 'dataset_version')))

    def test_raw_json_key_order_is_not_a_change(self):
        run_raw([raw_row(payload={'a': 1, 'b': 2})])
        before = state('raw.datasets', 'source_id')
        run_raw([raw_row(payload={'b': 2, 'a': 1})])
        self.assertEqual(before, state('raw.datasets', 'source_id'))

    def test_raw_existing_version_reactivation_keeps_latest_selection(self):
        for fetched in ['2026-09-06T00:00:00Z', None]:
            run_raw([raw_row(version='v1', fetched=fetched)])
            run_raw([raw_row(version='v2', fetched=fetched)])
            run_raw([raw_row(version='v1', fetched=fetched)])
            selected = psql('SELECT dataset_version FROM raw.datasets ORDER BY fetched_at DESC NULLS LAST, imported_at DESC LIMIT 1')
            self.assertEqual('v1', selected)
            before = state('raw.datasets', 'dataset_version')
            run_raw([raw_row(version='v1', fetched=fetched)])
            after = state('raw.datasets', 'dataset_version')
            if fetched is None:
                # Existing API uses imported_at for sources without fetched_at.
                self.assertNotEqual(before[0]['imported_at'], after[0]['imported_at'])
            else:
                self.assertEqual(before, after)
            psql('TRUNCATE raw.datasets')

    def test_raw_failure_rolls_back_all_changes(self):
        run_raw([raw_row()])
        before = state('raw.datasets', 'source_id')
        run_raw([raw_row(payload={'value': 3}), raw_row(version=None)], fail=True)
        self.assertEqual(before, state('raw.datasets', 'source_id'))
        self.assertEqual('1', psql('SELECT count(*) FROM platform.sync_runs'))

    def test_stats_identical_precision_and_freshness(self):
        run_stats([snapshot()], [row(), row('card-b')])
        before = state('analytics.game_stat_rows', 'entity_key')
        snap = state('analytics.game_stat_snapshots', 'id')[0]
        run_stats([snapshot()], [row(), row('card-b')])
        self.assertEqual(before, state('analytics.game_stat_rows', 'entity_key'))
        self.assertEqual(0, result('statistics-normalized')['written'])
        self.assertEqual(2, result('statistics-normalized')['details']['rows_unchanged'])
        self.assertEqual(0, result('statistics-normalized')['details']['rows_deleted'])
        after = state('analytics.game_stat_snapshots', 'id')[0]
        self.assertEqual(snap['id'], after['id'])
        self.assertNotEqual(snap['imported_at'], after['imported_at'])

    def test_stats_same_version_changes_all_fields_and_retains_history(self):
        run_stats([snapshot(), snapshot('history', 'old')], [row(), row('old-card', 'history')])
        old = state('analytics.game_stat_rows', 'entity_key')[1]
        changed = row(metrics={'corrected': True}, rate=None)
        changed[3:17] = [None, None, 'new', None, None, 'S', None, None, '0.0', None, '4.123456', None, 'image', 'url']
        run_stats([snapshot()], [changed])
        actual = state('analytics.game_stat_rows', 'entity_key')
        self.assertEqual(old, actual[1])
        self.assertEqual('new', actual[0]['name'])
        self.assertIsNone(actual[0]['win_rate'])
        self.assertEqual({'corrected': True}, actual[0]['metrics'])
        self.assertEqual(1, result('statistics-normalized')['written'])

    def test_stats_delete_only_missing_entities_in_imported_snapshots(self):
        run_stats([snapshot(), snapshot('other','other')],
                  [row(), row('delete-me'), row('protected','other')])
        run_stats([snapshot()], [row(), row('new-card')])
        actual = state('analytics.game_stat_rows', 'entity_key')
        self.assertEqual(['card-a','new-card','protected'], [x['entity_key'] for x in actual])
        report = result('statistics-normalized')
        self.assertEqual(1, report['written'])
        self.assertEqual(1, report['details']['rows_deleted'])
        self.assertEqual(1, report['details']['rows_unchanged'])

    def test_stats_empty_imported_snapshot_does_not_clear_other_snapshots(self):
        run_stats([snapshot(), snapshot('other','other')], [row(), row('protected','other')])
        run_stats([snapshot(), snapshot('other','other')], [row('protected','other')])
        self.assertEqual(['protected'], [x['entity_key'] for x in state('analytics.game_stat_rows','entity_key')])

    def test_stats_failure_rolls_back_deletes_updates_and_freshness(self):
        run_stats([snapshot()], [row(), row('keep-me')])
        before = state('analytics.game_stat_rows','entity_key')
        snapshots = state('analytics.game_stat_snapshots','id')
        invalid = row('broken'); invalid[2] = None
        run_stats([snapshot()], [row(name='changed'), invalid], fail=True)
        self.assertEqual(before, state('analytics.game_stat_rows','entity_key'))
        self.assertEqual(snapshots, state('analytics.game_stat_snapshots','id'))
        self.assertEqual('1', psql('SELECT count(*) FROM platform.sync_runs'))

    def test_stats_duplicate_keys_are_stable_and_counted_once(self):
        run_stats([snapshot()], [row(name='first'), row(name='second')])
        before = state('analytics.game_stat_rows','entity_key')
        run_stats([snapshot()], [row(name='first'), row(name='second')])
        self.assertEqual('first', before[0]['name'])
        self.assertEqual(before, state('analytics.game_stat_rows','entity_key'))
        report = result('statistics-normalized')
        self.assertEqual(2, report['read'])
        self.assertEqual(1, report['details']['rows_deduplicated'])
        self.assertEqual(1, report['details']['rows_unchanged'])

    def test_full_php_importers_and_completion_reports(self):
        # Adapt only private test copies' I/O boundaries. The importer bodies,
        # normalization, COPY streams, transaction SQL and report parsing run unchanged.
        with tempfile.TemporaryDirectory(prefix='hsdp-import-test-') as temporary:
            root = Path(temporary)
            data = root / 'datasets'; data.mkdir()
            (root / 'cards.json').write_text('{"cards": []}')
            for name in ['sync-context.php', 'statistics-normalizer.php']:
                (root / name).write_text((args.root / 'scripts' / name).read_text())
            target = """
function %s(): array {
    $process = proc_open(
        ['/usr/bin/sudo', '-n', 'docker', 'exec', '-i', '-e',
         'PGAPPNAME=' . syncApplicationName(), '%s',
         'psql', '-X', '-qAt', '-v', 'ON_ERROR_STOP=1', '-U', 'postgres'],
        [0 => ['pipe', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes
    );
    return [$process, $pipes];
}
"""
            for name, function in [('import-datasets.php', 'openTarget'),
                                   ('import-statistics.php', 'statistics_open_target')]:
                code = (args.root / 'scripts' / name).read_text()
                needle = f'function {function}(): array'
                self.assertEqual(1, code.count(needle))
                code = code.replace(needle, f'function unused_{function}(): array')
                code = code.replace("'/srv/hs-data-api/data/datasets'", repr(str(data)))
                code = code.replace("'/srv/hs-data-api/data/hearthstonejson.cards.ruRU.json'", repr(str(root / 'cards.json')))
                if name == 'import-statistics.php':
                    needle = 'function statistics_fetch_firestone_heroes('
                    self.assertEqual(1, code.count(needle))
                    code = code.replace(needle, 'function unused_statistics_fetch_firestone_heroes(')
                    code += "\nfunction statistics_fetch_firestone_heroes(int $p, array $c): array { throw new RuntimeException('offline fixture'); }\n"
                (root / name).write_text(code + target % (function, CONTAINER))
            payload = {'dataset_version': 'same-version', 'fetched_at': '2026-09-06T00:00:00Z',
                       'data': {'structured': {'type': 'arena_card_tiers',
                           'cards': [{'id': 'FULL_001', 'name': 'Карта', 'win_rate': 53.234567}]}}}
            source = data / 'firestone_arena_cards_normal.json'
            source.write_text(json.dumps(payload))
            for repeat in range(2):
                for name in ['import-datasets.php', 'import-statistics.php']:
                    completed = subprocess.run(['php', str(root / name)], text=True,
                                               capture_output=True, timeout=30)
                    self.assertEqual(0, completed.returncode, completed.stderr)
                    report = json.loads(completed.stdout.splitlines()[-1])
                    self.assertEqual(1 if repeat == 0 else 0, report['rows_written'])
                    self.assertEqual(0 if repeat == 0 else 1, report['rows_unchanged'])
            payload['data']['structured']['cards'][0]['win_rate'] = 55.678912
            source.write_text(json.dumps(payload))
            for name in ['import-datasets.php', 'import-statistics.php']:
                completed = subprocess.run(['php', str(root / name)], text=True,
                                           capture_output=True, timeout=30)
                self.assertEqual(0, completed.returncode, completed.stderr)
                self.assertEqual(1, json.loads(completed.stdout.splitlines()[-1])['rows_written'])


if __name__ == '__main__':
    unittest.main(argv=[sys.argv[0]] + test_args, verbosity=2)
