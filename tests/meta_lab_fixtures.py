from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hearthstone.deckstrings import write_deckstring
from hearthstone.enums import FormatType

from app.meta_intelligence.models import now_iso
from app.meta_intelligence.snapshots import ingest
from app.meta_intelligence.store import Store

FIXTURE = json.loads((Path(__file__).parent / "fixtures/meta_lab.json").read_text())


def dataset(at: str | None = None, *, format: str = "standard", rank: str = "legend", period: str = "past_week", patch: str = "patch_36.0.3", details: bool = False) -> dict:
    at = at or now_iso()
    rows = deepcopy(FIXTURE["archetypes"])
    if details:
        for index, row in enumerate(rows):
            decks = []
            for variant, (wr,games) in enumerate(((55.4,4210),(54.1,3670),(59.2,81))):
                cards = [(1000+i,2) for i in range(1,16)]
                if variant:
                    cards[-1] = (1100+variant,2)
                code = write_deckstring(cards, [7], FormatType.FT_STANDARD if format == "standard" else FormatType.FT_WILD)
                decks.append({"deck_code":code,"win_rate":wr-index*.4,"games":games,"updated_at":at,"url":f"https://www.hsguru.com/deck/{100+index*3+variant}"})
            row.update(decks=decks,card_stats=deepcopy(FIXTURE["cards"]),class_matchups=deepcopy(FIXTURE["class_matchups"]),card_stats_updated_at=at,matchups_updated_at=at)
            # Details do not duplicate meta metrics from another source.
            for key in ("winrate","games","popularity_pct"):
                row.pop(key)
    return {"state":"ok","fetched_at":at,"backend":"scrape_do","data":{"structured":{
        "type":"meta_lab_details" if details else "meta","schema_version":1,"expected_hours":24 if details else 4,
        "criteria":{"format":format,"rank":rank,"period":period,"patch":patch,"patch_scope":"single_patch"},
        "archetypes" if details else "strategies":rows,
    }}}


def publish(store: Store, payload: dict, source: str = "hsguru_lab_meta_standard_legend_past_week", **kwargs):
    return ingest(store, source, payload, {"state":"ok","fetched_at":payload["fetched_at"]}, **kwargs)


def seed(store: Store) -> None:
    from app.meta_intelligence.budget import opening_balance
    from app.meta_intelligence.signals import recompute
    now = datetime.now(UTC)-timedelta(minutes=5)
    for fmt in ("standard","wild"):
        for hours in (8,4,0):
            payload = dataset((now-timedelta(hours=hours)).isoformat(),format=fmt)
            payload["data"]["structured"]["strategies"][0]["popularity_pct"] = 8.2-hours*.3
            payload["data"]["structured"]["strategies"][0]["winrate"] = 54.2-hours*.15
            publish(store,payload,source=f"hsguru_lab_meta_{fmt}_legend_past_week")
        publish(store,dataset(now.isoformat(),format=fmt,details=True),source=f"hsguru_lab_details_{fmt}_legend_past_week")
    opening_balance(store,now.strftime("%Y-%m"),408_250)
    with store.connect(write=True) as conn:
        conn.execute("INSERT INTO lab_meta VALUES('demo','true')")
    recompute(store)

META_HTML = '\n<table>\n  <tr>\n    <th>Archetype</th><th>Winrate↓</th><th>Popularity</th>\n    <th>Turns</th><th>Duration</th><th>Climbing Speed</th>\n  </tr>\n  <tr>\n    <td>Quest Mage</td><td>52.4</td><td>4.2% (1,234)</td>\n    <td>8.5</td><td>8.1</td><td>0.42⭐/h</td>\n  </tr>\n  <tr>\n    <td>Tiny Rogue</td><td>70.0</td><td>0.1% (49)</td>\n    <td>5.0</td><td>4.0</td><td>2.0⭐/h</td>\n  </tr>\n</table>\n'
