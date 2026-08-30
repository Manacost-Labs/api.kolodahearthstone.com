from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.battlegrounds_comps_parse import _comps_from_html


class HsReplayCompsReactContextTest(unittest.TestCase):
    def test_publishes_only_visible_live_comps_with_current_core_cards(self) -> None:
        payload = [
            {
                "comp_id": 87,
                "comp_name": "Beasts - Tasty Lobstah",
                "comp_slug": "beasts-tasty-lobstah",
                "comp_tier": 1,
                "comp_difficulty": 2,
                "comp_core_cards": [132808, 133039],
                "comp_summary": "Scale the live Beast composition",
                "comp_hidden": False,
                "comp_last_updated": "2026-08-05T16:55:25.923Z",
            },
            {
                "comp_id": 2,
                "comp_name": "Mechs - Old Guide",
                "comp_slug": "mechs-old-guide",
                "comp_tier": 2,
                "comp_difficulty": 1,
                "comp_core_cards": [98592],
                "comp_hidden": True,
            },
            {
                "comp_id": 89,
                "comp_name": "Pirates - APM Golden",
                "comp_slug": "pirates-apm-golden",
                "comp_tier": 2,
                "comp_difficulty": 3,
                "comp_core_cards": [132925],
                "comp_hidden": False,
            },
            {
                "comp_id": 67,
                "comp_name": "Murlocs - APM",
                "comp_slug": "murlocs-apm",
                "comp_tier": 3,
                "comp_difficulty": 1,
                "comp_core_cards": [133026],
                "comp_hidden": False,
            },
        ]
        html = (
            '<html><script type="application/json" id="react_context">'
            + json.dumps(payload)
            + "</script></html>"
        )
        cards = {
            132808: {"id": "BG36_208", "dbfId": 132808, "name": "Deathrattle Runner"},
            133039: {"id": "BG36_210", "dbfId": 133039, "name": "Greedy Hyena"},
            132925: {"id": "BG36_750", "dbfId": 132925, "name": "Golden Pirate"},
            133026: {"id": "BG36_930", "dbfId": 133026, "name": "APM Murloc"},
        }

        with (
            patch("app.battlegrounds_comps_parse.cards_by_dbfid", return_value=cards),
            patch("app.battlegrounds_comps_parse.cards_by_id", return_value={}),
        ):
            comps = _comps_from_html(html)

        self.assertEqual([comp["comp_id"] for comp in comps], [87, 89, 67])
        self.assertEqual([comp["tier"] for comp in comps], ["S", "A", "B"])
        self.assertEqual(comps[0]["difficulty"], "Medium")
        self.assertEqual(
            [card["card_id"] for card in comps[0]["main_cards"]],
            ["BG36_208", "BG36_210"],
        )
        self.assertNotIn(2, [comp["comp_id"] for comp in comps])

    def test_reads_tier_column_from_rendered_flaresolverr_html(self) -> None:
        # FlareSolverr may return the rendered React tree without the JSON
        # bootstrap.  The tier marker is a sibling of a group of comp rows,
        # not part of each link's text.
        html = """
        <div class="tier-group">
          <div class="tier-marker">s</div>
          <div class="rows">
            <div class="row"><a href="https://hsreplay.net/battlegrounds/comps/2/mechs-magnetics">Mechs - Magnetics Medium</a></div>
            <div class="row"><a href="https://hsreplay.net/battlegrounds/comps/3/pirates-bounty-apm">Pirates - Bounty APM Hard</a></div>
          </div>
        </div>
        <div class="tier-group">
          <div class="tier-marker">b</div>
          <div class="rows">
            <div class="row"><a href="https://hsreplay.net/battlegrounds/comps/41/demons-shop-buff">Demons - Shop Buff Medium</a></div>
          </div>
        </div>
        """

        with (
            patch("app.battlegrounds_comps_parse.cards_by_dbfid", return_value={}),
            patch("app.battlegrounds_comps_parse.cards_by_id", return_value={}),
        ):
            comps = _comps_from_html(html)

        self.assertEqual([comp["comp_id"] for comp in comps], [2, 3, 41])
        self.assertEqual([comp["tier"] for comp in comps], ["S", "S", "B"])


if __name__ == "__main__":
    unittest.main()
