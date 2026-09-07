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
        # Legacy table regressions stay explicit; reader defaults have their own coverage.
        self.context.add_init_script("localStorage.setItem('panel-catalog-view', 'table')")
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
        expect(page.locator('select[name="card_type"]')).to_have_value("minion")
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
                for navigation in page.locator('.data-panel .pagination').all():
                    self.assertFalse(navigation.evaluate('e => e.scrollWidth > e.clientWidth'),
                                     'Pagination and page-jump controls must not be clipped')
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
                self.assertEqual(set(actual), fields)
                expect(page.locator('.catalog-browse-controls [name="per_page"]')).to_be_visible()
                expect(page.locator('.catalog-browse-controls [name="sort"]')).to_be_visible()
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

    def test_catalog_sort_jump_and_reload_preserve_filters(self):
        page = self.open_catalog("?q=BG_FIXTURE&card_type=minion&per_page=25&pool=0&page=2")
        page.locator('[name="sort"]').select_option("updated_desc")
        expect(page.locator('.cards-table tbody tr').first).to_contain_text('BG_FIXTURE_120')
        self.assertNotIn('page', parse_qs(urlsplit(page.url).query))
        jump = page.locator('.catalog-page-jump').first
        jump.locator('[name="page"]').fill('4')
        jump.get_by_role('button', name='Перейти').click()
        expect(page.locator('.catalog-toolbar-head')).to_contain_text('76–100 из 120')
        params = parse_qs(urlsplit(page.url).query)
        for key, value in {'q':'BG_FIXTURE', 'card_type':'minion', 'per_page':'25', 'pool':'0', 'sort':'updated_desc', 'page':'4'}.items():
            self.assertEqual(params[key], [value])
        page.reload()
        expect(page.locator('select[name="sort"]')).to_have_value('updated_desc')
        expect(page.locator('.cards-table tbody tr').first).to_contain_text('BG_FIXTURE_45')
        jump.locator('[name="page"]').fill('999')
        jump.get_by_role('button', name='Перейти').click()
        self.assertIn('page=4', page.url)
        expect(page.locator('.catalog-toolbar-head')).to_contain_text('76–100 из 120')

    def test_catalog_navigation_without_javascript(self):
        with self.browser.new_context(java_script_enabled=False) as context:
            page = context.new_page()
            page.goto(self.origin + '/tests/catalog_panel_fixture.php?per_page=25&pool=0')
            page.locator('[name="sort"]').select_option('updated_desc')
            page.get_by_role('button', name='Найти', exact=True).click()
            expect(page.locator('.cards-table tbody tr').first).to_contain_text('BG_FIXTURE_120')
            jump = page.locator('.catalog-page-jump').last
            jump.locator('[name="page"]').fill('3')
            jump.get_by_role('button', name='Перейти').click()
            expect(page.locator('.catalog-toolbar-head')).to_contain_text('51–75 из 120')
            self.assertIn('pool=0', page.url)
            self.assertIn('sort=updated_desc', page.url)

    def test_catalog_ime_and_unchanged_search_do_not_reload(self):
        page = self.open_catalog('?q=BG_FIXTURE')
        page.clock.install()
        page.evaluate("""() => {
            window.submits = 0;
            document.querySelector('[data-autofilter]').addEventListener('submit', e => {
                e.preventDefault(); window.submits++;
            });
        }""")
        search = page.locator('[data-filter-search]')
        search.fill('BG_FIXTURE ')
        page.clock.fast_forward(600)
        self.assertEqual(page.evaluate('submits'), 0)
        search.dispatch_event('compositionstart')
        search.fill('Мурлок')
        page.clock.fast_forward(600)
        self.assertEqual(page.evaluate('submits'), 0)
        search.dispatch_event('compositionend')
        page.clock.fast_forward(600)
        self.assertEqual(page.evaluate('submits'), 1)

    def test_catalog_network_script_budget(self):
        page = self.open_catalog()
        resources = page.evaluate("""performance.getEntriesByType('resource')
            .filter(r => r.initiatorType === 'script' && new URL(r.name).pathname.startsWith('/assets/'))
            .map(r => ({path: new URL(r.name).pathname, bytes:r.decodedBodySize}))""")
        self.assertEqual({r['path'] for r in resources}, {
            '/assets/workspace.js', '/assets/panel-ui.js', '/assets/table-controls.js', '/assets/media-preview.js',
            '/assets/catalog-reader.js'})
        size = sum(r['bytes'] for r in resources)
        self.assertGreater(size, 0)
        self.assertLess(size, 44000)
        print(f'Catalogue browser script budget: {len(resources)} requests, {size} decoded bytes')

    catalog_variants = ("", "minion", "spell", "constructed", "hero", "hero_skin", "pet", "coin",
                        "timewarped", "anomaly", "quest", "darkmoon_prize", "reward", "trinket")

    def open_catalog_variant(self, section, extra=""):
        self.context.add_init_script("localStorage.setItem('panel-catalog-view', 'table')")
        response = self.page.goto(self.origin + "/tests/catalog_variants_fixture.php?card_type=" + section + extra)
        self.assertEqual(response.status, 200)
        self.assertNotIn("Fatal error", self.page.content())
        return self.page

    def open_reader(self, section="", extra=""):
        self.page.goto(self.origin + "/tests/catalog_variants_fixture.php?card_type=" + section + extra)
        expect(self.page.locator(".catalog-reader")).to_be_visible()
        return self.page

    def test_reader_all_variants_default_and_complete_fields(self):
        for section in self.catalog_variants:
            with self.subTest(section=section):
                page = self.open_reader(section)
                expect(page.locator(".cards-table")).to_be_hidden()
                expect(page.locator("[data-reader-record]")).to_have_count(2)
                expect(page.locator("[data-reader-title]")).not_to_be_empty()
                expect(page.locator("[data-reader-stage] img")).to_be_visible()
                self.assertEqual(page.locator(".catalog-reader details, .catalog-reader form").count(), 0)
                labels = page.locator("[data-reader-field] option").all_text_contents()
                for index in range(1, len(labels)):
                    page.locator("[data-reader-field]").select_option(str(index - 1))
                    expect(page.locator("[data-reader-data]")).to_be_visible()
                    self.assertEqual(page.locator("[data-reader-data] details, [data-reader-data] [hidden]").count(), 0)
                page.locator("[data-reader-next]").click()
                expect(page.locator("[data-reader-position]")).to_have_text("2 / 2")
                expect(page.locator("[data-reader-next]")).to_be_disabled()

    def test_reader_gallery_paging_fullscreen_and_keyboard(self):
        page = self.open_reader("constructed", "&many_media=1")
        expect(page.locator("[data-reader-media-count]")).to_have_text("1 / 15")
        expect(page.locator("[data-reader-thumb]")).to_have_count(6)
        page.locator("[data-reader-media-page-next]").click()
        expect(page.locator("[data-reader-thumb]").first).to_have_attribute("data-reader-thumb", "6")
        page.locator("[data-reader-thumb]").last.click()
        expect(page.locator("[data-reader-stage] img")).to_have_attribute("src", self.origin + "/tests/catalog-art.svg?art=11")
        trigger = page.locator("[data-reader-stage] [data-preview]")
        trigger.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#fullscreenCard")).to_be_visible()
        page.keyboard.press("Escape")
        expect(trigger).to_be_focused()
        page.locator("[data-reader-media-page-next]").click()
        expect(page.locator("[data-reader-thumb]")).to_have_count(3)
        page.locator("[data-reader-thumb]").last.click()
        expect(page.locator("[data-reader-stage] img")).to_have_attribute("src", self.origin + "/tests/catalog-art.svg?art=14")
        expect(page.locator("[data-reader-media-next]")).to_be_disabled()
        page.set_viewport_size({"width": 320, "height": 1100})
        for thumbnail in page.locator("[data-reader-thumb]").all():
            box = thumbnail.bounding_box()
            self.assertGreaterEqual(box["width"], 44)
            self.assertGreaterEqual(box["height"], 44)
        page.set_viewport_size({"width": 1440, "height": 1100})
        page.locator("[data-reader-record]").first.focus()
        page.keyboard.press("ArrowDown")
        expect(page.locator("[data-reader-record]").nth(1)).to_be_focused()
        expect(page.locator("[data-reader-position]")).to_have_text("2 / 2")
        page.get_by_role("button", name="Таблица", exact=True).click()
        expect(page.locator(".cards-table")).to_be_visible()
        page.reload()
        expect(page.locator(".cards-table")).to_be_visible()
        page.get_by_role("button", name="Просмотр", exact=True).click()
        expect(page.locator(".catalog-reader")).to_be_visible()

    def test_reader_responsive_empty_media_and_bounded_list(self):
        page = self.open_reader("hero")
        expect(page.locator("[data-reader-media-navigation]")).to_be_hidden()
        expect(page.locator("[data-reader-thumbs]")).to_be_hidden()
        for width in (1440, 1024, 768, 390, 320):
            page.set_viewport_size({"width": width, "height": 1100})
            self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"))
            self.assertTrue(page.locator(".catalog-reader").evaluate("e => e.scrollWidth <= e.clientWidth + 1"))
            if width <= 760:
                expect(page.locator("[data-reader-list]")).to_be_hidden()
                page.locator("[data-reader-select]").select_option("1")
                expect(page.locator("[data-reader-position]")).to_have_text("2 / 2")
            directory = os.environ.get("PANEL_SCREENSHOT_DIR")
            if directory and width in (1440, 390):
                page.screenshot(path=str(Path(directory) / f"panel-reader-{width}.png"), full_page=True)
        self.open_reader("hero", "&no_media=1")
        expect(page.locator("[data-reader-stage]")).to_contain_text("Нет изображений")
        page.goto(self.origin + "/tests/catalog_variants_fixture.php?empty=1")
        expect(page.locator(".catalog-reader")).to_have_count(0)
        expect(page.locator(".cards-table .empty")).to_be_visible()
        page.goto(self.origin + "/tests/catalog_panel_fixture.php?per_page=150")
        page.set_viewport_size({"width": 1440, "height": 1100})
        expect(page.locator("[data-reader-record]")).to_have_count(120)
        self.assertLess(page.locator("[data-reader-list]").bounding_box()["height"], 650)

    def test_reader_related_fields_sounds_and_no_mutation_forms(self):
        page = self.open_reader("hero", "&rich_data=1")
        expect(page.locator("[data-reader-data]")).to_contain_text("Ледяное пламя")
        page.locator("[data-reader-field]").select_option(label="Buddy")
        expect(page.locator("[data-reader-data]")).to_contain_text("Приветствие компаньона")
        expect(page.locator("[data-reader-data] details")).to_have_count(0)
        page.locator("[data-reader-thumb]").last.click()
        expect(page.locator("[data-reader-stage] audio")).to_have_attribute("preload", "none")
        self.assertTrue(page.locator("[data-reader-stage] audio").evaluate("e => e.paused"))
        page.locator("[data-reader-next]").click()
        expect(page.locator("[data-reader-stage] audio")).to_have_count(0)
        self.open_reader("constructed", "&rich_data=1")
        page.locator("[data-reader-field]").select_option(label="Patch changes")
        expect(page.locator("[data-reader-data]")).to_contain_text("Атака увеличена на 1.")
        page.locator("[data-reader-thumb]").last.click()
        expect(page.locator("[data-reader-stage] audio")).to_be_visible()
        expect(page.locator("[data-reader-stage] audio")).to_have_attribute("aria-label", "Приветствие: Приветствие компаньона audio")
        expect(page.locator("[data-reader-caption]")).to_contain_text("Приветствие компаньона")
        self.open_reader("")
        page.locator("[data-reader-field]").select_option(label="Wiki")
        expect(page.locator("[data-reader-data]")).to_contain_text("Тестовый художник")
        self.assertNotIn("Действия", page.locator("[data-reader-field] option").all_text_contents())
        expect(page.locator(".catalog-reader form, .catalog-reader input[name=csrf]")).to_have_count(0)
        expect(page.locator('.catalog-reader a[href*="action=edit"]')).to_have_count(0)
        self.assertGreater(page.locator(".cards-table form").count(), 0)
        self.open_reader("hero_skin", "&rich_data=1")
        page.locator("[data-reader-thumb]").get_by_text("Видео", exact=True).click()
        expect(page.locator("[data-reader-stage] video")).to_have_attribute("preload", "none")
        self.assertTrue(page.locator("[data-reader-stage] video").evaluate("e => e.paused"))

    def test_reader_preserves_relationship_titles_and_safe_text(self):
        page = self.open_reader("coin", "&rich_data=1")
        page.locator("[data-reader-field]").select_option(label="Создаётся · 1")
        for value in ("GENERATED_1", "Создатель монетки", "RELATED_1", "Хранитель сокровищ"):
            expect(page.locator("[data-reader-data]")).to_contain_text(value)
        expect(page.locator("[data-reader-data] details")).to_have_count(0)
        self.open_reader("hero_skin", "&unsafe_data=1")
        page.locator("[data-reader-field]").select_option(label="Характеристики")
        expect(page.locator("[data-reader-data]")).to_contain_text('<img src=x onerror=alert(1)>')
        expect(page.locator("[data-reader-data] img, [data-reader-data] [onerror], [data-reader-data] [id]")).to_have_count(0)
        expect(page.locator('[data-reader-data] a[href^="javascript:"]')).to_have_count(0)

    def test_reader_broken_images_and_unavailable_module_fallback(self):
        self.page.route("**/tests/catalog-art.svg*", lambda route: route.abort())
        page = self.open_reader()
        expect(page.locator("[data-reader-stage]")).to_contain_text("Изображение недоступно")
        page.locator("[data-reader-next]").click()
        expect(page.locator("[data-reader-position]")).to_have_text("2 / 2")
        page.route("**/assets/catalog-reader.js*", lambda route: route.abort())
        page.reload()
        expect(page.locator(".cards-table")).to_be_visible()
        expect(page.locator(".catalog-reader")).to_have_count(0)
        expect(page.locator("[data-column-picker]")).to_be_visible()


    def test_real_catalogue_variants_and_responsive_galleries(self):
        for section in self.catalog_variants:
            with self.subTest(section=section):
                page = self.open_catalog_variant(section)
                gallery = section in ("hero_skin", "pet", "coin")
                expect(page.locator("h1")).to_have_count(1)
                expect(page.locator(".side-link[aria-current=page]")).to_have_count(1)
                expect(page.locator(".skin-card" if gallery else ".cards-table tbody tr")).to_have_count(2)
                if gallery:
                    expect(page.locator("[data-column-picker], [data-table-density], [data-table-navigation]")).to_have_count(0)
                    self.assertTrue(page.locator(".cards-table").evaluate("e => e.scrollHeight <= e.clientHeight + 1"))
                else:
                    expect(page.locator("[data-column-picker]")).to_be_visible()
                    self.assertEqual(page.locator(".cards-table th:not([scope=col])").count(), 0)
                    self.assertGreater(page.locator(".cards-table th[hidden]").count(), 0)
                    self.assertEqual(page.locator(".cards-table td:not([hidden])").evaluate_all(
                        "els => els.filter(e => getComputedStyle(e).display !== 'table-cell').map(e => e.className)"), [], section)
                for width in (1440, 1024, 768, 390, 320):
                    page.set_viewport_size({"width": width, "height": 1100})
                    self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth"), (section, width))
                    if gallery:
                        self.assertTrue(page.locator(".skin-card").evaluate_all("els => els.every(e => e.scrollWidth <= e.clientWidth + 1)"), (section, width))
                    directory = os.environ.get("PANEL_SCREENSHOT_DIR")
                    if directory and width in (1440, 390):
                        page.screenshot(path=str(Path(directory) / f"panel-catalog-{section or 'bg'}-{width}.png"), full_page=True)
                page.set_viewport_size({"width": 1440, "height": 1100})

    def test_real_catalogue_empty_and_missing_media_variants(self):
        for section in self.catalog_variants:
            with self.subTest(section=section):
                page = self.open_catalog_variant(section, "&empty=1")
                expect(page.locator(".cards-table .empty")).to_be_visible()
                expect(page.locator(".cards-table [data-preview]")).to_have_count(0)
                self.open_catalog_variant(section, "&no_media=1")
                expect(page.locator(".cards-table img")).to_have_count(0)
                expect(page.locator(".missing-card-image")).to_have_count(4 if section == "trinket" else 2)

    def test_real_catalogue_previews_keyboard_and_details(self):
        for section in self.catalog_variants:
            with self.subTest(section=section):
                page = self.open_catalog_variant(section)
                preview = page.locator(".cards-table [data-preview]:visible").first
                preview.focus()
                page.keyboard.press("Enter")
                dialog = page.get_by_role("dialog", name="Просмотр изображения")
                expect(dialog).to_be_visible()
                expect(dialog.locator("img")).to_have_attribute("src", expect_string := preview.get_attribute("data-preview"))
                self.assertTrue(expect_string.startswith("/tests/catalog-art.svg"))
                close = dialog.get_by_role("button", name="Закрыть")
                expect(close).to_be_focused()
                page.keyboard.press("Tab")
                expect(close).to_be_focused()
                page.keyboard.press("Shift+Tab")
                expect(close).to_be_focused()
                expect(page.locator("main.shell")).to_have_attribute("inert", "")
                page.keyboard.press("/")
                expect(close).to_be_focused()
                page.keyboard.press("Control+k")
                expect(page.locator("[data-command-palette]")).not_to_be_visible()
                page.keyboard.press("Escape")
                expect(dialog).to_be_hidden()
                expect(preview).to_be_focused()
                expect(page.locator("main.shell")).not_to_have_attribute("inert", "")
                details = page.locator(".cards-table details:visible").first
                if details.count():
                    details.locator("summary").first.click()
                    expect(details).to_have_attribute("open", "")

    def open_analytics(self, query=""):
        self.page.goto(self.origin + "/tests/analytics_panel_fixture.php" + query)
        expect(self.page.locator("[data-analytics-content]")).to_have_attribute("aria-busy", "false")
        return self.page

    def test_analytics_images_open_and_restore_keyboard_focus(self):
        page = self.open_analytics('?stats=bg_heroes')
        page.evaluate("""() => {
            analyticsFixture.payload.columns = [
                {key:'hero',label:'Герой'}, {key:'image',label:'Изображение',type:'image'},
                {key:'winrate',label:'Победы',type:'number'}];
            analyticsFixture.payload.rows = [
                {hero:'Тестовый герой', image:'/tests/catalog-art.svg', winrate:51}];
        }""")
        page.locator('[data-analytics-refresh]').click()
        preview = page.get_by_role('button', name='Открыть изображение Тестовый герой на весь экран')
        expect(preview).to_be_visible()
        preview.focus()
        page.keyboard.press('Enter')
        dialog = page.get_by_role('dialog', name='Просмотр изображения')
        expect(dialog).to_be_visible()
        expect(dialog.locator('img')).to_have_attribute('src', self.origin + '/tests/catalog-art.svg')
        close = dialog.get_by_role('button', name='Закрыть')
        expect(close).to_be_focused()
        page.keyboard.press('Tab')
        expect(close).to_be_focused()
        page.keyboard.press('Escape')
        expect(dialog).to_be_hidden()
        expect(preview).to_be_focused()
        expect(page.locator('main.shell')).not_to_have_attribute('inert', '')

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

    def test_analytics_late_response_cannot_replace_selected_module(self):
        page = self.open_analytics()
        page.clock.install()
        page.evaluate("analyticsFixture.delay = 2000; analyticsFixture.ignoreAbort = true")
        page.locator("[data-analytics-module-select]").select_option("meta")
        page.evaluate("analyticsFixture.delay = 0")
        page.locator("[data-analytics-module-select]").select_option("arena")
        expect(page.locator("[data-analytics-title]")).to_have_text("Тестовый набор: arena")
        page.clock.fast_forward(2500)
        expect(page.locator("[data-analytics-title]")).to_have_text("Тестовый набор: arena")
        expect(page.locator(".analytics-table")).to_have_attribute("data-module", "arena")

    def test_analytics_timeout_and_malformed_response_are_retryable(self):
        page = self.open_analytics()
        page.clock.install()
        page.evaluate("analyticsFixture.delay = 30000; analyticsFixture.ignoreAbort = true")
        page.locator("[data-analytics-refresh]").click()
        expect(page.locator("[data-analytics-content]")).to_have_attribute("aria-busy", "true")
        page.clock.fast_forward(16000)
        expect(page.locator(".analytics-empty.is-error")).to_contain_text("15 секунд")
        expect(page.locator("[data-analytics-refresh]")).to_be_enabled()
        page.clock.fast_forward(16000)
        expect(page.locator(".analytics-empty.is-error")).to_be_visible()
        page.evaluate("analyticsFixture.delay = 0; analyticsFixture.payload.rows = [null]")
        page.locator(".analytics-empty button").click()
        expect(page.locator(".analytics-empty.is-error")).to_contain_text("некорректный набор")
        page.evaluate("analyticsFixture.payload.rows = [{source: 'Recovered'}]")
        page.locator(".analytics-empty button").click()
        expect(page.locator(".analytics-table tbody tr")).to_have_count(1)

    def test_analytics_duos_reload_and_invalid_period(self):
        page = self.open_analytics("?stats=bg_heroes&stats_mode=duos&stats_rating=10")
        expect(page.locator("[data-analytics-mode]")).to_have_value("duos")
        expect(page.locator("[data-analytics-rating]")).to_have_value("10")
        page.locator("[data-analytics-mode]").select_option("solo")
        page.reload()
        expect(page.locator("[data-analytics-mode]")).to_have_value("solo")
        self.open_analytics("?stats=meta&stats_period=invalid")
        expect(page.locator("[data-analytics-period]")).to_have_value("past_day")
        page.locator("[data-analytics-module-select]").select_option("hsguru_archetypes")
        self.assertNotIn("stats_rank", parse_qs(urlsplit(page.url).query))
        self.assertNotIn("stats_period", parse_qs(urlsplit(page.url).query))

    def test_tokens_form_contract_and_responsive_disclosure(self):
        page = self.page
        page.goto(self.origin + '/tests/api_token_panel_fixture.php')
        expect(page.locator('h1')).to_have_text('Доступ к API')
        expect(page.locator('.token-issue-form')).to_be_hidden()
        expect(page.locator('[data-token-secret]')).to_have_count(0)
        expect(page.locator('.token-table th:visible')).to_have_count(7)
        page.locator('.token-create summary').first.click()
        expect(page.locator('.token-issue-form')).to_be_visible()
        expect(page.locator('.token-issue-form')).to_have_attribute('method', 'post')
        expect(page.locator('.token-issue-form [name="csrf"]')).to_have_value('fixture-csrf')
        expect(page.locator('.token-issue-form [name="form_nonce"]')).to_have_value('fixture-nonce')
        expect(page.locator('.token-issue-form input[type=checkbox]:checked')).to_have_count(1)
        expect(page.locator('.token-issue-form input[type=checkbox]:checked')).to_have_value('database:read')
        page.evaluate("document.querySelector('.token-issue-form').addEventListener('submit', e => { e.preventDefault(); window.tokenSubmission = Array.from(new FormData(e.target)); })")
        page.locator('.token-issue-form [name=name]').fill('My integration')
        page.locator('.token-issue-form button[type=submit]').click()
        self.assertIn(['action', 'issue_api_token'], page.evaluate('tokenSubmission'))
        self.assertIn(['name', 'My integration'], page.evaluate('tokenSubmission'))
        for width in (1440, 1024, 768, 390, 320):
            page.set_viewport_size({'width': width, 'height': 1100})
            self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))
            directory = os.environ.get('PANEL_SCREENSHOT_DIR')
            if directory and width in (1440, 390):
                page.evaluate('document.activeElement.blur(); window.scrollTo(0, 0)')
                page.screenshot(path=str(Path(directory) / f'panel-tokens-{width}.png'), full_page=True)

    def test_tokens_secret_copy_and_revoke_confirmation(self):
        page = self.page
        page.goto(self.origin + '/tests/api_token_panel_fixture.php?issued')
        page.evaluate("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async value => { window.copiedFixtureToken = value; }}, configurable: true})")
        page.locator('[data-copy-token]').click()
        expect(page.locator('[data-copy-token-status]')).to_contain_text('Токен скопирован')
        self.assertEqual(page.evaluate('copiedFixtureToken'), page.locator('[data-token-secret]').inner_text())
        storage = page.evaluate('JSON.stringify(localStorage) + JSON.stringify(sessionStorage)')
        self.assertNotIn('DemoToken001', storage)
        page.evaluate("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async () => { throw Error('denied'); }}, configurable: true})")
        page.locator('[data-copy-token]').click()
        expect(page.locator('[data-copy-token-status]')).to_contain_text('Выделите токен вручную')
        expect(page.locator('.token-table button').first).to_be_disabled()
        page.once('dialog', lambda dialog: dialog.dismiss())
        page.get_by_role('button', name='Отозвать токен WordPress production').click()
        self.assertIn('/tests/api_token_panel_fixture.php', page.url)
        revoke = page.locator('form').filter(has=page.locator('[name=token_id]'))
        expect(revoke.locator('[name=token_id]')).to_have_value('DemoRead0001')
        expect(revoke.locator('[name=csrf]')).to_have_value('fixture-csrf')

    def test_tokens_missing_empty_error_and_revoked_states(self):
        for query, selector, text in (
            ('missing', '.token-empty-state', 'Панель ещё не подключена'),
            ('empty', '.token-empty-state', 'Пока нет выпущенных токенов'),
            ('error', '.token-inline-error', 'Не удалось загрузить реестр'),
            ('revoked', '.token-table', 'Отозван'),
        ):
            self.page.goto(self.origin + '/tests/api_token_panel_fixture.php?' + query)
            expect(self.page.locator(selector)).to_contain_text(text)
            if query == 'missing':
                expect(self.page.locator('.token-issue-form')).to_have_count(0)

    def test_card_editing_fields_upload_contract_and_rejected_values(self):
        page = self.page
        page.goto(self.origin + '/tests/form_panel_fixture.php?mode=edit')
        expect(page.locator('h1')).to_have_text('Редактировать карту')
        form = page.locator('.card-form')
        expect(form).to_have_attribute('enctype', 'multipart/form-data')
        expect(form.locator('[name=csrf]')).to_have_value('fixture-csrf')
        expect(form.locator('[name=action]')).to_have_value('save')
        expect(form.locator('input[type=file]')).to_have_count(4)
        expected_names = {'csrf','action','id','name','name_en','card_id','dbf','card_type','tavern_tier','creature_type','attack','health','in_pool','duos_only','card_image_file','golden_image_file','art_image_file','framed_image_file','notes'}
        self.assertEqual(set(form.locator('[name]').evaluate_all('els => els.map(e => e.name)')), expected_names)
        form.locator('[name=name]').fill('Изменённое имя <b>')
        form.locator('[name=attack]').fill('7')
        form.locator('[name=notes]').fill('Не терять заметки после ошибки')
        form.locator('[name=in_pool]').uncheck()
        form.locator('[name=duos_only]').check()
        form.locator('button[type=submit]').click()
        expect(page.locator('[role=alert]')).to_contain_text('Тестовый отказ')
        expect(form.locator('[name=name]')).to_have_value('Изменённое имя <b>')
        expect(form.locator('[name=attack]')).to_have_value('7')
        expect(form.locator('[name=notes]')).to_have_value('Не терять заметки после ошибки')
        expect(form.locator('[name=in_pool]')).not_to_be_checked()
        expect(form.locator('[name=duos_only]')).to_be_checked()
        expect(form.locator('[name=id]')).to_have_value('42')
        expect(page.locator('h1')).to_have_text('Редактировать карту')
        # Recovery must use the submitted ID, independent of the original GET mode.
        page.goto(self.origin + '/tests/form_panel_fixture.php?mode=new')
        form.locator('[name=name]').fill('Новая карта после ошибки')
        form.locator('button[type=submit]').click()
        expect(page.locator('[role=alert]')).to_contain_text('Тестовый отказ')
        expect(page.locator('h1')).to_have_text('Добавить карту')
        expect(form.locator('[name=id]')).to_have_value('')
        expect(form.locator('[name=name]')).to_have_value('Новая карта после ошибки')

    def test_wiki_filters_do_not_change_when_editing_rows(self):
        page = self.page
        page.goto(self.origin + '/tests/form_panel_fixture.php?mode=wiki')
        expect(page.locator('[data-term-row]:visible')).to_have_count(5)
        missing = page.locator('button[data-term-status=missing]')
        missing.click()
        expect(page.locator('[data-term-row]:visible')).to_have_count(2)
        row = page.locator('[data-term-row]:visible').first
        row.locator('input:not([type=hidden])').fill('Провокация')
        expect(missing).to_have_attribute('aria-pressed', 'true')
        page.locator('button[data-term-status=translated]').click()
        expect(page.locator('[data-term-row]:visible')).to_have_count(4)
        page.locator('[data-term-filter]').fill('Провокация')
        expect(page.locator('[data-term-row]:visible')).to_have_count(1)
        # Hidden translations remain in FormData, so filtering cannot erase them.
        values = page.locator('.terms-form').evaluate('f => Array.from(new FormData(f))')
        self.assertEqual(len([key for key, value in values if key.endswith('[en]')]), 5)
        self.assertIn(['terms[mechanics][1][ru]', 'Провокация'], values)
        page.locator('[data-term-filter]').fill('no such term')
        expect(page.locator('[data-term-empty]')).to_be_visible()
        page.locator('[data-term-reset]').click()
        expect(page.locator('[data-term-row]:visible')).to_have_count(5)
        expect(page.locator('[data-term-filter]')).to_be_focused()
        page.get_by_role('button', name='Сохранить переводы').click()
        expect(page.locator('[role=alert]')).to_contain_text('Тестовый отказ')
        expect(page.locator('h1')).to_have_text('Переводы Wiki')
        expect(page.locator('[name="terms[mechanics][1][ru]"]')).to_have_value('Провокация')

    def test_forms_responsive_and_empty_wiki(self):
        page = self.page
        for mode in ('new', 'edit', 'wiki'):
            page.goto(self.origin + '/tests/form_panel_fixture.php?mode=' + mode)
            expect(page.locator('h1')).to_have_count(1)
            for width in (1440, 1024, 768, 390, 320):
                page.set_viewport_size({'width': width, 'height': 1100})
                self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'), (mode, width))
                directory = os.environ.get('PANEL_SCREENSHOT_DIR')
                if directory and width in (1440, 390):
                    page.evaluate('window.scrollTo(0, 0)')
                    page.screenshot(path=str(Path(directory) / f'panel-{mode}-{width}.png'), full_page=True)
        page.goto(self.origin + '/tests/form_panel_fixture.php?mode=wiki&empty')
        expect(page.locator('[data-term-count]')).to_contain_text('0 из 0')
        expect(page.locator('[data-term-empty]')).to_be_visible()

    def test_auth_states_preserve_headers_and_escape_messages(self):
        page = self.page
        for mode, code in (('login', 200), ('setup', 200), ('denied', 403), ('expired', 403), ('unavailable', 500), ('logout', 403), ('escape', 502)):
            response = page.goto(self.origin + '/tests/auth_panel_fixture.php?mode=' + mode)
            self.assertEqual(response.status, code)
            self.assertIn('no-store', response.headers['cache-control'])
            self.assertEqual(response.headers['x-frame-options'], 'DENY')
            self.assertIn("form-action 'self' https://github.com", response.headers['content-security-policy'])
            expect(page.locator('meta[name=robots]')).to_have_attribute('content', 'noindex,nofollow')
            expect(page.locator('h1')).to_have_count(1)
            if mode == 'escape':
                expect(page.locator('main img, main script')).to_have_count(0)
                expect(page.locator('h1')).to_have_text('<img src=x onerror=alert(1)>')
            if mode == 'setup':
                expect(page.locator('main form')).to_have_attribute('method', 'post')
                expect(page.locator('main [name=manifest]')).to_have_value('fixture-only')
            if mode == 'login':
                expect(page.locator('.button')).to_have_attribute('href', '/auth/github')
                page.keyboard.press('Tab')
                expect(page.locator('.button')).to_be_focused()

    def test_auth_responsive_and_saved_theme(self):
        page = self.page
        page.goto(self.origin + '/tests/auth_panel_fixture.php')
        for width in (1440, 1024, 768, 390, 320):
            page.set_viewport_size({'width': width, 'height': 1000})
            self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'))
            directory = os.environ.get('PANEL_SCREENSHOT_DIR')
            if directory and width in (1440, 390):
                page.screenshot(path=str(Path(directory) / f'panel-login-{width}.png'), full_page=True)
        page.evaluate("localStorage.setItem('bgCardsTheme', 'dark')")
        page.reload()
        expect(page.locator('html')).to_have_attribute('data-theme', 'dark')

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
