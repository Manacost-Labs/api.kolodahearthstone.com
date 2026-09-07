"""Isolated browser regressions: real PHP partials/JS, simulated data, no producers.

Run: make panel-browser-check (Python Playwright and Chromium required).
Optional PANEL_SCREENSHOT_DIR captures the tested layouts into an existing directory.
"""

import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import unittest
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import expect, sync_playwright


class PanelBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        panel = Path(__file__).resolve().parents[1]
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        cls.origin = f"http://127.0.0.1:{port}"
        cls.server = subprocess.Popen(
            ["php", "-S", f"127.0.0.1:{port}", "-t", str(panel)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cls.addClassCleanup(cls.stop_server)
        deadline = time.monotonic() + 5
        while True:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Local fixture server did not start")
                time.sleep(.05)
        cls.playwright = sync_playwright().start()
        cls.addClassCleanup(cls.playwright.stop)
        chromium = os.environ.get("PANEL_CHROMIUM") or shutil.which("chromium") or shutil.which("google-chrome")
        cls.browser = cls.playwright.chromium.launch(executable_path=chromium, headless=True)
        cls.addClassCleanup(cls.browser.close)

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        cls.server.wait(timeout=5)

    def setUp(self):
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1100}, reduced_motion="reduce")
        self.addCleanup(self.context.close)
        self.context.route("**/*", lambda route: route.continue_()
                           if urlsplit(route.request.url).netloc == urlsplit(self.origin).netloc else route.abort())
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.addCleanup(lambda: self.assertEqual(self.errors, []))

    def open_sources(self, query="", ready=True):
        self.page.goto(self.origin + "/tests/parser_control_fixture.php" + query)
        if ready:
            expect(self.page.locator("[data-parser-sources-body] tr")).to_have_count(7)
        return self.page

    def refresh(self):
        self.page.locator("[data-parser-refresh]").click()
        expect(self.page.locator("[data-parser-refresh]")).to_be_enabled()

    def assert_fits(self):
        self.assertFalse(self.page.evaluate("document.documentElement.scrollWidth > innerWidth"))
        self.assertFalse(self.page.locator(".parser-summary").evaluate("e => e.scrollWidth > e.clientWidth"))

    def test_responsive_shell_and_keyboard(self):
        page = self.open_sources()
        expect(page.locator("html")).to_have_attribute("data-theme", "light")
        expect(page.locator(".side-link[aria-current=page]")).to_have_text("Источники данных")
        for width in (1440, 1024, 768, 390, 320):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 1100})
                self.assert_fits()
                if width > 760:
                    expect(page.locator(".side-nav")).to_be_visible()
                    expect(page.locator("[data-sidebar-toggle]")).to_be_hidden()
                else:
                    expect(page.locator(".side-nav")).to_be_hidden()
                    page.locator("[data-sidebar-toggle]").click()
                    expect(page.locator(".side-nav")).to_be_visible()
                    expect(page.locator('[data-theme-option="dark"]')).to_be_visible()
                    page.keyboard.press("Escape")
                    expect(page.locator(".side-nav")).to_be_hidden()
                    expect(page.locator("[data-sidebar-toggle]")).to_be_focused()
                directory = os.environ.get("PANEL_SCREENSHOT_DIR")
                if directory and width in (1440, 390):
                    page.screenshot(path=str(Path(directory) / f"panel-sources-{width}.png"), full_page=True)
        page.keyboard.press("Control+k")
        expect(page.locator("[data-command-palette]")).to_be_visible()
        page.locator("[data-command-search]").fill("источники")
        expect(page.locator("[data-command-item]:visible")).to_have_count(2)
        page.keyboard.press("Escape")

    def test_filters_url_empty_and_reload(self):
        page = self.open_sources()
        page.locator("[data-parser-status]").select_option("fallback")
        expect(page.locator("[data-parser-sources-body] tr")).to_have_count(2)
        page.locator("[data-parser-search]").fill("HSGuru")
        expect(page.locator("[data-parser-sources-body] tr")).to_have_count(1)
        self.assertIn("source_q=HSGuru", page.url)
        page.reload()
        expect(page.locator("[data-parser-sources-body] tr")).to_have_count(1)
        expect(page.locator("[data-parser-status]")).to_have_value("fallback")
        page.locator("[data-parser-search]").fill("нет такого источника")
        expect(page.locator("[data-parser-empty]")).to_be_visible()
        page.locator("[data-parser-reset]").click()
        expect(page.locator("[data-parser-sources-body] tr")).to_have_count(7)
        self.assertNotIn("source_q", page.url)
        page.locator('[data-section-id="arena"]').click()
        expect(page.locator("[data-run-section]")).to_be_disabled()
        expect(page.locator(".parser-run-button")).to_be_disabled()
        expect(page.locator('[data-section-id="arena"]')).to_be_focused()

    def test_columns_survive_data_refresh_and_reload(self):
        page = self.open_sources()
        expect(page.locator(".parser-source-table th").nth(3)).to_be_hidden()
        picker = page.locator("[data-column-picker]")
        picker.locator("summary").click()
        picker.get_by_label("Записей", exact=True).check()
        picker.get_by_label("Последняя попытка", exact=True).uncheck()
        picker.locator("summary").click()
        self.refresh()
        expect(page.locator(".parser-source-table th").nth(2)).to_be_hidden()
        for row in page.locator("[data-parser-sources-body] tr").all():
            expect(row.locator("td").nth(2)).to_be_hidden()
            expect(row.locator("td").nth(3)).to_be_visible()
        page.reload()
        expect(page.locator("[data-parser-sources-body] tr")).to_have_count(7)
        expect(page.locator("[data-parser-sources-body] tr").first.locator("td").nth(2)).to_be_hidden()
        # Analytics rebuilds whole tables, not only their rows. Preferences must
        # attach to a replacement with the same header signature as well.
        page.evaluate("""() => {
            const table = document.querySelector('.parser-source-table');
            const replacement = table.cloneNode(true);
            replacement.querySelectorAll('[hidden]').forEach(e => e.removeAttribute('hidden'));
            table.replaceWith(replacement);
        }""")
        expect(page.locator("[data-parser-sources-body] tr").first.locator("td").nth(2)).to_be_hidden()
        page.locator("[data-parser-density]").click()
        page.reload()
        expect(page.locator("[data-parser-control]")).to_have_class("parser-workspace is-compact")

    def test_loading_error_retry_and_empty_registry(self):
        page = self.open_sources("?fixture_mode=error&fixture_delay=200", ready=False)
        expect(page.locator("[data-parser-summary]")).to_have_attribute("aria-busy", "true")
        expect(page.locator("[data-parser-alert]")).to_be_visible()
        expect(page.locator("[data-parser-summary]")).to_have_attribute("aria-busy", "false")
        expect(page.locator(".parser-skeleton-row")).to_have_count(0)
        page.evaluate("parserFixture.mode = 'success'")
        page.locator("[data-parser-retry]").click()
        expect(page.locator("[data-parser-sources-body] tr")).to_have_count(7)
        page.evaluate("parserFixture.mode = 'offline'")
        self.refresh()
        expect(page.locator("[data-parser-alert]")).to_be_visible()
        expect(page.locator("[data-parser-sources-body] tr")).to_have_count(7)
        expect(page.locator("[data-parser-updated]")).to_contain_text("последнее полученное состояние")
        page.evaluate("parserFixture.mode = 'empty'")
        self.refresh()
        expect(page.locator("[data-parser-empty-message]")).to_contain_text("В реестре пока нет")
        expect(page.locator("[data-parser-reset]")).to_be_hidden()

    def test_manual_run_confirmation_and_duplicate_guard(self):
        page = self.open_sources()
        page.locator(".parser-run-button").first.click()
        expect(page.locator("[data-run-dialog]")).to_be_visible()
        self.assertEqual(page.evaluate("parserFixture.calls.filter(c=>c.method==='POST').length"), 0)
        page.locator("[data-run-cancel]").click()
        page.locator(".parser-run-button").first.click()
        page.locator("[data-run-reason]").fill("Проверка интерфейса")
        page.evaluate("parserFixture.delay = 500")
        page.locator("[data-run-confirm]").click()
        expect(page.locator("[data-run-confirm]")).to_be_disabled()
        page.keyboard.press("Enter")
        expect(page.locator("[data-run-dialog]")).not_to_be_visible()
        expect(page.locator("[data-parser-run-feedback]")).to_contain_text("добавлен в очередь")
        posts = page.evaluate("parserFixture.calls.filter(c=>c.method==='POST')")
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["headers"]["X-CSRF-Token"], "0" * 64)
        self.assertIn('"source_ids":["new-source"]', posts[0]["body"])

    def test_malformed_refresh_preserves_last_valid_snapshot(self):
        page = self.open_sources()
        for bad in ("invalid", [None], [{"id": "x", "sources": "invalid"}], [{"id": "x", "sources": [None]}]):
            with self.subTest(sections=bad):
                page.evaluate("sections => { parserFixture.data = {...parserFixture.data, sections}; }", bad)
                self.refresh()
                expect(page.locator("[data-parser-alert]")).to_contain_text("некорректный реестр")
                expect(page.locator("[data-parser-sources-body] tr")).to_have_count(7)
        page.locator("[data-parser-status]").select_option("fallback")
        expect(page.locator("[data-parser-sources-body] tr")).to_have_count(2)

    def test_polling_preserves_keyboard_focus_without_stalling(self):
        page = self.page
        page.clock.install()
        self.open_sources()
        section = page.locator('[data-section-id="meta"]')
        section.click()
        count = page.evaluate("parserFixture.calls.length")
        page.clock.fast_forward(13000)
        page.wait_for_function("n => parserFixture.calls.length > n", arg=count)
        expect(page.locator("[data-parser-refresh]")).to_be_enabled()
        expect(section).to_be_focused()
        run = page.locator(".parser-run-button").first
        run.focus()
        count = page.evaluate("parserFixture.calls.length")
        page.clock.fast_forward(13000)
        page.wait_for_function("n => parserFixture.calls.length > n", arg=count)
        expect(page.locator("[data-parser-refresh]")).to_be_enabled()
        expect(run).to_be_focused()

    def test_search_during_initial_loading_does_not_invent_empty_registry(self):
        page = self.page
        page.clock.install()
        self.open_sources("?fixture_delay=5000", ready=False)
        page.locator("[data-parser-search]").fill("HSGuru")
        expect(page.locator("[data-parser-empty]")).to_be_hidden()
        expect(page.locator(".parser-skeleton-row")).to_have_count(6)
        page.clock.fast_forward(5100)
        expect(page.locator("[data-parser-sources-body] tr")).to_have_count(2)

    def test_manual_run_uncertain_result_never_auto_retries(self):
        page = self.open_sources()
        page.evaluate("parserFixture.mode = 'offline'")
        page.locator(".parser-run-button").first.click()
        page.locator("[data-run-confirm]").click()
        expect(page.locator("[data-run-status]")).to_contain_text("мог попасть в очередь")
        expect(page.locator("[data-run-confirm]")).to_be_disabled()
        expect(page.locator("[data-run-cancel]")).to_be_enabled()
        self.assertEqual(page.evaluate("parserFixture.calls.filter(c=>c.method==='POST').length"), 1)

    def test_safe_text_and_diagnostics(self):
        page = self.open_sources()
        page.evaluate("""() => {
            parserFixture.data.sections[0].sources[0].label = '<img src=x onerror=alert(1)>';
            parserFixture.data.sections[0].sources[1].lastError = '<script>bad()</script>';
        }""")
        self.refresh()
        expect(page.locator("[data-parser-sources-body] img, [data-parser-sources-body] script")).to_have_count(0)
        expect(page.locator("[data-parser-sources-body]")).to_contain_text("<img src=x onerror=alert(1)>")
        row = page.locator("[data-parser-sources-body] tr").filter(has_text="hsguru-meta-wild")
        row.locator("summary").click()
        expect(row.locator("pre")).to_have_text("<script>bad()</script>")

    def test_request_timeouts_and_diagnostics_pause_polling(self):
        page = self.page
        page.clock.install()
        self.open_sources("?fixture_delay=30000", ready=False)
        page.clock.fast_forward(16000)
        expect(page.locator("[data-parser-alert]")).to_contain_text("15 секунд")
        expect(page.locator("[data-parser-refresh]")).to_be_enabled()
        page.evaluate("parserFixture.delay = 0")
        self.refresh()
        expect(page.locator("[data-parser-sources-body] tr")).to_have_count(7)
        page.locator(".parser-source-diagnostics summary").first.click()
        calls = page.evaluate("parserFixture.calls.length")
        page.clock.fast_forward(13000)
        self.assertEqual(page.evaluate("parserFixture.calls.length"), calls)
        expect(page.locator(".parser-source-diagnostics[open]")).to_have_count(1)
        page.locator(".parser-run-button").first.click()
        page.evaluate("parserFixture.delay = 30000")
        page.locator("[data-run-confirm]").click()
        page.clock.fast_forward(21000)
        expect(page.locator("[data-run-status]")).to_contain_text("мог попасть в очередь")
        expect(page.locator("[data-run-cancel]")).to_be_enabled()
        expect(page.locator("[data-run-confirm]")).to_be_disabled()

    def open_catalog(self, query=""):
        self.page.goto(self.origin + "/tests/catalog_panel_fixture.php" + query)
        return self.page

    def test_catalog_filters_pagination_and_empty_reset(self):
        page = self.open_catalog("?q=BG_FIXTURE&card_type=minion&per_page=25&pool=1")
        expect(page.locator(".cards-table tbody tr")).to_have_count(25)
        page.locator(".pagination .page-next").first.click()
        self.assertIn("page=2", page.url)
        self.assertIn("q=BG_FIXTURE", page.url)
        self.assertIn("pool=1", page.url)
        expect(page.locator(".catalog-toolbar-head")).to_contain_text("26–50 из 120")
        page.locator('[name="tier"]').select_option("1")
        expect(page.locator(".cards-table tbody tr")).to_have_count(20)
        self.assertNotIn("page", parse_qs(urlsplit(page.url).query))
        expect(page.locator(".catalog-toolbar-head")).to_contain_text("1–20 из 20")
        page.locator("[data-filter-search]").fill("Несуществующая карта")
        expect(page.locator(".catalog-empty")).to_be_visible()
        page.locator(".catalog-empty a").click()
        expect(page.locator(".cards-table tbody tr")).to_have_count(50)
        expect(page.locator('[name="card_type"]')).to_have_value("minion")
        expect(page.locator("[data-filter-search]")).to_have_value("")
        expect(page.locator(".catalog-more")).not_to_have_attribute("open", "")

    def test_catalog_columns_density_keyboard_and_responsive_layout(self):
        page = self.open_catalog()
        expect(page.locator(".cards-table th:visible")).to_have_count(8)
        page.locator("[data-column-picker] summary").click()
        page.get_by_label("CARD_ID", exact=True).check()
        page.locator("[data-column-picker] summary").click()
        page.locator("[data-table-density]").click()
        page.reload()
        expect(page.locator(".cards-table th:visible")).to_have_count(9)
        expect(page.locator("[data-table-density]")).to_have_attribute("aria-pressed", "true")
        page.keyboard.press("/")
        expect(page.locator("[data-filter-search]")).to_be_focused()
        page.locator(".catalog-more summary").focus()
        page.keyboard.press("Enter")
        expect(page.locator('[name="tier"]')).to_be_visible()
        page.keyboard.press("Enter")
        expect(page.locator('[name="tier"]')).to_be_hidden()
        for width in (1440, 1024, 768, 390, 320):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 1100})
                self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"))
                expect(page.locator(".catalog-search-submit")).to_be_visible()
                directory = os.environ.get("PANEL_SCREENSHOT_DIR")
                if directory and width in (1440, 390):
                    page.evaluate("document.activeElement.blur(); window.scrollTo(0, 0)")
                    page.screenshot(path=str(Path(directory) / f"panel-catalog-{width}.png"), full_page=True)

    def test_catalog_advanced_filters_match_each_section(self):
        variants = {
            "": {"tier", "creature_type", "pool", "duos"},
            "constructed": {"constructed_format", "media"},
            "pet": {"tier", "media"}, "hero": {"media"},
            "hero_skin": {"rarity", "media"}, "coin": set(),
            "darkmoon_prize": {"tier", "pool"}, "anomaly": {"pool"},
            "timewarped": {"tier", "creature_type"},
        }
        for section, fields in variants.items():
            with self.subTest(section=section):
                page = self.open_catalog("?card_type=" + section)
                page.locator(".catalog-more summary").click()
                actual = page.locator(".catalog-more select").evaluate_all("els => els.map(e => e.name)")
                self.assertEqual(set(actual), fields | {"per_page"})
                if section in ("pet", "darkmoon_prize"):
                    expect(page.locator('[name="tier"] option')).to_have_count(5)

    def test_catalog_debounce_does_not_duplicate_manual_submission(self):
        page = self.open_catalog()
        page.clock.install()
        page.evaluate("""() => {
            window.submits = 0;
            document.querySelector('[data-autofilter]').addEventListener('submit', e => {
                e.preventDefault(); window.submits++;
            });
        }""")
        page.locator("[data-filter-search]").fill("М")
        page.clock.fast_forward(600)
        self.assertEqual(page.evaluate("submits"), 0)
        page.locator("[data-filter-search]").fill("Мурлок")
        page.locator(".catalog-search-submit").click()
        page.clock.fast_forward(600)
        self.assertEqual(page.evaluate("submits"), 1)
        page.locator("[data-filter-search]").fill("Мурлоки")
        page.locator('[name="card_type"]').select_option("minion")
        page.clock.fast_forward(600)
        self.assertEqual(page.evaluate("submits"), 2)

    def open_analytics(self, query=""):
        self.page.goto(self.origin + "/tests/analytics_panel_fixture.php" + query)
        expect(self.page.locator("[data-analytics-content]")).to_have_attribute("aria-busy", "false")
        return self.page

    def test_analytics_modules_filters_and_url_restore(self):
        page = self.open_analytics()
        expect(page.locator("h1")).to_have_text("Обзор и статистика")
        expect(page.locator("[data-analytics-controls]")).to_be_hidden()
        modules = page.locator("[data-analytics-module-select]")
        expect(modules.locator("option")).to_have_count(12)
        for module in ("meta", "hsguru_archetypes", "constructed_cards", "arena_cards", "archetypes", "decks", "bg_heroes", "bg_minions", "arena", "patches"):
            modules.select_option(module)
            expect(page.locator("[data-analytics-title]")).to_have_text("Тестовый набор: " + module)
            self.assertEqual(parse_qs(urlsplit(page.url).query)["stats"], [module])
        modules.select_option("constructed_cards")
        page.locator("[data-analytics-format]").select_option("wild")
        page.locator("[data-analytics-card-period]").select_option("3d")
        page.locator("[data-analytics-search]").fill("Fire Fly")
        page.locator("[data-analytics-controls] button[type=submit]").click()
        expect(page.locator("[data-analytics-content]")).to_have_attribute("aria-busy", "false")
        request = parse_qs(urlsplit(page.evaluate("analyticsFixture.calls.at(-1)")).query)
        self.assertEqual(request["q"], ["Fire Fly"])
        self.assertEqual(request["format"], ["wild"])
        self.assertEqual(request["card_period"], ["3d"])
        page.reload()
        expect(modules).to_have_value("constructed_cards")
        expect(page.locator("[data-analytics-search]")).to_have_value("Fire Fly")
        expect(page.locator("[data-analytics-format]")).to_have_value("wild")
        expect(page.locator("[data-analytics-card-period]")).to_have_value("3d")

    def test_analytics_card_search_and_detail_keyboard(self):
        page = self.open_analytics()
        detail = page.locator(".analytics-row-action button").first
        detail.click()
        drawer = page.locator("[data-analytics-detail-drawer]")
        expect(drawer).to_be_visible()
        page.keyboard.press("Escape")
        expect(drawer).to_be_hidden()
        expect(detail).to_be_focused()
        page.locator("[data-card-statistics-input]").fill("Fire Fly")
        page.locator("[data-card-statistics-form] button").click()
        expect(page.locator("[data-analytics-module-select]")).to_have_value("card")
        expect(page.locator("[data-analytics-title]")).to_have_text("Тестовый набор: card")
        request = parse_qs(urlsplit(page.evaluate("analyticsFixture.calls.at(-1)")).query)
        self.assertEqual(request["card_name"], ["Fire Fly"])

    def test_analytics_error_retry_empty_and_view_preferences(self):
        page = self.open_analytics()
        picker = page.locator("[data-column-picker]")
        picker.locator("summary").click()
        picker.get_by_label("Набор", exact=True).uncheck()
        picker.locator("summary").click()
        page.locator("[data-table-density]").click()
        page.evaluate("analyticsFixture.mode = 'error'")
        page.locator("[data-analytics-refresh]").click()
        expect(page.locator(".analytics-empty.is-error")).to_contain_text("Тестовая ошибка сервиса")
        page.evaluate("analyticsFixture.mode = 'success'")
        page.locator(".analytics-empty button").click()
        expect(page.locator(".analytics-table tbody tr")).to_have_count(5)
        expect(page.locator('.analytics-table th[data-column="dataset"]')).to_be_hidden()
        page.reload()
        expect(page.locator("[data-table-density]")).to_have_attribute("aria-pressed", "true")
        expect(page.locator('.analytics-table th[data-column="dataset"]')).to_be_hidden()
        page.evaluate("analyticsFixture.mode = 'empty'")
        page.locator("[data-analytics-refresh]").click()
        expect(page.locator(".analytics-empty")).to_contain_text("Нет данных по выбранным фильтрам")
        expect(page.locator(".analytics-table")).to_have_count(0)

    def test_analytics_responsive_layout(self):
        page = self.open_analytics()
        for width in (1440, 1024, 768, 390, 320):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 1100})
                self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"))
                expect(page.locator("[data-analytics-module-select]")).to_be_visible()
                expect(page.locator("[data-analytics-refresh]")).to_be_visible()
                directory = os.environ.get("PANEL_SCREENSHOT_DIR")
                if directory and width in (1440, 390):
                    page.evaluate("window.scrollTo(0, 0)")
                    page.screenshot(path=str(Path(directory) / f"panel-analytics-{width}.png"), full_page=True)

    def test_saved_themes_and_other_modules(self):
        page = self.open_sources()
        page.locator('[data-theme-option="dark"]').click()
        page.reload()
        expect(page.locator("html")).to_have_attribute("data-theme", "dark")
        for module in ("catalog", "analytics", "api_token"):
            with self.subTest(module=module):
                page.goto(self.origin + f"/tests/{module}_panel_fixture.php")
                expect(page.locator(".side-link[aria-current=page]")).to_have_count(1)
                expect(page.locator("[data-command-open]")).to_be_visible()
                page.locator('[data-theme-option="light"]').click()
                for width in (1440, 768, 390):
                    page.set_viewport_size({"width": width, "height": 1100})
                    self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"))
                page.set_viewport_size({"width": 1440, "height": 1100})


if __name__ == "__main__":
    unittest.main(verbosity=2)
