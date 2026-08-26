#!/usr/bin/env python3
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.patches_db import count_patches, delete_patches_not_in, get_patch, upsert_patch

USER_AGENT = "HSDataAPI/0.1 (+https://api.kolodahearthstone.com)"
WIKI_PATCHES_URL = "https://hearthstone.wiki.gg/wiki/Patches"
OFFICIAL_NEWS_URL = "https://hearthstone.blizzard.com/en-us/news?category=patch-notes"
HS_MANACOST_SITEMAP_URL = "https://hs-manacost.ru/sitemap.xml"
HS_MANACOST_WP_POSTS_URL = "https://hs-manacost.ru/wp-json/wp/v2/posts"

OFFICIAL_SLUG_PREFIXES = (
    "obnovlenie-",
    "obnovleniye-",
    "obnovleniya-",
    "patch-",
    "opisanie-obnovleniya-",
    "podrobnaya-informaciya-ob-obnovlenii-",
    "obnovlenie-dlya-hearthstone-",
    "obnovlenie-hearthstone-",
    "informaciya-o-patche-",
    "servernoe-obnovlenie-",
)
BLOCKED_SLUG_FRAGMENTS = (
    "runeterra",
    "legends-of-runeterra",
    "league-of-legends",
)
WP_POST_CACHE: dict[str, dict] = {}
FETCH_ATTEMPTS = 3
FETCH_RETRY_DELAYS_SECONDS = (1.0, 3.0)
RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
PARTIAL_EXIT_CODE = 10
ALLOWED_HS_MANACOST_HOSTS = frozenset({"hs-manacost.ru", "www.hs-manacost.ru"})
ALLOWED_OFFICIAL_NEWS_HOSTS = frozenset(
    {"hearthstone.blizzard.com", "playhearthstone.com"}
)
MAX_CONSECUTIVE_DETAIL_FAILURES = 3
MAX_RUN_SECONDS = 30 * 60


class SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, allowed_hosts: frozenset[str]) -> None:
        super().__init__()
        self.allowed_hosts = allowed_hosts

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp,
        code: int,
        msg: str,
        headers,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not _is_allowed_https_url(newurl, self.allowed_hosts):
            raise urllib.error.HTTPError(
                newurl,
                code,
                "Redirect target is not an approved HTTPS host",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
        if tag in {"p", "li", "h1", "h2", "h3", "h4", "br"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1
        if tag in {"p", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        text = " ".join(self.parts)
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return html.unescape(text).strip()


class HeadingExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sections: list[dict[str, str]] = []
        self.current_tag: str | None = None
        self.current_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"h2", "h3", "h4"}:
            self.current_tag = tag
            self.current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == self.current_tag:
            title = html.unescape(" ".join(self.current_text)).strip()
            title = re.sub(r"\s+", " ", title)
            if title:
                self.sections.append({"level": tag, "title": title})
            self.current_tag = None
            self.current_text = []

    def handle_data(self, data: str) -> None:
        if self.current_tag:
            self.current_text.append(data.strip())


def _is_retryable_fetch_error(exc: OSError) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in RETRYABLE_HTTP_STATUSES
    return True


def _is_allowed_https_url(url: str, allowed_hosts: frozenset[str]) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname in allowed_hosts
        and parsed.username is None
        and parsed.password is None
        and port in {None, 443}
    )


def _is_allowed_hs_manacost_url(url: str) -> bool:
    return _is_allowed_https_url(url, ALLOWED_HS_MANACOST_HOSTS)


def _contributes_to_detail_circuit(exc: BaseException) -> bool:
    if isinstance(exc, json.JSONDecodeError):
        return True
    if isinstance(exc, OSError):
        return _is_retryable_fetch_error(exc)
    return False


def _open_url(
    req: urllib.request.Request,
    *,
    timeout: float,
    allowed_hosts: frozenset[str],
):
    opener = urllib.request.build_opener(SafeRedirectHandler(allowed_hosts))
    return opener.open(req, timeout=timeout)


def fetch_text(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    initial_host = parsed.hostname
    allowed_hosts = (
        ALLOWED_HS_MANACOST_HOSTS
        if initial_host in ALLOWED_HS_MANACOST_HOSTS
        else frozenset({initial_host}) if initial_host else frozenset()
    )
    if not _is_allowed_https_url(url, allowed_hosts):
        raise ValueError("Source URL must use its approved HTTPS host")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, FETCH_ATTEMPTS + 1):
        try:
            with _open_url(req, timeout=30, allowed_hosts=allowed_hosts) as resp:
                return resp.read().decode("utf-8", "ignore")
        except OSError as exc:
            if attempt >= FETCH_ATTEMPTS or not _is_retryable_fetch_error(exc):
                raise
            time.sleep(FETCH_RETRY_DELAYS_SECONDS[attempt - 1])
    raise AssertionError("unreachable")


def latest_wiki_versions(limit: int | None) -> list[str]:
    page = fetch_text(WIKI_PATCHES_URL)
    versions: list[str] = []
    for match in re.finditer(r"Patch ([0-9]+(?:\.[0-9]+){1,3})", page):
        version = match.group(1)
        if version not in versions:
            versions.append(version)
        if limit is not None and len(versions) >= limit:
            break
    return versions


def latest_official_patches(limit: int | None) -> list[dict[str, str]]:
    page = fetch_text(OFFICIAL_NEWS_URL)
    entities: list[dict] = []

    # Blizzard's JSON-LD list can lag behind the visible news feed.  The same
    # server-rendered page exposes its current sticky articles as JSON; on the
    # 36.4 release day this was the only machine-readable entry containing the
    # new patch notes.
    marker = "var stickyBlogList = "
    marker_at = page.find(marker)
    if marker_at >= 0:
        try:
            sticky, _ = json.JSONDecoder().raw_decode(
                page[marker_at + len(marker) :].lstrip()
            )
        except (json.JSONDecodeError, TypeError):
            sticky = []
        if isinstance(sticky, list):
            entities.extend(item for item in sticky[:50] if isinstance(item, dict))

    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for raw_script in scripts:
        try:
            payload = json.loads(html.unescape(raw_script))
        except (json.JSONDecodeError, TypeError):
            continue
        json_ld_entities = ((payload.get("mainEntity") or {}).get("itemListElement") or [])
        if isinstance(json_ld_entities, list):
            entities.extend(
                item for item in json_ld_entities if isinstance(item, dict)
            )

    patches: list[dict[str, str]] = []
    seen: set[str] = set()
    for entity in entities:
        headline = str(entity.get("headline") or entity.get("title") or "").strip()
        match = re.fullmatch(r"([0-9]+(?:\.[0-9]+){1,3}) Patch Notes", headline)
        if not match or match.group(1) in seen:
            continue
        version = match.group(1)
        official_url = str(entity.get("url") or entity.get("defaultUrl") or "")
        if official_url and not _is_allowed_https_url(
            official_url, ALLOWED_OFFICIAL_NEWS_HOSTS
        ):
            official_url = ""
        published_at = str(entity.get("datePublished") or "")
        publish_ms = entity.get("publish")
        if not published_at and isinstance(publish_ms, (int, float)):
            published_at = datetime.fromtimestamp(publish_ms / 1000, UTC).isoformat()
        seen.add(version)
        patches.append(
            {
                "version": version,
                "official_title": headline,
                "official_url": official_url,
                "official_published_at": published_at,
                "official_modified_at": str(
                    entity.get("dateModified") or entity.get("updated_at") or ""
                ),
                "official_summary": str(
                    entity.get("description") or entity.get("summary") or ""
                ).strip(),
            }
        )
    if not patches:
        raise RuntimeError("Official Hearthstone news returned no patch notes")
    patches.sort(
        key=lambda item: tuple(int(part) for part in item["version"].split(".")),
        reverse=True,
    )
    return patches if limit is None else patches[:limit]


def combined_patch_catalog(limit: int | None) -> list[dict[str, str]]:
    official = latest_official_patches(limit)
    wiki_versions = latest_wiki_versions(None)
    official_by_wiki_version: dict[str, dict[str, str]] = {}
    unmatched_official: list[dict[str, str]] = []
    for official_patch in official:
        official_version = official_patch["version"]
        wiki_version = next(
            (
                candidate
                for candidate in wiki_versions
                if candidate == official_version
                or hs_manacost_version(candidate) == official_version
            ),
            None,
        )
        if wiki_version:
            official_by_wiki_version[wiki_version] = official_patch
        else:
            unmatched_official.append(official_patch)
    catalog = [
        {**official_by_wiki_version.get(version, {}), "version": version}
        for version in wiki_versions
    ]
    catalog.extend(unmatched_official)
    catalog.sort(
        key=lambda item: tuple(int(part) for part in item["version"].split(".")),
        reverse=True,
    )
    if limit is not None:
        catalog = catalog[:limit]
    return catalog


def current_patch_version() -> str:
    """Return the current public patch without the Hearthstone build suffix.

    Blizzard's news index can lag behind the game client for several hours.  The
    combined catalog also consults wiki.gg, which publishes client builds such as
    ``36.2.0.248348``.  Site filters and HSGuru use the public ``36.2.0`` form.
    """
    latest = combined_patch_catalog(1)
    version = str((latest[0] if latest else {}).get("version") or "")
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", version):
        raise RuntimeError("Patch catalog returned no valid current version")
    parts = version.split(".")
    return ".".join(parts[:3]) if len(parts) == 4 else version


def validate_full_catalog(catalog: list[dict[str, str]], *, existing_count: int) -> None:
    versions = [str(item.get("version") or "") for item in catalog]
    unique_versions = {version for version in versions if version}
    minimum_count = max(100, int(existing_count * 0.80))
    if len(unique_versions) < minimum_count:
        raise RuntimeError(
            "Patch catalog truncation guard rejected full refresh: "
            f"discovered {len(unique_versions)}, required at least {minimum_count} "
            f"(existing {existing_count})"
        )
    if len(unique_versions) != len(versions):
        raise RuntimeError("Patch catalog contains empty or duplicate versions")


def hs_manacost_version(wiki_version: str) -> str:
    parts = wiki_version.split(".")
    if len(parts) >= 4:
        wiki_version = ".".join(parts[:3])
    return wiki_version[:-2] if wiki_version.endswith(".0") else wiki_version


def hs_manacost_version_candidates(wiki_version: str) -> list[str]:
    candidates = [wiki_version]
    parts = wiki_version.split(".")
    if len(parts) >= 4:
        candidates.append(".".join(parts[:3]))
    short = hs_manacost_version(wiki_version)
    candidates.append(short)
    out: list[str] = []
    for candidate in candidates:
        if candidate not in out:
            out.append(candidate)
    return out


def slug_for_url(url: str) -> str:
    return url.rstrip("/").split("/")[-1].lower()


def is_blocked_slug(slug: str) -> bool:
    return any(fragment in slug for fragment in BLOCKED_SLUG_FRAGMENTS)


def has_official_slug_prefix(slug: str) -> bool:
    return any(slug.startswith(prefix) for prefix in OFFICIAL_SLUG_PREFIXES)


def contains_dashed_version(slug: str, dashed_version: str) -> bool:
    pattern = re.compile(rf"(?:^|-){re.escape(dashed_version)}(?:-(?!\d)|$)")
    return bool(pattern.search(slug))


def contains_loose_dashed_version(slug: str, dashed_version: str) -> bool:
    pattern = re.compile(rf"(?:^|-){re.escape(dashed_version)}(?:-|$)")
    return bool(pattern.search(slug))


def special_slug_version_candidates(wiki_version: str) -> list[tuple[str, str, str]]:
    parts = wiki_version.split(".")
    hs_version = hs_manacost_version(wiki_version)
    candidates: list[tuple[str, str, str]] = []
    if len(parts) >= 4:
        candidates.append((hs_version, parts[3], "build"))
        candidates.append((hs_version, "".join(parts), "compact_full"))
        if parts[2] == "0":
            candidates.append((hs_version, "-".join(parts[:3] + ["0"]), "zero_tail"))
    short_parts = hs_version.split(".")
    if len(short_parts) == 2 and all(part.isdigit() and len(part) <= 2 for part in short_parts):
        candidates.append((hs_version, "".join(short_parts), "compact_short"))
    out: list[tuple[str, str, str]] = []
    for candidate in candidates:
        if candidate not in out:
            out.append(candidate)
    return out


def sitemap_match_score(slug: str, wiki_version: str, hs_version: str, *, special: bool = False) -> int:
    score = 74 if special else 90
    if slug.startswith(("obnovlenie-", "obnovleniye-", "obnovleniya-")):
        score += 8
    if slug.startswith("patch-"):
        score -= 4
    if hs_version == wiki_version:
        score += 4
    if slug.startswith(("obnovlenie-hearthstone-", "obnovlenie-dlya-hearthstone-")):
        score -= 2
    if slug.startswith("servernoe-obnovlenie-"):
        score += 5
    return score


def score_sitemap_slug(slug: str, wiki_version: str) -> tuple[int, str] | None:
    if is_blocked_slug(slug) or not has_official_slug_prefix(slug):
        return None

    best: tuple[int, str] | None = None
    for hs_version in hs_manacost_version_candidates(wiki_version):
        dashed = hs_version.replace(".", "-")
        if contains_dashed_version(slug, dashed):
            candidate = (sitemap_match_score(slug, wiki_version, hs_version), hs_version)
            if best is None or candidate[0] > best[0]:
                best = candidate

    for hs_version, special_version, special_kind in special_slug_version_candidates(wiki_version):
        if special_kind == "compact_short" and not slug.startswith("patch-"):
            continue
        if contains_dashed_version(slug, special_version):
            candidate = (sitemap_match_score(slug, wiki_version, hs_version, special=True), hs_version)
            if best is None or candidate[0] > best[0]:
                best = candidate

    return best


def loose_sitemap_match(slug: str, wiki_version: str) -> str | None:
    if is_blocked_slug(slug) or not has_official_slug_prefix(slug):
        return None
    for hs_version in hs_manacost_version_candidates(wiki_version):
        dashed = hs_version.replace(".", "-")
        if contains_loose_dashed_version(slug, dashed):
            return hs_version
    return None


def strip_title(markup: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", markup or "")).strip()


def title_matches_patch(title: str, wiki_version: str) -> str | None:
    plain = strip_title(title)
    lowered = plain.lower()
    if any(fragment in lowered for fragment in ("runeterra", "legends of runeterra", "league of legends")):
        return None
    if not re.search(r"(обновлен|патч|patch|update)", lowered, re.IGNORECASE):
        return None

    for hs_version in hs_manacost_version_candidates(wiki_version):
        pattern = re.compile(rf"(?<![\d.]){re.escape(hs_version)}(?![\d.])")
        if pattern.search(plain):
            return hs_version

    parts = wiki_version.split(".")
    if len(parts) >= 4 and re.search(rf"(?<!\d){re.escape(parts[3])}(?!\d)", plain):
        return hs_manacost_version(wiki_version)
    return None


def hs_manacost_post_urls() -> list[str]:
    root = ET.fromstring(fetch_text(HS_MANACOST_SITEMAP_URL))
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    sitemap_candidates = [
        loc.text.strip()
        for loc in root.findall(".//sm:loc", ns)
        if loc.text and "post-sitemap" in loc.text
    ]
    invalid_sitemaps = [
        sitemap_url
        for sitemap_url in sitemap_candidates
        if not _is_allowed_hs_manacost_url(sitemap_url)
    ]
    if invalid_sitemaps:
        raise RuntimeError("hs-manacost.ru sitemap referenced an unapproved host")
    if not sitemap_candidates:
        raise RuntimeError("hs-manacost.ru sitemap returned no post sitemaps")
    urls: list[str] = []
    for sitemap_url in sitemap_candidates:
        sitemap = ET.fromstring(fetch_text(sitemap_url))
        urls.extend(
            loc.text.strip()
            for loc in sitemap.findall(".//sm:loc", ns)
            if loc.text and _is_allowed_hs_manacost_url(loc.text.strip())
        )
    if not urls:
        raise RuntimeError("hs-manacost.ru post sitemaps returned no approved post URLs")
    return urls


def find_patch_url(post_urls: list[str], wiki_version: str) -> tuple[str | None, str | None]:
    candidates: list[tuple[int, str, str]] = []
    loose_candidates: list[tuple[str, str]] = []
    for url in post_urls:
        slug = slug_for_url(url)
        scored = score_sitemap_slug(slug, wiki_version)
        if scored:
            score, hs_version = scored
            candidates.append((score, url, hs_version))
        elif loose_sitemap_match(slug, wiki_version):
            loose_candidates.append((url, slug))

    seen_slugs = {slug_for_url(url) for _, url, _ in candidates}
    if not candidates:
        for url, slug in loose_candidates:
            if slug in seen_slugs:
                continue
            try:
                post = wp_post_by_slug(slug)
            except Exception:
                continue
            hs_version = title_matches_patch((post.get("title") or {}).get("rendered") or "", wiki_version)
            if not hs_version:
                continue
            candidates.append((82, post.get("link") or url, hs_version))
            seen_slugs.add(slug)

    if not candidates:
        return None, None
    candidates.sort(
        key=lambda item: (
            -item[0],
            not slug_for_url(item[1]).startswith(("obnovlenie-", "obnovleniye-", "obnovleniya-")),
            slug_for_url(item[1]),
        )
    )
    return candidates[0][1], candidates[0][2]


def wp_post_by_slug(slug: str) -> dict:
    if slug in WP_POST_CACHE:
        return WP_POST_CACHE[slug]
    query = urllib.parse.urlencode(
        {
            "slug": slug,
            "_fields": "id,date,modified,link,title,slug,excerpt,content,categories,tags",
        }
    )
    url = f"https://hs-manacost.ru/wp-json/wp/v2/posts?{query}"
    posts = json.loads(fetch_text(url))
    if not posts:
        raise RuntimeError(f"WordPress API returned no post for slug {slug}")
    WP_POST_CACHE[slug] = posts[0]
    return WP_POST_CACHE[slug]


def strip_html(markup: str) -> str:
    parser = TextExtractor()
    parser.feed(markup or "")
    return parser.text()


def headings(markup: str) -> list[dict[str, str]]:
    parser = HeadingExtractor()
    parser.feed(markup or "")
    return parser.sections


def _base_wiki_fields(
    version: str,
    *,
    wiki_rank: int,
    hs_version: str | None,
    official: dict[str, str] | None,
) -> dict:
    return {
        "version": version,
        "display_version": version,
        "wiki_rank": wiki_rank,
        "wiki_title": f"Patch {version}",
        "wiki_url": f"https://hearthstone.wiki.gg/wiki/Patch_{version}",
        "hs_manacost_version": hs_version,
        **(official or {}),
    }


def build_wiki_patch(
    version: str,
    *,
    wiki_rank: int,
    hs_version: str | None = None,
    official: dict[str, str] | None = None,
) -> dict:
    return {
        **_base_wiki_fields(
            version,
            wiki_rank=wiki_rank,
            hs_version=hs_version,
            official=official,
        ),
        "match_state": "missing_manacost",
        "fetched_at": datetime.now(UTC).isoformat(),
    }


def build_patch(
    version: str,
    source_url: str,
    hs_version: str,
    *,
    wiki_rank: int,
    official: dict[str, str] | None = None,
) -> dict:
    slug = source_url.rstrip("/").split("/")[-1]
    post = wp_post_by_slug(slug)
    content_html = (post.get("content") or {}).get("rendered") or ""
    excerpt_html = (post.get("excerpt") or {}).get("rendered") or ""
    title = html.unescape(re.sub(r"<[^>]+>", "", (post.get("title") or {}).get("rendered") or "")).strip()
    excerpt = strip_html(excerpt_html)
    content_text = strip_html(content_html)
    summary = excerpt or "\n".join(content_text.splitlines()[:2])[:500]
    return {
        **_base_wiki_fields(
            version,
            wiki_rank=wiki_rank,
            hs_version=hs_version,
            official=official,
        ),
        "title": title,
        "slug": slug,
        "source_url": post.get("link") or source_url,
        "match_state": "matched",
        "published_at": post.get("date"),
        "modified_at": post.get("modified"),
        "excerpt": excerpt,
        "summary": summary,
        "sections": headings(content_html),
        "categories": post.get("categories") or [],
        "tags": post.get("tags") or [],
        "content_text": content_text,
        "fetched_at": datetime.now(UTC).isoformat(),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed Hearthstone patch links from wiki and hs-manacost.ru.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="Import every patch listed on Hearthstone Wiki.")
    group.add_argument("--limit", type=int, default=2, help="Number of latest wiki patches to import.")
    parser.add_argument(
        "--matched-only",
        action="store_true",
        help="Store only patches that have a matching hs-manacost.ru article.",
    )
    return parser.parse_args(argv)


def main() -> int:
    # Backward compatible shorthand: `seed_hs_manacost_patches.py 10`.
    argv = sys.argv[1:]
    if len(argv) == 1 and argv[0].isdigit():
        argv = ["--limit", argv[0]]
    args = parse_args(argv)
    started_at = time.monotonic()
    limit = None if args.all else args.limit
    catalog = combined_patch_catalog(limit)
    if args.all and not args.matched_only:
        validate_full_catalog(catalog, existing_count=count_patches())
    versions = [item["version"] for item in catalog]
    post_urls = hs_manacost_post_urls()
    stored: list[dict[str, str | None]] = []
    missing: list[str] = []
    failed: list[dict[str, str]] = []
    preserved_matched: list[str] = []
    not_attempted: list[str] = []
    consecutive_detail_failures = 0
    circuit_open = False
    deadline_reached = False
    for wiki_rank, catalog_item in enumerate(catalog):
        if time.monotonic() - started_at >= MAX_RUN_SECONDS:
            deadline_reached = True
            not_attempted.extend(item["version"] for item in catalog[wiki_rank:])
            break
        version = catalog_item["version"]
        official = {key: value for key, value in catalog_item.items() if key != "version"}
        source_url, hs_version = find_patch_url(post_urls, version)
        if source_url and hs_version:
            try:
                patch = build_patch(
                    version,
                    source_url,
                    hs_version,
                    wiki_rank=wiki_rank,
                    official=official,
                )
            except (OSError, RuntimeError, json.JSONDecodeError) as exc:
                failed.append({"version": version, "error_type": type(exc).__name__})
                if _contributes_to_detail_circuit(exc):
                    consecutive_detail_failures += 1
                else:
                    consecutive_detail_failures = 0
                if consecutive_detail_failures >= MAX_CONSECUTIVE_DETAIL_FAILURES:
                    circuit_open = True
                    not_attempted.extend(
                        item["version"] for item in catalog[wiki_rank + 1 :]
                    )
                    break
                continue
            consecutive_detail_failures = 0
        else:
            missing.append(version)
            if args.matched_only:
                continue
            existing = get_patch(version, include_content=False)
            if existing and existing.get("match_state") == "matched":
                preserved_matched.append(version)
                failed.append(
                    {
                        "version": version,
                        "error_type": "PreviouslyMatchedArticleMissing",
                    }
                )
                continue
            patch = build_wiki_patch(version, wiki_rank=wiki_rank, official=official)
        upsert_patch(patch)
        stored.append(
            {
                "version": version,
                "wiki_rank": patch.get("wiki_rank"),
                "hs_manacost_version": patch.get("hs_manacost_version"),
                "wiki_url": patch.get("wiki_url"),
                "official_url": patch.get("official_url"),
                "title": patch.get("title"),
                "source_url": patch.get("source_url"),
                "match_state": patch.get("match_state"),
            }
        )
    partial = bool(failed or not_attempted)
    deleted_stale = (
        delete_patches_not_in(set(versions))
        if args.all and not args.matched_only and not partial
        else 0
    )
    print(
        json.dumps(
            {
                "ok": not partial,
                "state": "partial" if partial else "ok",
                "versions_seen": len(versions),
                "stored_count": len(stored),
                "matched_count": len([item for item in stored if item.get("match_state") == "matched"]),
                "missing_manacost_count": len(missing),
                "failed_count": len(failed),
                "failed_versions": failed,
                "preserved_matched_count": len(preserved_matched),
                "preserved_matched_versions": preserved_matched,
                "not_attempted_count": len(not_attempted),
                "not_attempted_versions": not_attempted,
                "circuit_open": circuit_open,
                "deadline_reached": deadline_reached,
                "deleted_stale_count": deleted_stale,
                "missing_manacost_versions": missing,
                "stored": stored,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return PARTIAL_EXIT_CODE if partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
