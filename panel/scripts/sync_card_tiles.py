#!/opt/wiki-hs-parser/.venv/bin/python
"""Publish complete app tiles from local art; no network downloads or DB writes."""
from __future__ import annotations

import argparse
import concurrent.futures
import functools
import hashlib
import io
import json
import math
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PIL import Image, ImageDraw, ImageFilter, ImageFont, UnidentifiedImageError

ROOT = Path(os.environ.get('KOLODAHS_APP_ROOT') or Path(__file__).resolve().parents[1])
RECIPE = '1-app-png'
SIZE = (640, 128)
SCALE = 4
FIELDS = (
    'name', 'name_ru', 'name_en', 'name_russian', 'pet_name_ru', 'variant_name',
    'pet_name', 'coin_name_ru', 'coin_name_en', 'card_type', 'tavern_tier', 'tier',
    'mana_cost', 'cost', 'rarity', 'creature_type', 'creature_types', 'races',
    'minion_type', 'race', '_tile_art_path', '_tile_art_version',
)
TABLES = {
    'battleground_card': 'battlegrounds_cards', 'constructed_card': 'constructed_cards',
    'hero': 'battlegrounds_heroes', 'hero_skin': 'hero_skins', 'pet': 'hearthstone_pets',
    'coin': 'hearthstone_coins', 'timewarped_card': 'battlegrounds_timewarped_cards',
    'library_card': 'battlegrounds_library_cards',
}
TRIBE_COLORS = {
    'beast': '#49633f', 'demon': '#56375e', 'dragon': '#753f37', 'elemental': '#386074',
    'mech': '#46586e', 'murloc': '#28675f', 'naga': '#3b477b', 'pirate': '#795839',
    'quilboar': '#75475a', 'undead': '#59603c',
}


def cell(value: Any) -> str:
    if value is None or value is False or isinstance(value, (dict, list, tuple)):
        return ''
    return '1' if value is True else str(value)


def signature(row: dict, entity_type: str) -> str:
    values = [RECIPE, entity_type, *(cell(row.get(field)) for field in FIELDS)]
    return hashlib.sha256('\0'.join(values).encode()).hexdigest()


def number(value: Any, minimum: int = 0) -> str:
    if isinstance(value, bool):
        return '—'
    try:
        parsed = float(value) if cell(value).strip() else float('nan')
        return str(int(parsed)) if parsed.is_integer() and parsed >= minimum else '—'
    except (ValueError, TypeError, OverflowError):
        return '—'


def palette(row: dict) -> list[str]:
    tribes = set()
    for key in ('creature_types', 'races', 'creature_type', 'minion_type', 'race'):
        value = row.get(key)
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, dict):
                item = item.get('slug')
            if isinstance(item, str):
                tribes.update(re.split(r'[\s,;/|+]+', item.lower()))
    tribes = {'mech' if t == 'mechanical' else 'quilboar' if t == 'quillboar' else t for t in tribes}
    if 'all' in tribes:
        return ['#49633f', '#28675f', '#75475a', '#605273']
    return list(dict.fromkeys(TRIBE_COLORS[t] for t in sorted(tribes) if t in TRIBE_COLORS)) or ['#606367']


def tile(row: dict, entity_type: str) -> dict:
    bg = entity_type in ('battleground_card', 'timewarped_card')
    kind = cell(row.get('card_type') or ('minion' if bg else 'constructed')).lower()
    spell = kind in ('spell', 'battleground_spell', 'tavern_spell')
    rarity = cell(row.get('rarity')).upper()
    legendary = rarity in ('LEGENDARY', '5')
    colors = palette(row) if bg and not spell else ['#606367'] if bg else [
        '#715022' if legendary else '#503961' if rarity in ('EPIC', '4') else '#23445b' if rarity in ('RARE', '3') else '#606367'
    ]
    tier = row.get('tavern_tier') if row.get('tavern_tier') is not None else row.get('tier')
    mana = row.get('mana_cost') if row.get('mana_cost') is not None else row.get('cost')
    gold = row.get('cost') if row.get('cost') is not None else row.get('mana_cost')
    cost = number(tier, 1) if bg else number(mana)
    price = number(gold) if bg and spell else '3' if bg and kind == 'minion' else '' if bg else '★' if legendary else ''
    name = next((cell(row.get(key)) for key in ('name_ru', 'name', 'name_russian', 'name_en', 'pet_name_ru', 'variant_name', 'pet_name', 'coin_name_ru', 'coin_name_en') if row.get(key)), 'Без названия')
    return dict(name=name, cost=cost, price=price, colors=colors, bg=bg)


def local_art(row: dict, root: Path) -> Path | None:
    parsed = urlsplit(cell(row.get('_tile_art_path')))
    if parsed.scheme or parsed.netloc:
        raise ValueError('Art must be a local upload')
    path = parsed.path
    if not path:
        return None
    if not path.startswith('/uploads/'):
        raise ValueError('Art must be a local upload')
    candidate = (root / path.lstrip('/')).resolve()
    allowed = (root / 'uploads').resolve()
    if allowed not in candidate.parents:
        raise ValueError('Art path escapes uploads')
    return candidate if candidate.is_file() else None


@functools.lru_cache(maxsize=8)
def font(root: str, name: str, size: int):
    return ImageFont.truetype(str(Path(root) / 'assets/deck-tiles' / name), size * SCALE)


def ellipsis(name: str, face, width: int) -> str:
    if face.getlength(name) <= width:
        return name
    lo, hi = 0, len(name)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if face.getlength(name[:mid] + '…') <= width:
            lo = mid
        else:
            hi = mid - 1
    return name[:lo] + '…'


def draw_text(image: Image.Image, xy: tuple, text: str, face, color='#ffffff', stroke='#18130f', anchor='mm'):
    shadow = Image.new('RGBA', image.size)
    ImageDraw.Draw(shadow).text((xy[0], xy[1] + 2 * SCALE), text, font=face, fill='#000000', anchor=anchor, stroke_width=SCALE)
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(2 * SCALE)))
    ImageDraw.Draw(image).text(xy, text, font=face, fill=color, anchor=anchor, stroke_width=SCALE, stroke_fill=stroke)


def draw_star(image: Image.Image) -> None:
    # The bundled game fonts do not contain U+2605; render the actual icon.
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        radius = (13 if i % 2 == 0 else 5.5) * SCALE
        points.append((302 * SCALE + math.cos(angle) * radius, 32 * SCALE + math.sin(angle) * radius))
    shadow = Image.new('RGBA', image.size)
    ImageDraw.Draw(shadow).polygon([(x, y + 2 * SCALE) for x, y in points], fill='#000000')
    image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(SCALE)))
    draw = ImageDraw.Draw(image)
    draw.polygon(points, fill='#ffffff')
    draw.line(points + [points[0]], fill='#18130f', width=SCALE, joint='curve')


def gradient(colors: list[str], size: tuple[int, int]) -> Image.Image:
    # CSS linear-gradient(110deg): project each pixel onto its gradient axis.
    dx, dy = math.sin(math.radians(110)), -math.cos(math.radians(110))
    span = (size[0] - 1) * dx + (size[1] - 1) * dy
    stops = [0, .35, .7, 1] if colors == ['#49633f', '#28675f', '#75475a', '#605273'] else [i / (len(colors) - 1) for i in range(len(colors))]
    rgb = [tuple(bytes.fromhex(color.lstrip('#'))) for color in colors]
    strip = Image.new('RGBA', (math.ceil(span) + 1, 1))
    pixels = strip.load()
    for x in range(strip.width):
        position = min(1, x / span)
        index = next((i for i in range(len(stops) - 1) if position <= stops[i + 1]), len(stops) - 2)
        fraction = (position - stops[index]) / (stops[index + 1] - stops[index])
        pixels[x, 0] = (*[round(a + (b - a) * fraction) for a, b in zip(rgb[index], rgb[index + 1])], 255)
    return strip.transform(size, Image.Transform.AFFINE, (dx, dy, 0, 0, 0, 0))


def render(row: dict, entity_type: str, root: Path = ROOT) -> bytes:
    design = tile(row, entity_type)
    colors = design['colors']
    image = Image.new('RGBA', (320 * SCALE, 64 * SCALE), colors[0])
    if len(colors) > 1:
        image = gradient(colors, image.size)
    source = local_art(row, root)
    if source:
        with Image.open(source) as artwork:
            if artwork.size != (320, 64):
                raise ValueError('Horizontal art must be 320x64')
            image.alpha_composite(artwork.convert('RGBA').resize(image.size, Image.Resampling.LANCZOS), (-34 * SCALE, 0))
    # The opaque right column covers the two edge pixels exactly as in the panel.
    ImageDraw.Draw(image).rectangle((284 * SCALE, 0, image.width, image.height), fill=colors[-1])
    belwe = font(str(root), 'hearthstone-belwe.ttf', 28)
    if not design['bg'] and design['cost'] != '—':
        with Image.open(root / 'assets/deck-tiles/mana-crystal.png') as crystal:
            image.alpha_composite(crystal.convert('RGBA').resize((40 * SCALE, 42 * SCALE), Image.Resampling.LANCZOS), (0, 11 * SCALE))
    draw_text(image, (20 * SCALE, 32 * SCALE), design['cost'], belwe, '#ffe2a0' if design['bg'] else '#ffffff', '#392008' if design['bg'] else '#102746')
    face_name = 'hearthstone-benguiat.ttf' if re.search('[А-Яа-яЁё]', design['name']) else 'hearthstone-belwe.ttf'
    title_face = font(str(root), face_name, 21)
    title = ellipsis(design['name'], title_face, 236 * SCALE)
    draw_text(image, (48 * SCALE, 32 * SCALE), title, title_face, anchor='lm')
    if design['price'] == '★':
        draw_star(image)
    elif design['price']:
        draw_text(image, (302 * SCALE, 32 * SCALE), design['price'], font(str(root), 'hearthstone-benguiat.ttf', 27), '#ffe2a0' if design['bg'] else '#ffffff')
    mask = Image.new('L', image.size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, image.width-1, image.height-1), radius=3*SCALE, fill=255)
    image.putalpha(mask)
    output = io.BytesIO()
    image.resize(SIZE, Image.Resampling.LANCZOS).save(output, format='PNG', optimize=True)
    return output.getvalue()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.chmod(temporary, 0o644)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def publish(row: dict, entity_type: str, entity_id: str, root: Path = ROOT) -> dict:
    data = render(row, entity_type, root)
    safe = re.sub(r'[^A-Za-z0-9_.-]+', '_', entity_id).strip('._') or 'card'
    safe = safe[:100]
    content_hash = hashlib.sha256(data).hexdigest()[:16]
    path = f'/uploads/card-tiles/{entity_type}/{safe}-{content_hash}.png'
    atomic_write(root / path.lstrip('/'), data)
    return dict(path=path, signature=signature(row, entity_type), width=SIZE[0], height=SIZE[1])


def source_cost(payload: str) -> int | None:
    try:
        data = json.loads(payload)
        ru, en = data.get('ru') or {}, data.get('en') or {}
        value = ru.get('cost') if ru.get('cost') is not None else en.get('cost')
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        if isinstance(value, str) and not re.fullmatch(r'0|[1-9][0-9]*', value):
            return None
        parsed = int(value)
        return parsed if 0 <= parsed <= 2**63 - 1 else None
    except (ValueError, TypeError, AttributeError):
        return None


def publish_record_index(assets: dict, entity_type: str, root: Path = ROOT) -> None:
    directory = root / 'uploads/card-tiles' / entity_type / 'records'
    for entity_id, asset in assets.items():
        filename = hashlib.sha256(entity_id.encode()).hexdigest() + '.json'
        path = directory / filename
        content = json.dumps(asset, ensure_ascii=False, separators=(',', ':')).encode()
        if not path.is_file() or path.read_bytes() != content:
            atomic_write(path, content)
    atomic_write(directory / '.complete', b'1\n')


def collect(conn, entity_type: str) -> list[dict]:
    table = TABLES[entity_type]
    with conn.cursor() as cur:
        cur.execute(f'SHOW COLUMNS FROM `{table}`')
        columns = {column['Field'] for column in cur.fetchall()}
        selected = [key for key in (*FIELDS, 'card_id', 'library', 'variant_id') if key in columns]
        cur.execute('SELECT ' + ','.join(f'`{key}`' for key in selected) + f' FROM `{table}`')
        rows = list(cur.fetchall())
        if entity_type == 'battleground_card':
            spells = [row['card_id'] for row in rows if row.get('card_type') == 'spell']
            if spells:
                placeholders = ','.join(['%s'] * len(spells))
                cur.execute('SELECT history.card_id,history.payload_json FROM battlegrounds_card_changes AS history '
                            'JOIN (SELECT card_id,MAX(id) AS id FROM battlegrounds_card_changes '
                            f"WHERE source='hearthstonejson' AND card_id IN ({placeholders}) GROUP BY card_id) AS latest ON history.id=latest.id", spells)
                prices = {item['card_id']: source_cost(item['payload_json']) for item in cur.fetchall()}
                for row in rows:
                    if row.get('card_type') == 'spell':
                        row['cost'] = prices.get(row['card_id'])
        cur.execute("SELECT entity_id,local_image_url,generated_at FROM horizontal_art_assets WHERE entity_type=%s AND status='ready'", (entity_type,))
        arts = {str(item['entity_id']): item for item in cur.fetchall()}
    for row in rows:
        card_id = cell(row.get('card_id')) or f"variant:{row['variant_id']}"
        row['_entity_id'] = f"{row['library']}:{card_id}" if entity_type == 'library_card' else card_id
        art = arts.get(row['_entity_id'], {})
        row['_tile_art_path'] = cell(art.get('local_image_url'))
        row['_tile_art_version'] = cell(art.get('generated_at'))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entity', choices=('all', *TABLES), default='all')
    parser.add_argument('--card-id')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--force', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    from backfill_constructed_images import connect_db, load_php_config
    conn = connect_db(load_php_config())
    errors = 0
    try:
        for entity_type in TABLES if args.entity == 'all' else [args.entity]:
            rows = collect(conn, entity_type)
            if args.card_id:
                rows = [row for row in rows if row['_entity_id'] == args.card_id]
            manifest_path = ROOT / 'uploads/card-tiles' / entity_type / 'manifest.json'
            old = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
            assets = old.get('assets', {})
            pending = [row for row in rows if args.force or assets.get(row['_entity_id'], {}).get('signature') != signature(row, entity_type) or not (ROOT / assets.get(row['_entity_id'], {}).get('path', '').lstrip('/')).is_file()]
            print(json.dumps(dict(entity_type=entity_type, total=len(rows), generate=len(pending), reused=len(rows)-len(pending)), ensure_ascii=False), flush=True)
            if args.dry_run:
                continue
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
                tasks = {pool.submit(publish, row, entity_type, row['_entity_id']): row for row in pending}
                for task in concurrent.futures.as_completed(tasks):
                    row = tasks[task]
                    try:
                        assets[row['_entity_id']] = task.result()
                    except (OSError, ValueError, UnidentifiedImageError) as exc:
                        errors += 1
                        print(json.dumps(dict(entity_type=entity_type, id=row['_entity_id'], error=str(exc)), ensure_ascii=False), flush=True)
            atomic_write(manifest_path, json.dumps(dict(recipe=RECIPE, width=SIZE[0], height=SIZE[1], assets=assets), ensure_ascii=False, separators=(',', ':')).encode())
            publish_record_index(assets, entity_type)
    finally:
        conn.close()
    return int(errors > 0)


if __name__ == '__main__':
    raise SystemExit(main())
