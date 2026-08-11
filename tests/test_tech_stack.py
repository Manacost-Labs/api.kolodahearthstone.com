import unittest

from app.tech_stack import build_technologies_payload


class TechStackTest(unittest.TestCase):
    def test_technologies_payload_includes_site_kitchen(self) -> None:
        data = build_technologies_payload()

        self.assertGreaterEqual(data["count"], 10)
        names = {t["name"] for t in data["technologies"]}
        self.assertIn("FastAPI + Uvicorn", names)
        self.assertIn("playwright-stealth", names)
        self.assertGreaterEqual(data["site_count"], 7)

        sites = {site["key"]: site for site in data["sites"]}
        self.assertIn("hsreplay", sites)
        self.assertIn("hsguru", sites)
        self.assertIn("vicious-syndicate", sites)
        hsreplay_api_names = {api["name"] for api in sites["hsreplay"]["apis"]}
        self.assertIn("Meta archetypes", hsreplay_api_names)
        self.assertIn(
            "Firebase ladderData",
            {api["name"] for api in sites["vicious-syndicate"]["apis"]},
        )

        firestone = sites["firestone"]
        self.assertIn(
            "Standard meta overviews",
            {api["name"] for api in firestone["apis"]},
        )
        standard_api = next(
            api for api in firestone["apis"] if api["name"] == "Standard meta overviews"
        )
        self.assertIn("static.zerotoheroes.com", standard_api["url_pattern"])
        self.assertTrue(any("0..1" in strategy for strategy in standard_api.values()))
        self.assertTrue(
            any(
                "10 decks + 10 archetypes" in strategy
                for strategy in firestone["parser_strategy"]
            )
        )
        self.assertTrue(any("tos.md" in risk for risk in firestone["risks"]))

        hearthstone_decks = sites["hearthstone-decks"]
        self.assertIn(
            "WordPress REST posts",
            {api["name"] for api in hearthstone_decks["apis"]},
        )
        wordpress_api = next(
            api
            for api in hearthstone_decks["apis"]
            if api["name"] == "WordPress REST posts"
        )
        self.assertIn("wp-json/wp/v2/posts", wordpress_api["url_pattern"])
        self.assertTrue(
            any(
                "40" in strategy and "95%" in strategy
                for strategy in hearthstone_decks["parser_strategy"]
            )
        )


if __name__ == "__main__":
    unittest.main()
