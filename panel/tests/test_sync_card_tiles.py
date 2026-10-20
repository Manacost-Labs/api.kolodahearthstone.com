from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import sync_card_tiles as tiles


class CardTileTest(unittest.TestCase):
    def test_spell_source_price_keeps_zero_and_rejects_invalid_values(self):
        for value, expected in [(0, 0), ('2', 2), (3, 3), (-1, None), (True, None), ('', None), ('02', None), ('unknown', None), (2.5, None), (2**63, None)]:
            self.assertEqual(tiles.source_cost(json.dumps({'ru': {'cost': value}})), expected)
        self.assertEqual(tiles.source_cost('{"en":{"cost":4}}'), 4)
        self.assertEqual(tiles.source_cost('{"ru":{"cost":0},"en":{"cost":4}}'), 0)
        self.assertIsNone(tiles.source_cost('{broken'))

    def test_costs_and_colors_match_panel_design(self):
        fixtures = [
            ('constructed_card', 'constructed', {'name_ru': 'Авиана', 'mana_cost': 9, 'rarity': 'LEGENDARY'}),
            ('constructed_card', 'constructed', {'mana_cost': 0, 'cost': 5, 'rarity': 'EPIC'}),
            ('constructed_card', 'constructed', {'mana_cost': None, 'cost': 2, 'rarity': 'RARE'}),
            ('battleground_card', '', {'tavern_tier': 3, 'card_type': 'minion', 'creature_type': 'dragon'}),
            ('battleground_card', '', {'tier': 2, 'card_type': 'spell', 'cost': 0, 'mana_cost': 4, 'creature_type': 'beast'}),
            ('battleground_card', '', {'tier': None, 'card_type': 'spell', 'mana_cost': 2}),
            ('timewarped_card', 'timewarped', {'tier': 4, 'card_type': 'minion', 'races': '["mechanical", "quillboar"]'}),
            ('battleground_card', '', {'tier': 6, 'card_type': 'minion', 'creature_types': '[{"slug":"all"}]'}),
            ('hero', 'hero', {'name': 'Герой'}),
        ]
        script = "import {deckTile} from './panel-next/lib/model.ts'; let s=''; for await (const c of process.stdin) s+=c; console.log(JSON.stringify(JSON.parse(s).map(([_,type,row])=>deckTile(row,type))));"
        expected = json.loads(subprocess.check_output(['node', '--experimental-strip-types', '--input-type=module', '-e', script], input=json.dumps(fixtures), text=True, cwd=ROOT.parent, stderr=subprocess.DEVNULL))
        for (entity, _, row), model in zip(fixtures, expected):
            with self.subTest(row=row):
                actual = tiles.tile(row, entity)
                self.assertEqual((actual['cost'], actual['price'], actual['colors'][-1], actual['bg']), (model['cost'], model['price'], model['color'], model['battlegrounds']))
                code = "require $argv[1]; echo json_encode(card_tile_layout(json_decode(stream_get_contents(STDIN),true),$argv[2]));"
                for php in ['php', *(['/opt/php74/bin/php'] if Path('/opt/php74/bin/php').is_file() else [])]:
                    layout = json.loads(subprocess.check_output([php, '-r', code, str(ROOT / 'lib/card_tile_layout.php'), entity], input=json.dumps(row), text=True))
                    self.assertEqual((layout['cost']['text'], layout['badge']['text'], layout['background']['color']), (model['cost'], model['price'], model['color']))
                    self.assertEqual(layout['title']['text'], actual['name'])
                    self.assertEqual(layout['title']['overflow'], 'ellipsis')
                    self.assertNotIn('width', layout['title'], 'The app chooses the title width')
                    self.assertEqual(layout['art']['aspect_ratio'], 5)
                    self.assertEqual(bool(layout['cost']['icon']), not actual['bg'] and actual['cost'] != '—')
                    self.assertEqual(bool(layout['badge']['icon']), actual['price'] == '★')

    def test_render_and_asset_url_have_matching_signatures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(ROOT / 'assets/deck-tiles', root / 'assets/deck-tiles')
            art = root / 'uploads/horizontal.webp'
            art.parent.mkdir()
            Image.new('RGBA', (320, 64), '#1ac2dd').save(art, lossless=True)
            row = {'name_ru': 'Очень длинное название легендарной карты', 'mana_cost': 9, 'rarity': 'LEGENDARY', '_tile_art_path': '/uploads/horizontal.webp', '_tile_art_version': '2026-10-08 12:00:00'}
            asset = tiles.publish(row, 'constructed_card', 'test:card', root)
            picture = Image.open(root / asset['path'].lstrip('/'))
            self.assertEqual(picture.size, (640, 128))
            self.assertEqual(picture.mode, 'RGBA')
            self.assertLess(picture.getpixel((0, 0))[3], 4)
            self.assertEqual(picture.getpixel((575, 10))[:3], (113, 80, 34))
            # The legendary marker is a white star, including its pointed top.
            self.assertTrue(all(c > 200 for c in picture.getpixel((604, 45))[:3]))
            self.assertTrue(all(c > 200 for c in picture.getpixel((604, 64))[:3]))
            self.assertEqual(picture.getpixel((582, 40))[:3], (113, 80, 34))
            self.assertEqual(picture.getpixel((560, 12))[:3], (26, 194, 221))
            manifest = root / 'uploads/card-tiles/constructed_card/manifest.json'
            tiles.atomic_write(manifest, json.dumps({'assets': {'test:card': asset}}).encode())
            code = "require $argv[1]; $r=json_decode(stream_get_contents(STDIN),true); $a=['local_image_url'=>$r['_tile_art_path'],'generated_at'=>$r['_tile_art_version']]; echo json_encode([card_tile_signature($r,'constructed_card',$a),card_tile_asset_url($r,'constructed_card','test:card',$a,$argv[2])]);"
            def api(data):
                interpreters = ['php']
                if Path('/opt/php74/bin/php').is_file():
                    interpreters.append('/opt/php74/bin/php')
                results = [json.loads(subprocess.check_output([php, '-r', code, str(ROOT / 'lib/card_tile_assets.php'), str(root)], input=json.dumps(data), text=True)) for php in interpreters]
                for result in results[1:]:
                    self.assertEqual(result, results[0], 'Tile lookup must work in the production PHP 7.4 pool')
                return results[0]
            result = api(row)
            self.assertEqual(result, [asset['signature'], 'https://api.kolodahearthstone.com' + asset['path']])
            tiles.publish_record_index({'test:card': asset}, 'constructed_card', root)
            # Index lookup works without reading the full manifest.
            manifest.write_text('{invalid')
            self.assertEqual(api(row), result)
            changed = dict(row, mana_cost=0)
            self.assertIsNone(api(changed)[1])
            self.assertNotEqual(tiles.publish(changed, 'constructed_card', 'test:card', root)['path'], asset['path'])
            self.assertIsNone(api(dict(row, _tile_art_version='new'))[1])
            (root / asset['path'].lstrip('/')).unlink()
            self.assertIsNone(api(row)[1])
            asset['path'] = '/uploads/card-tiles/constructed_card/../../escape-1234567890abcdef.png'
            tiles.atomic_write(manifest, json.dumps({'assets': {'test:card': asset}}).encode())
            tiles.publish_record_index({'test:card': asset}, 'constructed_card', root)
            self.assertIsNone(api(row)[1])
            manifest.write_text('{invalid')
            self.assertIsNone(api(row)[1])

    def test_missing_art_and_invalid_local_paths(self):
        self.assertEqual(Image.open(io.BytesIO(tiles.render({'name': 'No art', 'mana_cost': 0}, 'constructed_card'))).size, (640, 128))
        for path in ['https://example.com/uploads/a.png', '/uploads/../../etc/passwd', '/etc/passwd']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                tiles.local_art({'_tile_art_path': path}, ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'uploads').mkdir()
            Image.new('RGB', (1, 1)).save(root / 'uploads/bad.png')
            with self.assertRaisesRegex(ValueError, '320x64'):
                tiles.render({'_tile_art_path': '/uploads/bad.png'}, 'constructed_card', root)

    def test_gradient_and_unknown_costs(self):
        image = tiles.gradient(['#49633f', '#28675f', '#75475a', '#605273'], (1280, 256))
        self.assertEqual(image.getpixel((0, 0))[:3], (73, 99, 63))
        self.assertEqual(image.getpixel((1279, 255))[3], 255)
        self.assertNotEqual(image.getpixel((640, 0)), image.getpixel((640, 255)))
        for value in [None, '', True, -1, 'NaN', 'Infinity', 1.5]:
            self.assertEqual(tiles.number(value), '—')


if __name__ == '__main__':
    unittest.main()
