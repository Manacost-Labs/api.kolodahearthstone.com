from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .fetcher import refresh_sources
from .source_state import SourceState
from .sources import SOURCE_BY_ID

DEFAULT_ENV_FILE = Path("/etc/hs-data-api.env")


def load_env_file(path: Path = DEFAULT_ENV_FILE) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        # Always trust /etc/hs-data-api.env for app settings (avoid stale shell exports).
        if key.startswith(
            (
                "HS_API_",
                "HS_FETCH_",
                "HS_HSGURU_",
                "HS_FLARESOLVERR_",
                "HS_IPROYAL_",
                "HS_PROXY_",
                "HSREPLAY_",
                "VICIOUS_SYNDICATE_",
                "TELEGRAM_",
            )
        ):
            os.environ[key] = value
        elif key not in os.environ:
            os.environ[key] = value


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Hearthstone data sources.")
    sub = parser.add_subparsers(dest="command", required=True)
    refresh = sub.add_parser("refresh")
    refresh.add_argument("--all", action="store_true", help="Refresh every configured source.")
    refresh.add_argument("--source", action="append", default=[], help="Refresh one source id.")
    refresh.add_argument(
        "--require-all-ok",
        action="store_true",
        help="Exit non-zero unless every selected source publishes a fresh dataset.",
    )
    refresh.add_argument(
        "--tier",
        choices=[
            "light_api",
            "medium_api",
            "browser_patchright",
            "browser_protected",
        ],
        help="Refresh only sources in this tier (for split cron).",
    )
    refresh.add_argument(
        "--lab-backends",
        action="store_true",
        help="Use HS_FETCH_BACKENDS_LAB (includes cloakbrowser) for this run only.",
    )
    refresh.add_argument(
        "--scheduled",
        action="store_true",
        help="Honor per-section enable/disable controls for a scheduled run.",
    )
    scheduled_check = sub.add_parser(
        "scheduled-check",
        help="Exit 0 when a source's scheduled section is enabled, otherwise exit 1.",
    )
    scheduled_check.add_argument("--source", required=True, help="Configured source id.")
    sub.add_parser("proxy-check", help="Verify HS_FETCH_PROXY_URL egress IP.")
    sub.add_parser(
        "proxy-rotation-check",
        help="Sample egress IPs (rotation test; set HS_IPROYAL_ROTATE_PER_FETCH=true for max spread).",
    )
    pf = sub.add_parser("preflight", help="Run refresh preflight checks (proxy, FlareSolverr, HSReplay probe).")
    pf.add_argument("--strict", action="store_true", help="Exit 1 if any required check fails.")
    canary = sub.add_parser("canary", help="Run parser canary checks for proxy, auth and key API endpoints.")
    canary.add_argument("--strict", action="store_true", help="Exit 1 if any canary check fails.")
    freshness = sub.add_parser(
        "freshness-check",
        help="Audit stale/cached-after-failure datasets and optionally send stale alerts.",
    )
    freshness.add_argument("--since-hours", type=float, default=24.0)
    freshness.add_argument("--alert", action="store_true", help="Send configured stale Telegram alerts.")
    game_audit = sub.add_parser(
        "game-change-audit",
        help="Compare patch, HearthstoneJSON, wiki changes and critical site feeds.",
    )
    game_audit.add_argument("--alert", action="store_true", help="Send a Telegram alert when attention is required.")
    quality = sub.add_parser(
        "quality-check",
        help="Audit cached datasets with parser validation, source contracts and quality scores.",
    )
    quality.add_argument("--min-quality-score", type=float, default=0.85)
    quality.add_argument("--warn-quality-score", type=float, default=0.95)
    hsguru_recon = sub.add_parser("hsguru-recon", help="Inspect a HSGuru page for embedded JSON/API candidates.")
    hsguru_recon.add_argument("--url", default="https://www.hsguru.com/meta?format=2&min_games=100&rank=legend")
    sub.add_parser(
        "firecrawl-map-hsreplay",
        help="Run Firecrawl /v2/map for hsreplay.net and rebuild the derived HSReplay index.",
    )
    sub.add_parser(
        "rebuild-hsreplay-index",
        help="Rebuild the derived HSReplay index from current cached datasets without Firecrawl credits.",
    )
    archetypes = sub.add_parser(
        "refresh-hsreplay-archetypes",
        help="Refresh the local SQLite database with HSReplay Standard archetype snapshots.",
    )
    archetypes.add_argument("--rank-range", default="LEGEND")
    archetypes.add_argument("--game-type", default="RANKED_STANDARD")
    archetypes.add_argument("--region", default="REGION_EU")
    archetypes.add_argument("--summary-time-range", default="LAST_7_DAYS")
    archetypes.add_argument("--deck-time-range", default="LAST_30_DAYS")
    archetypes.add_argument("--mulligan-time-range", default="LAST_30_DAYS")
    archetypes.add_argument("--limit", type=int, default=None, help="Debug: refresh only first N archetypes from the index.")
    archetypes.add_argument(
        "--scheduled",
        action="store_true",
        help="Skip this scheduled pipeline when its admin section is disabled.",
    )
    bg_minions = sub.add_parser(
        "refresh-bg-minions-db",
        help="Refresh the local SQLite database with HSReplay Battlegrounds minion snapshots.",
    )
    bg_minions.add_argument(
        "--scheduled",
        action="store_true",
        help="Skip this scheduled pipeline when its admin section is disabled.",
    )
    bg_hero_details = sub.add_parser(
        "refresh-bg-hero-details",
        help="Refresh HSReplay Battlegrounds hero detail statistics and duos hero tier list.",
    )
    bg_hero_details.add_argument("--limit", type=int, default=None, help="Debug: refresh only first N solo heroes.")
    bg_hero_details.add_argument("--concurrency", type=int, default=3)
    bg_hero_details.add_argument("--mmr", default="TOP_50_PERCENT")
    bg_hero_details.add_argument("--time-range", default="CURRENT_BATTLEGROUNDS_PATCH")
    bg_hero_details.add_argument(
        "--scheduled",
        action="store_true",
        help="Skip this scheduled pipeline when its admin section is disabled.",
    )
    hsguru_matrix = sub.add_parser(
        "refresh-hsguru-meta-matrix",
        help="Refresh the unified HSGuru Standard/Wild meta matrix through Firecrawl.",
    )
    hsguru_matrix.add_argument("--concurrency", type=int, default=2)
    hsguru_matrix.add_argument(
        "--scheduled",
        action="store_true",
        help="Skip this scheduled pipeline when its admin section is disabled.",
    )
    fun_decks = sub.add_parser(
        "refresh-fun-decks",
        help="Rebuild the derived fun/off-meta decks dataset from streamer candidates.",
    )
    fun_decks.add_argument(
        "--scheduled",
        action="store_true",
        help="Skip this scheduled pipeline when its admin section is disabled.",
    )
    fun_decks.add_argument(
        "--format",
        choices=("standard", "wild", "all"),
        default="all",
        help="Restrict candidate hunt to one format (standard refresh timer uses this).",
    )
    hsguru_analysis = sub.add_parser(
        "refresh-hsguru-archetype-analysis",
        help="Refresh Legend class matchups and card stats for active HSGuru archetypes.",
    )
    hsguru_analysis.add_argument("--concurrency", type=int, default=3)
    hsguru_analysis.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Debug: refresh only the first N active archetypes.",
    )
    sub.add_parser(
        "capture-bg-compositions-screenshot",
        help="Capture a Firecrawl screenshot of HSReplay Battlegrounds compositions.",
    )
    login = sub.add_parser("hsreplay-login", help="Log into HSReplay Premium and save browser session.")
    imp = sub.add_parser(
        "hsreplay-import-storage",
        help="Import Playwright storage_state JSON (export from logged-in browser).",
    )
    imp.add_argument("path", type=Path, help="Path to storage_state JSON file.")
    vicious_imp = sub.add_parser(
        "vicious-import-storage",
        help="Import Vicious Syndicate Playwright storage_state or Cookie-Editor JSON.",
    )
    vicious_imp.add_argument("path", type=Path, help="Path to exported cookie JSON file.")
    enrich = sub.add_parser("enrich-links", help="Rebuild structured data from cached links (no refetch).")
    enrich.add_argument("--source", action="append", default=[], help="Source id to enrich.")
    enrich.add_argument("--all-hsreplay", action="store_true", help="All HSReplay sources.")
    sub.add_parser(
        "telegram-setup",
        help="Fetch latest Telegram bot updates, automatically detect chat ID, and configure notifications.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    args = parse_args(argv or sys.argv[1:])
    if args.command == "scheduled-check":
        if args.source not in SOURCE_BY_ID:
            print(json.dumps({
                "ok": False,
                "enabled": False,
                "source_id": args.source,
                "error": "unknown_source",
            }, ensure_ascii=False, indent=2))
            return 2
        from .parser_control import is_source_scheduled_enabled
        from .parser_control_registry import SOURCE_TO_SECTION

        enabled = is_source_scheduled_enabled(args.source)
        print(json.dumps({
            "ok": True,
            "enabled": enabled,
            "source_id": args.source,
            "section_id": SOURCE_TO_SECTION[args.source],
        }, ensure_ascii=False, indent=2))
        return 0 if enabled else 1
    if args.command == "proxy-check":
        from .scrapers.proxy import check_proxy_health

        info = asyncio.run(check_proxy_health())
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0
    if args.command == "proxy-rotation-check":
        from .scrapers.proxy import check_proxy_rotation

        info = asyncio.run(check_proxy_rotation(8))
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0 if info.get("rotating") or info.get("unique_ips", 0) >= 1 else 1
    if args.command == "preflight":
        from .preflight import run_refresh_preflight

        async def _pf() -> dict:
            return (
                await run_refresh_preflight(needs_proxy=True, needs_flaresolverr=True)
            ).to_dict()

        result = asyncio.run(_pf())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.strict and not result.get("ok"):
            return 1
        return 0
    if args.command == "canary":
        from .canary import run_canary

        result = asyncio.run(run_canary(strict=bool(args.strict)))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.strict and not result.get("ok"):
            return 1
        return 0
    if args.command == "freshness-check":
        from .fetcher import _maybe_cached_after_failure_alert
        from .refresh_log import build_summary
        from .stale_monitor import alert_stale_sources
        from .storage import load_status

        async def _send_freshness_alerts(cached_source_ids: list[str]) -> dict[str, int]:
            stale_alerts = await alert_stale_sources()
            cached_attempts = 0
            for source_id in cached_source_ids:
                source = SOURCE_BY_ID.get(source_id)
                if source is None:
                    continue
                status = load_status(source_id) or {}
                await _maybe_cached_after_failure_alert(source, status)
                cached_attempts += 1
            return {
                "stale_alerts_sent": stale_alerts,
                "cached_after_failure_alerts_attempted": cached_attempts,
            }

        summary = build_summary(since_hours=args.since_hours)
        cached_after_failure_sources = summary.get("cached_after_failure_sources", [])
        payload = {
            "ok": bool(summary.get("freshness", {}).get("ok")),
            "freshness": summary.get("freshness"),
            "stale_datasets": summary.get("stale_datasets", []),
            "cached_after_failure_sources": cached_after_failure_sources,
            "stale_hours_threshold": summary.get("stale_hours_threshold"),
        }
        if args.alert:
            payload["alerts"] = asyncio.run(_send_freshness_alerts(cached_after_failure_sources))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1
    if args.command == "game-change-audit":
        from .game_change_audit import run_game_change_audit

        result = run_game_change_audit()
        if args.alert and result.get("requires_attention"):
            from .fetcher import send_telegram_alert

            patch = result.get("patch") or {}
            detail = (
                f"patch={patch.get('current')} changed={patch.get('changed')}; "
                f"card changes={result.get('card_changes')}; "
                f"source issues={result.get('source_issue_count')}"
            )
            asyncio.run(
                send_telegram_alert(
                    "_game_change_audit",
                    "attention",
                    detail[:1000],
                    "https://arena.hs-manacost.ru/admin",
                )
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "quality-check":
        from collections import Counter

        from .scrapers.quality import quality_metrics, validate_parsed_data
        from .source_contracts import contract_quality_report, get_contract
        from .storage import load_dataset, load_status

        sources = []
        bad = []
        warn = []
        for source in SOURCE_BY_ID.values():
            status = load_status(source.id) or {}
            dataset = load_dataset(source.id) or {}
            data = dataset.get("data") or {}
            structured = data.get("structured") or data.get("hsreplay_extracted") or {}
            error_type = None
            try:
                metrics = quality_metrics(source, data) if data else {}
                contract = get_contract(source.id)
                contract_report = (
                    contract_quality_report(source.id, structured)
                    if contract is not None and structured
                    else None
                )
                if source.kind == "pipeline":
                    state = status.get("state", SourceState.NEVER_FETCHED)
                    validate_ok = state == SourceState.OK and bool(structured)
                    reason = (
                        "ok"
                        if validate_ok
                        else f"pipeline status/structured data invalid (state={state})"
                    )
                else:
                    validate_ok, reason = (
                        validate_parsed_data(source, data)
                        if data
                        else (False, "missing dataset")
                    )
            except Exception as exc:
                validate_ok = False
                reason = f"quality-check raised {type(exc).__name__}: {exc}"
                metrics = {}
                contract_report = None
                error_type = type(exc).__name__
            quality_score = metrics.get("quality_score")
            row = {
                "source_id": source.id,
                "site": source.site,
                "category": source.category,
                "state": status.get("state", SourceState.NEVER_FETCHED),
                "backend": status.get("backend"),
                "serving_cached_dataset": bool(status.get("serving_cached_dataset")),
                "structured_type": structured.get("type"),
                "rows_total": metrics.get("rows_total"),
                "quality_score": quality_score,
                "validate_ok": validate_ok,
                "validate_reason": reason,
                "error_type": error_type,
                "contract_ok": None if contract_report is None else contract_report.get("ok"),
                "contract_warnings": None if contract_report is None else contract_report.get("warnings"),
            }
            sources.append(row)
            low_score = isinstance(quality_score, (int, float)) and quality_score < args.min_quality_score
            warn_score = (
                isinstance(quality_score, (int, float))
                and args.min_quality_score <= quality_score < args.warn_quality_score
            )
            if (
                not validate_ok
                or row["contract_ok"] is False
                or row["serving_cached_dataset"]
                or low_score
            ):
                bad.append(row)
            elif warn_score:
                warn.append(row)
        payload = {
            "ok": not bad,
            "sources": len(sources),
            "by_site": dict(Counter(row["site"] for row in sources)),
            "min_quality_score": args.min_quality_score,
            "warn_quality_score": args.warn_quality_score,
            "bad_count": len(bad),
            "bad_sources": bad,
            "warn_count": len(warn),
            "warn_sources": warn,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload["ok"] else 1
    if args.command == "hsguru-recon":
        import httpx

        from .hsguru_api import discover_hsguru_api_candidates
        from .scrapers.http_resilience import build_fetch_headers
        from .scrapers.proxy import httpx_client_kwargs

        async def _recon() -> dict:
            async with httpx.AsyncClient(
                headers=build_fetch_headers(args.url),
                **httpx_client_kwargs("hsguru_recon", page_url=args.url, timeout=45.0),
            ) as client:
                response = await client.get(args.url)
                payload = discover_hsguru_api_candidates(response.text, page_url=args.url)
                payload["http_status"] = response.status_code
                payload["bytes"] = len(response.content)
                return payload

        result = asyncio.run(_recon())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "firecrawl-map-hsreplay":
        from .firecrawl_map import refresh_hsreplay_map_and_index

        result = refresh_hsreplay_map_and_index()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "rebuild-hsreplay-index":
        from .firecrawl_map import build_hsreplay_index
        from .fetcher import RefreshLock

        with RefreshLock():
            result = build_hsreplay_index()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "refresh-hsreplay-archetypes":
        if args.scheduled:
            from .parser_control import is_source_scheduled_enabled

            if not is_source_scheduled_enabled("hsreplay_archetypes"):
                print(json.dumps({
                    "ok": True,
                    "skipped": True,
                    "reason": "section_disabled",
                    "source_id": "hsreplay_archetypes",
                }, ensure_ascii=False, indent=2))
                return 0
        from .hsreplay_archetypes_db import (
            export_latest_archetypes_json,
            refresh_hsreplay_archetype_database,
        )

        result = asyncio.run(
            refresh_hsreplay_archetype_database(
                rank_range=args.rank_range,
                game_type=args.game_type,
                region=args.region,
                summary_time_range=args.summary_time_range,
                deck_time_range=args.deck_time_range,
                mulligan_time_range=args.mulligan_time_range,
                limit=args.limit,
            )
        )
        result["export_path"] = str(export_latest_archetypes_json())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "refresh-bg-minions-db":
        if args.scheduled:
            from .parser_control import is_source_scheduled_enabled

            if not is_source_scheduled_enabled("hsreplay_battlegrounds_minions"):
                print(json.dumps({
                    "ok": True,
                    "skipped": True,
                    "reason": "section_disabled",
                    "source_id": "hsreplay_battlegrounds_minions",
                }, ensure_ascii=False, indent=2))
                return 0
        from .hsreplay_bg_minions_db import refresh_bg_minion_database_sync

        result = refresh_bg_minion_database_sync()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "refresh-bg-hero-details":
        if args.scheduled:
            from .parser_control import is_source_scheduled_enabled

            if not is_source_scheduled_enabled("hsreplay_battlegrounds_hero_details"):
                print(json.dumps({
                    "ok": True,
                    "skipped": True,
                    "reason": "section_disabled",
                    "source_id": "hsreplay_battlegrounds_hero_details",
                }, ensure_ascii=False, indent=2))
                return 0
        from .hsreplay_bg_hero_details import refresh_bg_hero_details

        result = asyncio.run(
            refresh_bg_hero_details(
                limit=args.limit,
                concurrency=args.concurrency,
                mmr=args.mmr,
                time_range=args.time_range,
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "refresh-hsguru-meta-matrix":
        if args.scheduled:
            from .parser_control import is_source_scheduled_enabled

            if not is_source_scheduled_enabled("hsguru_meta_matrix"):
                print(json.dumps({
                    "ok": True,
                    "skipped": True,
                    "reason": "section_disabled",
                    "source_id": "hsguru_meta_matrix",
                }, ensure_ascii=False, indent=2))
                return 0
        from .hsguru_meta_matrix import refresh_hsguru_meta_matrix

        result = asyncio.run(
            refresh_hsguru_meta_matrix(concurrency=max(1, min(args.concurrency, 5)))
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "refresh-fun-decks":
        if args.scheduled:
            from .parser_control import is_source_scheduled_enabled

            if not is_source_scheduled_enabled("hsguru_fun_decks"):
                print(json.dumps({
                    "ok": True,
                    "skipped": True,
                    "reason": "section_disabled",
                    "source_id": "hsguru_fun_decks",
                }, ensure_ascii=False, indent=2))
                return 0
        from .fun_decks import refresh_fun_decks

        focus = None if getattr(args, "format", "all") in (None, "all") else str(args.format)
        result = refresh_fun_decks(scheduled=bool(args.scheduled), format_focus=focus)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "refresh-hsguru-archetype-analysis":
        from .hsguru_archetype_analysis import refresh_hsguru_archetype_analysis

        result = asyncio.run(
            refresh_hsguru_archetype_analysis(
                concurrency=max(1, min(args.concurrency, 10)),
                limit=args.limit,
            )
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "capture-bg-compositions-screenshot":
        from .hsreplay_bg_screenshots import capture_compositions_screenshot

        result = asyncio.run(capture_compositions_screenshot())
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 1
    if args.command == "hsreplay-login":
        from .config import hsreplay_storage_path
        from .hsreplay_auth import ensure_hsreplay_login
        from .scrapers.browser_pool import PatchrightPool
        from .scrapers.proxy import playwright_proxy

        async def _login() -> bool:
            pool = await PatchrightPool.get()
            ctx_kw: dict = {"viewport": {"width": 1440, "height": 900}}
            px = playwright_proxy("hsreplay_login")
            if px:
                ctx_kw["proxy"] = px
            context = await pool._browser.new_context(**ctx_kw)
            page = await context.new_page()
            try:
                return await ensure_hsreplay_login(page, context)
            finally:
                await context.close()

        ok = asyncio.run(_login())
        print(json.dumps({"ok": ok, "storage": str(hsreplay_storage_path())}, indent=2))
        return 0 if ok else 1
    if args.command == "hsreplay-import-storage":
        from .hsreplay_auth import import_storage_state

        dest = import_storage_state(args.path)
        print(json.dumps({"ok": True, "storage": str(dest)}, indent=2))
        return 0
    if args.command == "vicious-import-storage":
        from .vicious_syndicate_auth import import_vicious_syndicate_storage

        dest = import_vicious_syndicate_storage(args.path)
        print(json.dumps({"ok": True, "storage": str(dest)}, indent=2))
        return 0
    if args.command == "telegram-setup":
        from .config import telegram_bot_token
        import httpx

        token = telegram_bot_token()
        if not token:
            print("ERROR: TELEGRAM_BOT_TOKEN is not configured in /etc/hs-data-api.env", file=sys.stderr)
            return 1

        redacted = f"{token[:6]}...{token[-4:]}" if len(token) > 12 else "***"
        print(f"Connecting to Telegram Bot with token: {redacted}")
        print("Please send a message (e.g. /start) to your bot in Telegram now.")
        print("Waiting for updates...")

        async def _setup() -> int:
            url = f"https://api.telegram.org/bot{token}/getUpdates"
            async with httpx.AsyncClient(timeout=10.0) as client:
                for attempt in range(1, 11):
                    try:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        data = resp.json()
                        updates = data.get("result", [])
                        if updates:
                            last_update = updates[-1]
                            message = last_update.get("message") or last_update.get("edited_message")
                            if message and "chat" in message:
                                chat = message["chat"]
                                chat_id = str(chat["id"])
                                first_name = chat.get("first_name", "")
                                username = chat.get("username", "")
                                print(f"\nDetected Telegram Chat!")
                                print(f"  Chat ID: {chat_id}")
                                print(f"  Name: {first_name} (@{username})")
                                
                                env_path = Path("/etc/hs-data-api.env")
                                if env_path.exists():
                                    lines = env_path.read_text(encoding="utf-8").splitlines()
                                    new_lines = []
                                    updated = False
                                    for line in lines:
                                        if line.strip().startswith("TELEGRAM_CHAT_ID="):
                                            new_lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
                                            updated = True
                                        else:
                                            new_lines.append(line)
                                    if not updated:
                                        new_lines.append(f"TELEGRAM_CHAT_ID={chat_id}")
                                    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
                                    print(f"Updated /etc/hs-data-api.env with TELEGRAM_CHAT_ID={chat_id}")
                                    os.environ["TELEGRAM_CHAT_ID"] = chat_id
                                else:
                                    print("WARNING: /etc/hs-data-api.env not found, could not save chat ID automatically.")

                                test_url = f"https://api.telegram.org/bot{token}/sendMessage"
                                test_msg = (
                                    "✅ <b>Hearthstone Parser Alert</b>\n\n"
                                    "Уведомления успешно настроены и подключены к этому чату! "
                                    "Вы будете получать сообщения о критических ошибках сбора данных."
                                )
                                try:
                                    await client.post(test_url, json={
                                        "chat_id": chat_id,
                                        "text": test_msg,
                                        "parse_mode": "HTML"
                                    })
                                    print("Sent test notification message to Telegram!")
                                except Exception as e:
                                    print(f"Failed to send test message: {e}", file=sys.stderr)
                                return 0
                    except Exception as e:
                        print(f"Error checking updates: {e}", file=sys.stderr)
                    print(f"Attempt {attempt}/10: No new messages found yet. Checking again in 3s...")
                    await asyncio.sleep(3)
                print("\nTimeout: No messages found in Telegram. Did you send a message to the bot?", file=sys.stderr)
                return 1

        return asyncio.run(_setup())
    if args.command == "enrich-links":
        from .hsreplay_extract import (
            extract_arena_cards_from_links,
            extract_arena_winning_decks_from_links,
            extract_bg_comps_from_links,
            extract_ranked_cards_from_links,
        )
        from .sources import SOURCES
        from .storage import load_dataset, save_dataset
        from .structured import build_structured

        ids = args.source or []
        if args.all_hsreplay:
            ids = [s.id for s in SOURCES if s.site == "hsreplay"]
        if not ids:
            print("Use --source ID or --all-hsreplay", file=sys.stderr)
            return 2
        for sid in ids:
            source = SOURCE_BY_ID[sid]
            ds = load_dataset(sid)
            if not ds:
                print(sid, "skip: no dataset")
                continue
            data = ds["data"]
            links = data.get("links") or []
            extracted: dict = {}
            if sid == "hsreplay_battlegrounds_comps":
                extracted = {"type": "bg_comps", "comps": extract_bg_comps_from_links(links), "blocked": False}
            elif sid.startswith("hsreplay_cards_"):
                extracted = {
                    "type": "card_stats",
                    "cards": extract_ranked_cards_from_links(links),
                    "blocked": False,
                }
            elif sid == "hsreplay_arena_winning_decks":
                extracted = {
                    "type": "arena_winning_decks",
                    "decks": extract_arena_winning_decks_from_links(
                        links, data.get("text_preview") or []
                    ),
                }
            elif sid == "hsreplay_arena_cards_advanced":
                extracted = {
                    "type": "arena_card_tiers",
                    "cards": extract_arena_cards_from_links(links),
                    "total_cards": None,
                }
            else:
                extracted = build_structured(source, data)
            data["hsreplay_extracted"] = extracted
            data["structured"] = extracted
            ds["data"] = data
            save_dataset(sid, ds)
            summary = {
                k: len(v) if isinstance(v, list) else v
                for k, v in extracted.items()
                if k != "type"
            }
            print(sid, json.dumps(summary, ensure_ascii=False))
        return 0
    if args.command == "refresh":
        if not args.all and not args.source and not args.tier:
            print("Use --all, --tier TIER, or --source SOURCE_ID", file=sys.stderr)
            return 2
        if getattr(args, "lab_backends", False):
            from .config import fetch_backends_lab

            os.environ["HS_FETCH_BACKENDS"] = ",".join(fetch_backends_lab())
        missing = [source_id for source_id in args.source if source_id not in SOURCE_BY_ID]
        if missing:
            print(f"Unknown source ids: {', '.join(missing)}", file=sys.stderr)
            return 2
        pipeline = [
            source_id
            for source_id in args.source
            if SOURCE_BY_ID[source_id].kind == "pipeline"
        ]
        if pipeline:
            print(
                f"Pipeline sources (own systemd timers, not scraped by refresh): {', '.join(pipeline)}. "
                "Use their dedicated commands (e.g. refresh-bg-hero-details, refresh-hsreplay-archetypes).",
                file=sys.stderr,
            )
            return 2
        source_ids = None if args.all else (args.source or None)
        if args.scheduled:
            results = asyncio.run(
                refresh_sources(
                    source_ids,
                    tier=args.tier,
                    respect_section_controls=True,
                )
            )
        else:
            results = asyncio.run(refresh_sources(source_ids, tier=args.tier))
        print(json.dumps(results, ensure_ascii=False, indent=2))
        if args.require_all_ok:
            expected_ids = set(args.source)
            if args.all:
                expected_ids = {
                    source_id
                    for source_id, source in SOURCE_BY_ID.items()
                    if source.kind == "scrape"
                }
            if args.scheduled and expected_ids:
                from .parser_control import filter_scheduled_source_ids

                expected_ids = set(filter_scheduled_source_ids(list(expected_ids)))
            fresh_ids = {
                str(result.get("source_id") or "")
                for result in results
                if result.get("state") == "ok"
                and not result.get("serving_cached_dataset")
            }
            if expected_ids and (not results or not expected_ids.issubset(fresh_ids)):
                return 1
            if any(
                result.get("state") != "ok" or result.get("serving_cached_dataset")
                for result in results
            ):
                return 1
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
