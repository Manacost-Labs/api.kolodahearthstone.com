from __future__ import annotations

import os
from pathlib import Path

from .trinket_slices import TRINKET_SLICE_SOURCE_IDS

DEFAULT_DATA_DIR = "/var/lib/hs-data-api"
DEFAULT_BACKENDS = "flaresolverr,patchright,scrapling,curl_cffi,cloudscraper"
DEFAULT_HSGURU_BACKENDS = "flaresolverr,patchright,scrapling,curl_cffi,cloudscraper"
DEFAULT_BACKENDS_LAB = (
    "cloakbrowser,flaresolverr,scrapling,patchright,curl_cffi,cloudscraper"
)
DEFAULT_HSREPLAY_JSON_CHANNELS = "flaresolverr,scrape_do,curl_cffi"
DEFAULT_HSREPLAY_MARKDOWN_CHANNELS = "flaresolverr,curl_cffi"


def runtime_display() -> str | None:
    value = os.environ.get("DISPLAY", "").strip()
    return value or None


def cloakbrowser_display() -> str:
    return os.environ.get("HS_CLOAKBROWSER_DISPLAY", ":99").strip() or ":99"


def firecrawl_map_hsreplay_url() -> str:
    return os.environ.get(
        "HS_FIRECRAWL_MAP_HSREPLAY_URL", "https://hsreplay.net"
    ).strip()


def firecrawl_map_hsreplay_limit(default: int = 5000) -> int:
    return max(1, int(os.environ.get("HS_FIRECRAWL_MAP_HSREPLAY_LIMIT", str(default))))


def build_id() -> str | None:
    value = os.environ.get("HS_BUILD_ID", "").strip()
    return value or None


def json_backup_keep_per_file() -> int:
    return max(0, int(os.environ.get("HS_JSON_BACKUP_KEEP_PER_FILE", "5")))


def pytest_current_test() -> str | None:
    value = os.environ.get("PYTEST_CURRENT_TEST", "").strip()
    return value or None


def python_environment() -> str:
    return os.environ.get("PYTHON_ENV", "").strip().lower()


def data_dir() -> Path:
    return Path(os.environ.get("HS_API_DATA_DIR", DEFAULT_DATA_DIR))


def bind_host() -> str:
    return os.environ.get("HS_API_BIND_HOST", "0.0.0.0")


def bind_port() -> int:
    return int(os.environ.get("HS_API_PORT", "8000"))


def api_key() -> str | None:
    value = os.environ.get("HS_API_KEY", "").strip()
    return value or None


def orchestrator_api_key() -> str | None:
    value = os.environ.get("HS_ORCHESTRATOR_API_KEY", "").strip()
    return value or None


def cors_allowed_origins() -> list[str]:
    raw = os.environ.get("HS_CORS_ALLOWED_ORIGINS", "https://api.kolodahearthstone.com")
    origins = [item.strip() for item in raw.split(",") if item.strip()]
    return origins or ["https://api.kolodahearthstone.com"]


def request_delay_seconds() -> float:
    return float(os.environ.get("HS_API_REQUEST_DELAY_SECONDS", "8.0"))


def request_timeout_seconds() -> float:
    return float(os.environ.get("HS_API_REQUEST_TIMEOUT_SECONDS", "150.0"))


def redis_url() -> str | None:
    value = os.environ.get("HS_REDIS_URL", "").strip()
    return value or None


def redis_key_prefix() -> str:
    value = os.environ.get("HS_REDIS_KEY_PREFIX", "koloda:v1").strip()
    return value or "koloda:v1"


def redis_operation_timeout_seconds() -> float:
    return max(
        0.05,
        min(2.0, float(os.environ.get("HS_REDIS_TIMEOUT_SECONDS", "0.25"))),
    )


def graphql_cache_ttl_seconds() -> int:
    return max(0, min(3_600, int(os.environ.get("HS_GRAPHQL_CACHE_TTL_SECONDS", "60"))))


def graphql_cache_local_entries() -> int:
    return max(0, min(10_000, int(os.environ.get("HS_GRAPHQL_CACHE_LOCAL_ENTRIES", "256"))))


def user_agent() -> str:
    return os.environ.get(
        "HS_API_USER_AGENT",
        "HSDataAPI/0.1 (+https://example.invalid/contact)",
    )


def fetch_proxy_url() -> str | None:
    value = os.environ.get("HS_FETCH_PROXY_URL", "").strip()
    return value or None


def fetch_require_proxy() -> bool:
    return os.environ.get("HS_FETCH_REQUIRE_PROXY", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def fetch_direct_enabled() -> bool:
    return os.environ.get("HS_FETCH_DIRECT_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def fetch_backends() -> list[str]:
    raw = os.environ.get("HS_FETCH_BACKENDS", DEFAULT_BACKENDS)
    return [part.strip() for part in raw.split(",") if part.strip()]


def hsguru_fetch_backends() -> list[str]:
    raw = os.environ.get("HS_HSGURU_FETCH_BACKENDS", DEFAULT_HSGURU_BACKENDS)
    return [part.strip() for part in raw.split(",") if part.strip()]


def fetch_backends_lab() -> list[str]:
    raw = os.environ.get("HS_FETCH_BACKENDS_LAB", DEFAULT_BACKENDS_LAB)
    return [part.strip() for part in raw.split(",") if part.strip()]


def flaresolverr_url() -> str:
    return os.environ.get("HS_FLARESOLVERR_URL", "http://127.0.0.1:8191/v1").strip()


def hsreplay_cookie_path() -> Path:
    return Path(
        os.environ.get("HSREPLAY_COOKIE_PATH", "/etc/hs-data-api-hsreplay-cookies.json")
    )


def fetch_max_retries() -> int:
    return max(1, int(os.environ.get("HS_FETCH_MAX_RETRIES", "3")))


def proxy_sticky_mode() -> str:
    """
    Sticky proxy session key strategy for residential providers (IPRoyal).
    domain — one IP per site (hsreplay.net, hsguru.com); recommended default.
    source — one IP per source_id (HS_IPROYAL_SESSION_PER_SOURCE=true equivalent).
    rotate — new session per fetch (only for debugging; causes day-2 bans).
    """
    raw = os.environ.get("HS_PROXY_STICKY_MODE", "domain").strip().lower()
    if raw in {"domain", "source", "rotate"}:
        return raw
    return "domain"


def iproyal_session_per_source() -> bool:
    """Legacy flag; when true, forces source-level sticky (overrides domain mode)."""
    return os.environ.get("HS_IPROYAL_SESSION_PER_SOURCE", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def http_retry_attempts() -> int:
    return max(1, int(os.environ.get("HS_HTTP_RETRY_ATTEMPTS", "3")))


def iproyal_session_lifetime() -> str:
    """IPRoyal sticky lifetime tag, e.g. 30m or 2h."""
    return os.environ.get("HS_IPROYAL_SESSION_LIFETIME", "30m").strip() or "30m"


def iproyal_api_token() -> str | None:
    value = os.environ.get("HS_IPROYAL_API_TOKEN", "").strip()
    return value or None


def iproyal_rotate_per_fetch() -> bool:
    """Append a unique IPRoyal session suffix per request (fresh IP). Off if your plan returns 407."""
    return os.environ.get("HS_IPROYAL_ROTATE_PER_FETCH", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def flaresolverr_hsguru_wait_ms() -> int:
    """Extra wait after page load for HSGuru React tables (FlareSolverr `wait` param).
    Higher values help with slow hydration on residential IPs; 30-60s typical for meta.
    """
    return max(0, int(os.environ.get("HS_FLARESOLVERR_HSGURU_WAIT_MS", "30000")))


def flaresolverr_hsguru_decks_wait_ms() -> int:
    """Deck pages are server-rendered; avoid the long meta-table hydration wait."""
    return max(
        0,
        int(os.environ.get("HS_FLARESOLVERR_HSGURU_DECKS_WAIT_MS", "0")),
    )


def flaresolverr_session_per_source() -> bool:
    """New FlareSolverr browser session per source during refresh (better IP/cookie isolation)."""
    return os.environ.get(
        "HS_FLARESOLVERR_SESSION_PER_SOURCE", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def proxy_check_url() -> str:
    return os.environ.get("HS_PROXY_CHECK_URL", "https://api.ipify.org").strip()


def hsreplay_email() -> str | None:
    value = os.environ.get("HSREPLAY_EMAIL", "").strip()
    return value or None


def hsreplay_password() -> str | None:
    value = os.environ.get("HSREPLAY_PASSWORD", "").strip()
    return value or None


def hsreplay_storage_path() -> Path:
    return Path(
        os.environ.get("HSREPLAY_STORAGE_PATH", str(data_dir() / "hsreplay-auth.json"))
    )


def vicious_syndicate_storage_path() -> Path:
    return Path(
        os.environ.get(
            "VICIOUS_SYNDICATE_STORAGE_PATH",
            str(data_dir() / "vicious-syndicate-auth.json"),
        )
    )


def hsguru_storage_path() -> Path:
    return Path(
        os.environ.get(
            "HSGURU_STORAGE_PATH",
            str(data_dir() / "hsguru-auth.json"),
        )
    )


def scrape_do_token() -> str | None:
    value = (
        os.environ.get("HS_SCRAPE_DO_TOKEN") or os.environ.get("SCRAPE_DO_TOKEN") or ""
    ).strip()
    return value or None


def scrape_do_timeout_seconds() -> float:
    return max(15.0, float(os.environ.get("HS_SCRAPE_DO_TIMEOUT_SECONDS", "120")))


def hsreplay_scrape_do_max_requests() -> int:
    """Maximum reserved Scrape.do JSON calls in one parser refresh."""
    return max(0, int(os.environ.get("HS_HSREPLAY_SCRAPE_DO_MAX_REQUESTS", "120")))


def hsreplay_scrape_do_max_credits() -> int:
    """Maximum reserved Scrape.do credits for HSReplay JSON per refresh."""
    return max(0, int(os.environ.get("HS_HSREPLAY_SCRAPE_DO_MAX_CREDITS", "160")))


def hsreplay_scrape_do_max_concurrency() -> int:
    """Concurrent non-rendered Scrape.do HSReplay JSON requests."""
    return max(1, int(os.environ.get("HS_HSREPLAY_SCRAPE_DO_MAX_CONCURRENCY", "2")))


def scrapfly_api_key() -> str | None:
    """Return the active Scrapfly key without exposing it in logs."""
    from .scrapfly_keys import parse_scrapfly_api_keys, peek_scrapfly_key

    if parse_scrapfly_api_keys():
        lease = peek_scrapfly_key()
        return lease.key.key if lease else None
    value = (
        os.environ.get("SCRAPFLY_API_KEY")
        or os.environ.get("HS_SCRAPFLY_API_KEY")
        or ""
    ).strip()
    return value or None


def scrapfly_default_key_credit_limit() -> int:
    return max(1, int(os.environ.get("HS_SCRAPFLY_KEY_ROTATION_CREDITS", "1000")))


def scrapfly_key_reset_day() -> int:
    return max(1, min(28, int(os.environ.get("HS_SCRAPFLY_KEY_RESET_DAY", "22"))))


def scrapfly_timeout_seconds() -> float:
    return max(160.0, float(os.environ.get("HS_SCRAPFLY_TIMEOUT_SECONDS", "160")))


def brightdata_unlocker_enabled() -> bool:
    return os.environ.get(
        "HS_BRIGHTDATA_UNLOCKER_ENABLED", "false"
    ).strip().lower() in {"1", "true", "yes", "on"}


def brightdata_api_key() -> str | None:
    """Return the Bright Data bearer token without logging or fingerprinting it."""
    value = os.environ.get("HS_BRIGHTDATA_API_KEY", "").strip()
    return value or None


def brightdata_unlocker_zone() -> str | None:
    value = os.environ.get("HS_BRIGHTDATA_UNLOCKER_ZONE", "").strip()
    return value or None


def brightdata_source_ids() -> set[str]:
    raw = os.environ.get("HS_BRIGHTDATA_SOURCE_IDS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def brightdata_monthly_billable_limit() -> int:
    # A zero default makes the paid fallback fail closed until an operator has
    # explicitly chosen a monthly ceiling.
    try:
        return max(
            0,
            int(os.environ.get("HS_BRIGHTDATA_MONTHLY_BILLABLE_LIMIT", "0")),
        )
    except ValueError:
        return 0


def brightdata_timeout_seconds() -> float:
    try:
        configured = float(os.environ.get("HS_BRIGHTDATA_TIMEOUT_SECONDS", "180"))
    except ValueError:
        configured = 180.0
    return min(300.0, max(30.0, configured))


def brightdata_circuit_failure_threshold() -> int:
    try:
        configured = int(os.environ.get("HS_BRIGHTDATA_CIRCUIT_FAILURE_THRESHOLD", "3"))
    except ValueError:
        configured = 3
    return max(1, configured)


def brightdata_circuit_cooldown_seconds() -> int:
    try:
        configured = int(
            os.environ.get("HS_BRIGHTDATA_CIRCUIT_COOLDOWN_SECONDS", "1800")
        )
    except ValueError:
        configured = 1800
    return max(60, configured)


def openrouter_api_key() -> str | None:
    """Return the OpenRouter bearer token without logging or fingerprinting it."""
    value = os.environ.get("HS_OPENROUTER_API_KEY", "").strip()
    return value or None


def ai_review_enabled() -> bool:
    return os.environ.get("HS_AI_REVIEW_ENABLED", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ai_review_mode() -> str:
    value = os.environ.get("HS_AI_REVIEW_MODE", "observe").strip().lower()
    return value if value in {"observe", "quarantine"} else "observe"


def ai_review_model() -> str:
    return (
        os.environ.get(
            "HS_AI_REVIEW_MODEL",
            "google/gemma-4-26b-a4b-it",
        ).strip()
        or "google/gemma-4-26b-a4b-it"
    )


def ai_review_source_ids() -> set[str]:
    raw = os.environ.get("HS_AI_REVIEW_SOURCE_IDS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def ai_review_timeout_seconds() -> float:
    try:
        value = float(os.environ.get("HS_AI_REVIEW_TIMEOUT_SECONDS", "15"))
    except ValueError:
        value = 15.0
    return min(120.0, max(5.0, value))


def ai_review_max_tokens() -> int:
    try:
        value = int(os.environ.get("HS_AI_REVIEW_MAX_TOKENS", "320"))
    except ValueError:
        value = 320
    return min(800, max(64, value))


def ai_review_max_prompt_chars() -> int:
    try:
        value = int(os.environ.get("HS_AI_REVIEW_MAX_PROMPT_CHARS", "12000"))
    except ValueError:
        value = 12000
    return min(20000, max(2000, value))


def ai_review_confidence_threshold() -> float:
    try:
        value = float(os.environ.get("HS_AI_REVIEW_CONFIDENCE_THRESHOLD", "0.95"))
    except ValueError:
        value = 0.95
    return min(1.0, max(0.5, value))


def ai_review_max_concurrency() -> int:
    """Legacy per-lane concurrency setting kept for existing deployments."""

    try:
        value = int(os.environ.get("HS_AI_REVIEW_MAX_CONCURRENCY", "2"))
    except ValueError:
        value = 2
    return min(8, max(1, value))


def _ai_review_lane_concurrency(variable: str, *, default: int) -> int:
    raw = os.environ.get(variable)
    if raw is None:
        raw = os.environ.get("HS_AI_REVIEW_MAX_CONCURRENCY", str(default))
    try:
        value = int(raw)
    except ValueError:
        value = default
    return min(8, max(1, value))


def ai_review_candidate_max_concurrency() -> int:
    return _ai_review_lane_concurrency(
        "HS_AI_REVIEW_CANDIDATE_MAX_CONCURRENCY",
        default=1,
    )


def ai_review_diagnosis_max_concurrency() -> int:
    return _ai_review_lane_concurrency(
        "HS_AI_REVIEW_DIAGNOSIS_MAX_CONCURRENCY",
        default=2,
    )


def ai_review_max_per_refresh() -> int:
    try:
        value = int(os.environ.get("HS_AI_REVIEW_MAX_PER_REFRESH", "10"))
    except ValueError:
        value = 10
    return min(1000, max(1, value))


def ai_review_diagnose_failures_enabled() -> bool:
    return os.environ.get("HS_AI_REVIEW_DIAGNOSE_FAILURES", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def ai_review_max_failures_per_refresh() -> int:
    try:
        value = int(os.environ.get("HS_AI_REVIEW_MAX_FAILURES_PER_REFRESH", "20"))
    except ValueError:
        value = 20
    return min(200, max(1, value))


def ai_review_retry_attempts() -> int:
    try:
        value = int(os.environ.get("HS_AI_REVIEW_RETRY_ATTEMPTS", "2"))
    except ValueError:
        value = 2
    return min(3, max(1, value))


def ai_review_retry_base_seconds() -> float:
    try:
        value = float(os.environ.get("HS_AI_REVIEW_RETRY_BASE_SECONDS", "0.25"))
    except ValueError:
        value = 0.25
    return min(2.0, max(0.0, value))


def ai_review_circuit_failure_threshold() -> int:
    try:
        value = int(os.environ.get("HS_AI_REVIEW_CIRCUIT_FAILURE_THRESHOLD", "3"))
    except ValueError:
        value = 3
    return min(20, max(1, value))


def ai_review_post_refresh_timeout_seconds() -> float:
    try:
        value = float(os.environ.get("HS_AI_REVIEW_POST_REFRESH_TIMEOUT_SECONDS", "30"))
    except ValueError:
        value = 30.0
    return min(120.0, max(5.0, value))


def hsguru_current_patch_period() -> str | None:
    value = os.environ.get("HS_HSGURU_PATCH_PERIOD", "").strip()
    if not value or value.lower() == "auto":
        return None
    return value if value.startswith("patch_") else f"patch_{value}"


def telegram_bot_token() -> str | None:
    value = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    return value or None


def telegram_chat_id() -> str | None:
    value = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    return value or None


def refresh_parallel_light() -> int:
    v = max(1, int(os.environ.get("HS_REFRESH_PARALLEL_LIGHT", "2")))
    if v > 3:
        # Guard: elevated parallelism increases chance of 429/407/CF rate limits and FS contention.
        # Only raise after 7+ days of clean crons (no proxy 407, FS stable, low "table too small").
        import logging

        logging.getLogger(__name__).warning(
            "HS_REFRESH_PARALLEL_LIGHT=%s >3 — monitor /ops/summary and logs for 407/429/FS errors before keeping this value",
            v,
        )
    return v


def refresh_parallel_medium() -> int:
    v = max(1, int(os.environ.get("HS_REFRESH_PARALLEL_MEDIUM", "1")))
    if v > 2:
        import logging

        logging.getLogger(__name__).warning(
            "HS_REFRESH_PARALLEL_MEDIUM=%s >2 — elevated API concurrency; watch for source throttling",
            v,
        )
    return v


def refresh_preflight_strict() -> bool:
    return os.environ.get("HS_REFRESH_PREFLIGHT_STRICT", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def refresh_preflight_probe_hsreplay() -> bool:
    return os.environ.get(
        "HS_REFRESH_PREFLIGHT_PROBE_HSREPLAY", "true"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def api_json_attempts_per_channel() -> int:
    return max(1, int(os.environ.get("HS_API_JSON_ATTEMPTS_PER_CHANNEL", "2")))


def api_json_retry_delay_seconds() -> float:
    return float(os.environ.get("HS_API_JSON_RETRY_DELAY_SECONDS", "2.0"))


def refresh_parallel_stagger_min() -> float:
    return float(os.environ.get("HS_REFRESH_PARALLEL_STAGGER_MIN", "0.3"))


def refresh_parallel_stagger_max() -> float:
    return float(os.environ.get("HS_REFRESH_PARALLEL_STAGGER_MAX", "1.0"))


def refresh_delay_browser_only() -> bool:
    return os.environ.get("HS_REFRESH_DELAY_BROWSER_ONLY", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def hsreplay_json_channels() -> list[str]:
    raw = os.environ.get("HS_HSREPLAY_JSON_CHANNELS", DEFAULT_HSREPLAY_JSON_CHANNELS)
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def hsreplay_markdown_channels() -> list[str]:
    """Channels for HSReplay markdown pages (BG comps); Jina omitted by default (451)."""
    raw = os.environ.get(
        "HS_HSREPLAY_MARKDOWN_CHANNELS", DEFAULT_HSREPLAY_MARKDOWN_CHANNELS
    )
    return [part.strip().lower() for part in raw.split(",") if part.strip()]


def stale_dataset_hours() -> float:
    return float(os.environ.get("HS_STALE_HOURS", "12"))


def bg_comp_detail_cache_ttl_hours() -> float:
    """TTL for cached HSReplay battlegrounds comps detail markdown."""
    return max(0.1, float(os.environ.get("HS_BG_COMP_DETAIL_CACHE_TTL_HOURS", "6")))


def telegram_alert_dedup_seconds() -> int:
    return max(60, int(os.environ.get("HS_TELEGRAM_ALERT_DEDUP_SECONDS", "3600")))


def log_rotate_max_bytes() -> int:
    return max(
        1_000_000, int(os.environ.get("HS_LOG_ROTATE_MAX_BYTES", str(50 * 1024 * 1024)))
    )


def log_rotate_max_age_days() -> int:
    return max(1, int(os.environ.get("HS_LOG_ROTATE_MAX_AGE_DAYS", "7")))


def log_retention_days() -> int:
    """Maximum age of compressed structured-log archives."""
    return max(1, int(os.environ.get("HS_LOG_RETENTION_DAYS", "14")))


def log_retention_archives() -> int:
    """Maximum number of compressed structured-log archives to retain."""
    return max(1, int(os.environ.get("HS_LOG_RETENTION_ARCHIVES", "5")))


def quality_thresholds_path() -> Path:
    return Path(
        os.environ.get(
            "HS_QUALITY_THRESHOLDS_PATH",
            str(
                Path(__file__).resolve().parent.parent
                / "config"
                / "quality_thresholds.json"
            ),
        )
    )


def dataset_regression_drop_ratio() -> float:
    return float(os.environ.get("HS_DATASET_REGRESSION_DROP_RATIO", "0.30"))


def firecrawl_api_key() -> str | None:
    """Return the currently active Firecrawl key (rotating pool or legacy single key)."""
    from .firecrawl_keys import parse_firecrawl_api_keys, peek_firecrawl_key

    if parse_firecrawl_api_keys():
        lease = peek_firecrawl_key()
        return lease.key.key if lease else None
    value = (
        os.environ.get("FIRECRAWL_API_KEY")
        or os.environ.get("HS_FIRECRAWL_API_KEY")
        or ""
    ).strip()
    return value or None


def firecrawl_default_key_credit_limit() -> int:
    return max(1, int(os.environ.get("HS_FIRECRAWL_KEY_ROTATION_CREDITS", "1000")))


def firecrawl_key_reset_day() -> int:
    """Day of month when Firecrawl key rotation counters reset (billing-cycle aligned)."""
    # Cap at 28 so February never overflows.
    return max(1, min(28, int(os.environ.get("HS_FIRECRAWL_KEY_RESET_DAY", "22"))))


def fun_deck_min_score() -> float:
    return min(1.0, max(0.0, float(os.environ.get("HS_FUN_DECK_MIN_SCORE", "0.55"))))


def fun_deck_max_meta_similarity() -> float:
    return min(
        1.0, max(0.0, float(os.environ.get("HS_FUN_DECK_MAX_META_SIMILARITY", "0.42")))
    )


def fun_deck_retention_hours() -> int:
    return max(1, int(os.environ.get("HS_FUN_DECK_RETENTION_HOURS", "168")))


def firecrawl_max_age_ms() -> int:
    return max(0, int(os.environ.get("HS_FIRECRAWL_MAX_AGE_MS", "172800000")))


def firecrawl_wait_ms() -> int:
    return max(0, int(os.environ.get("HS_FIRECRAWL_WAIT_MS", "5000")))


def firecrawl_timeout_ms() -> int:
    return max(1000, int(os.environ.get("HS_FIRECRAWL_TIMEOUT_MS", "30000")))


def firecrawl_hsguru_matchups_timeout_ms() -> int:
    return min(
        300_000,
        max(
            firecrawl_timeout_ms(),
            int(os.environ.get("HS_FIRECRAWL_HSGURU_MATCHUPS_TIMEOUT_MS", "180000")),
        ),
    )


def firecrawl_primary_source_ids() -> set[str]:
    raw = os.environ.get(
        "HS_FIRECRAWL_PRIMARY_SOURCE_IDS",
        (
            "hsguru_streamer_decks_legend_1000,"
            "hsguru_matchups_legend,"
            "hsguru_matchups_wild_legend"
        ),
    )
    return {part.strip() for part in raw.split(",") if part.strip()}


def firecrawl_fallback_source_ids() -> set[str]:
    raw = os.environ.get(
        "HS_FIRECRAWL_FALLBACK_SOURCE_IDS",
        ",".join(
            [
                "hsguru_meta_standard_legend",
                "hsguru_meta_standard_diamond_4to1",
                "hsguru_meta_wild_legend",
                "hsguru_meta_wild_diamond_4to1",
                "hsguru_meta_standard_top_5k",
                "hsguru_meta_standard_top_legend",
                "hsguru_meta_wild_top_legend",
                "hsguru_meta_wild_top_5k",
                "hsguru_matchups_legend",
                "hsguru_matchups_wild_legend",
                "hsguru_matchups_diamond_4to1",
                "hsreplay_battlegrounds_comps",
                "hsreplay_battlegrounds_heroes",
                "hsreplay_battlegrounds_trinkets_lesser",
                "hsreplay_battlegrounds_trinkets_greater",
                *sorted(TRINKET_SLICE_SOURCE_IDS),
                "hsreplay_decks_trending",
                "heartharena_tierlist",
                "vicious_syndicate_radars",
            ]
        ),
    )
    return {part.strip() for part in raw.split(",") if part.strip()}


def firecrawl_fallback_max_attempts_per_refresh() -> int:
    return max(
        0, int(os.environ.get("HS_FIRECRAWL_FALLBACK_MAX_ATTEMPTS_PER_REFRESH", "8"))
    )


def firecrawl_fallback_max_attempts_per_source() -> int:
    return max(
        1, int(os.environ.get("HS_FIRECRAWL_FALLBACK_MAX_ATTEMPTS_PER_SOURCE", "2"))
    )


def fingerprint_node_enabled() -> bool:
    return os.environ.get("HS_FINGERPRINT_SUITE_ENABLED", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def cloakbrowser_humanize() -> bool:
    return os.environ.get("HS_CLOAKBROWSER_HUMANIZE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def cloakbrowser_geoip() -> bool:
    return os.environ.get("HS_CLOAKBROWSER_GEOIP", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def cloakbrowser_headless() -> bool:
    return os.environ.get("HS_CLOAKBROWSER_HEADLESS", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def cloakbrowser_hsguru_headless() -> bool:
    """HSGuru often needs headed mode even with CloakBrowser patches."""
    return os.environ.get(
        "HS_CLOAKBROWSER_HSGURU_HEADLESS", "false"
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def cloakbrowser_fingerprint_seed(source_id: str) -> int:
    """Stable per-source seed so repeat visits look like the same device."""
    import hashlib

    digest = hashlib.md5(source_id.encode("utf-8")).hexdigest()
    offset = int(digest[:8], 16) % 89_999
    return 10_000 + offset


def scrapling_solve_cloudflare() -> bool:
    return os.environ.get("HS_SCRAPLING_SOLVE_CLOUDFLARE", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def scrapling_disable_resources() -> bool:
    return os.environ.get("HS_SCRAPLING_DISABLE_RESOURCES", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def scrapling_timeout_ms() -> int:
    return max(30_000, int(os.environ.get("HS_SCRAPLING_TIMEOUT_MS", "180000")))


def fetch_backend_max_seconds() -> float | None:
    """Per-backend wall clock cap in rotator (unset = no extra cap beyond fetch timeouts)."""
    raw = os.environ.get("HS_FETCH_BACKEND_MAX_SECONDS", "").strip()
    if not raw:
        return None
    return max(45.0, float(raw))


def browser_preferred_sticky_backends() -> frozenset[str]:
    raw = os.environ.get(
        "HS_BROWSER_PREFERRED_STICKY_BACKENDS",
        "flaresolverr,patchright,playwright",
    )
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def fetch_playwright_stealth_enabled() -> bool:
    return os.environ.get("HS_FETCH_PLAYWRIGHT_STEALTH", "true").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
