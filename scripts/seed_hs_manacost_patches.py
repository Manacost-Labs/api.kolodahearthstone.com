#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.patches_db import count_patches, delete_patches_not_in, upsert_patch

USER_AGENT = "HSDataAPI/0.1 (+https://api.hs-manacost.ru)"
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


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "ignore")


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
    scripts = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        flags=re.IGNORECASE | re.DOTALL,
    )
    patches: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_script in scripts:
        try:
            payload = json.loads(html.unescape(raw_script))
        except (json.JSONDecodeError, TypeError):
            continue
        entities = ((payload.get("mainEntity") or {}).get("itemListElement") or [])
        for entity in entities:
            headline = str(entity.get("headline") or "").strip()
            match = re.fullmatch(r"([0-9]+(?:\.[0-9]+){1,3}) Patch Notes", headline)
            if not match or match.group(1) in seen:
                continue
            version = match.group(1)
            seen.add(version)
            patches.append(
                {
                    "version": version,
                    "official_title": headline,
                    "official_url": str(entity.get("url") or ""),
                    "official_published_at": str(entity.get("datePublished") or ""),
                    "official_modified_at": str(entity.get("dateModified") or ""),
                    "official_summary": str(entity.get("description") or "").strip(),
                }
            )
            if limit is not None and len(patches) >= limit:
                return patches
    if not patches:
        raise RuntimeError("Official Hearthstone news returned no patch notes")
    return patches


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
    sitemap_urls = [
        loc.text
        for loc in root.findall(".//sm:loc", ns)
        if loc.text and "post-sitemap" in loc.text
    ]
    urls: list[str] = []
    for sitemap_url in sitemap_urls:
        sitemap = ET.fromstring(fetch_text(sitemap_url))
        urls.extend(
            loc.text
            for loc in sitemap.findall(".//sm:loc", ns)
            if loc.text
        )
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
    limit = None if args.all else args.limit
    catalog = combined_patch_catalog(limit)
    if args.all and not args.matched_only:
        validate_full_catalog(catalog, existing_count=count_patches())
    versions = [item["version"] for item in catalog]
    post_urls = hs_manacost_post_urls()
    stored: list[dict[str, str | None]] = []
    missing: list[str] = []
    for wiki_rank, catalog_item in enumerate(catalog):
        version = catalog_item["version"]
        official = {key: value for key, value in catalog_item.items() if key != "version"}
        source_url, hs_version = find_patch_url(post_urls, version)
        if source_url and hs_version:
            patch = build_patch(
                version,
                source_url,
                hs_version,
                wiki_rank=wiki_rank,
                official=official,
            )
        else:
            missing.append(version)
            if args.matched_only:
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
    deleted_stale = delete_patches_not_in(set(versions)) if args.all and not args.matched_only else 0
    print(
        json.dumps(
            {
                "ok": True,
                "versions_seen": len(versions),
                "stored_count": len(stored),
                "matched_count": len([item for item in stored if item.get("match_state") == "matched"]),
                "missing_manacost_count": len(missing),
                "deleted_stale_count": deleted_stale,
                "missing_manacost_versions": missing,
                "stored": stored,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
