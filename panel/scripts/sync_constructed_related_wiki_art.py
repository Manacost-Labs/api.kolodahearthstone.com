#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import subprocess
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from pathlib import Path
from typing import Any, Iterable

from sync_constructed_related_cards import connect_db, json_list, load_php_config, utc_now


APP_ROOT = Path(__file__).resolve().parents[1]
WIKI_API = "https://hearthstone.wiki.gg/api.php"
SCRAPE_DO_ENDPOINT = "https://api.scrape.do"
UPLOAD_DIR = APP_ROOT / "uploads" / "constructed-related-wiki-full-art"
UPLOAD_URL = "/uploads/constructed-related-wiki-full-art"
USER_AGENT = "db.kolodahs.ru-wiki-full-art-sync/1.0 (admin@kolodahs.ru)"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MEDIAWIKI_TITLE_BATCH_SIZE = 50
EXCLUDED_VARIANT_RE = re.compile(r"\b(?:signature|golden|diamond|premium|animated)\b", re.IGNORECASE)


class ScrapeDoFetchError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 0) -> None:
        super().__init__(message)
        self.status_code = status_code


def scrape_do_fetch(
    target_url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 180,
) -> bytes:
    token = str(os.environ.get("HS_SCRAPE_DO_TOKEN") or "").strip()
    if not token:
        raise ScrapeDoFetchError("Scrape.do token is not configured")
    query = {
        "token": token,
        "url": target_url,
        "render": "false",
        "retryTimeout": "30000",
    }
    request_headers: dict[str, str] = {}
    if headers:
        query["extraHeaders"] = "true"
        request_headers = {f"Sd-{name}": value for name, value in headers.items()}
    request = urllib.request.Request(
        SCRAPE_DO_ENDPOINT + "?" + urllib.parse.urlencode(query),
        headers=request_headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        raise ScrapeDoFetchError(
            f"Scrape.do HTTP {exc.code}",
            status_code=int(exc.code),
        ) from None
    except (URLError, TimeoutError):
        raise ScrapeDoFetchError("Scrape.do transport error") from None


def canonical_title(value: str) -> str:
    return " ".join(value.replace("_", " ").strip().split()).casefold()


def chunks(items: list[str], size: int) -> Iterable[list[str]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def wiki_api(params: dict[str, Any]) -> dict[str, Any]:
    query = {
        "format": "json",
        "formatversion": "2",
        "maxlag": "5",
        **params,
    }
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            body = scrape_do_fetch(
                WIKI_API + "?" + urllib.parse.urlencode(query),
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=90,
            )
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected MediaWiki API response")
            error = payload.get("error")
            if error:
                code = str(error.get("code") or "") if isinstance(error, dict) else ""
                if code == "maxlag":
                    raise RuntimeError(f"MediaWiki API maxlag: {error}")
                raise RuntimeError(f"MediaWiki API error: {error}")
            return payload
        except ScrapeDoFetchError as exc:
            last_error = exc
            if exc.status_code not in {0, 429, 500, 502, 503, 504}:
                raise
        except (json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if isinstance(exc, RuntimeError) and "maxlag" not in str(exc):
                raise
        if attempt < 4:
            time.sleep(min(16, 2**attempt))
    raise RuntimeError(f"MediaWiki API failed after retries: {last_error}")


def load_related_cards(
    conn,
    format_slug: str,
    parent_card_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    format_filter = ""
    parent_filter = ""
    params: list[str] = []
    if format_slug != "all":
        format_filter = "AND f.format_slug = %s"
        params.append(format_slug)
    if parent_card_id:
        parent_filter = "AND wm.card_id = %s"
        params.append(parent_card_id)
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT wm.related_cards_json
            FROM constructed_card_wiki_meta wm
            INNER JOIN constructed_format_cards f ON f.card_id = wm.card_id
            WHERE f.in_format = 1
              {format_filter}
              {parent_filter}
              AND wm.status = 'ok'
              AND wm.related_cards_json IS NOT NULL
            """,
            params,
        )
        rows = cursor.fetchall()

    related: dict[str, dict[str, Any]] = {}
    for row in rows:
        for group in json_list(row.get("related_cards_json")):
            if not isinstance(group, dict):
                continue
            for item in group.get("cards", []) or []:
                if not isinstance(item, dict):
                    continue
                card_id = str(item.get("card_id") or "").strip()
                page_title = str(item.get("title") or "").strip()
                page_url = str(item.get("url") or "").strip()
                if not card_id or not page_title:
                    continue
                related.setdefault(
                    card_id,
                    {
                        "card_id": card_id,
                        "page_title": page_title,
                        "page_url": page_url,
                    },
                )

    if not related:
        return {}
    card_ids = sorted(related)
    placeholders = ",".join(["%s"] * len(card_ids))
    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT card_id, name_en, wiki_full_art_sha1, local_wiki_full_art_url
            FROM constructed_cards
            WHERE card_id IN ({placeholders})
            """,
            card_ids,
        )
        stored = {str(row["card_id"]): row for row in cursor.fetchall()}
    for card_id in list(related):
        if card_id not in stored:
            del related[card_id]
            continue
        related[card_id].update(stored[card_id])
    return related


def resolve_alias(aliases: dict[str, str], title: str) -> str:
    current = canonical_title(title)
    seen = set()
    while current in aliases and aliases[current] != current and current not in seen:
        seen.add(current)
        current = aliases[current]
    return current


def fetch_page_images(page_titles: list[str]) -> tuple[dict[str, list[str]], dict[str, str]]:
    result: dict[str, list[str]] = {}
    aliases: dict[str, str] = {canonical_title(title): canonical_title(title) for title in page_titles}
    for batch in chunks(page_titles, MEDIAWIKI_TITLE_BATCH_SIZE):
        continuation: dict[str, Any] = {}
        while True:
            payload = wiki_api(
                {
                    "action": "query",
                    "prop": "images",
                    "titles": "|".join(batch),
                    "imlimit": "max",
                    "redirects": "1",
                    **continuation,
                }
            )
            query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
            for collection in ("normalized", "redirects"):
                for item in query.get(collection, []) or []:
                    source = canonical_title(str(item.get("from") or ""))
                    target = canonical_title(str(item.get("to") or ""))
                    if source and target:
                        aliases[source] = target
            for page in query.get("pages", []) or []:
                if not isinstance(page, dict) or page.get("missing"):
                    continue
                page_key = canonical_title(str(page.get("title") or ""))
                images = result.setdefault(page_key, [])
                for item in page.get("images", []) or []:
                    title = str(item.get("title") or "").strip()
                    if title and title not in images:
                        images.append(title)
            if not payload.get("continue"):
                break
            continuation = dict(payload["continue"])
    return result, aliases


def pick_full_art(page_title: str, images: list[str]) -> str | None:
    page_key = canonical_title(page_title)
    candidates: list[tuple[int, str]] = []
    for title in images:
        file_name = title.removeprefix("File:").strip()
        lower = file_name.casefold()
        suffix = Path(lower).suffix
        if suffix not in IMAGE_EXTENSIONS:
            continue
        stem = lower[: -len(suffix)]
        marker = next(
            (value for value in (" full", " art") if stem.endswith(value)),
            None,
        )
        if marker is None:
            continue
        if EXCLUDED_VARIANT_RE.search(stem):
            continue
        score = 0
        art_name = canonical_title(stem.removesuffix(marker))
        if art_name == page_key:
            score += 100
        elif page_key in art_name or art_name in page_key:
            score += 50
        if suffix in {".jpg", ".jpeg"}:
            score += 5
        candidates.append((score, title))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1].casefold()))
    return candidates[0][1]


def fetch_image_info(file_titles: list[str]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for batch in chunks(sorted(set(file_titles)), 40):
        payload = wiki_api(
            {
                "action": "query",
                "prop": "imageinfo",
                "titles": "|".join(batch),
                "iiprop": "url|size|mime|sha1|timestamp",
                "redirects": "1",
            }
        )
        query = payload.get("query") if isinstance(payload.get("query"), dict) else {}
        for page in query.get("pages", []) or []:
            image_info = page.get("imageinfo") if isinstance(page, dict) else None
            if not image_info:
                continue
            info = dict(image_info[0])
            info["title"] = str(page.get("title") or "")
            result[canonical_title(info["title"])] = info
    return result


def extension_for(info: dict[str, Any]) -> str:
    mime = str(info.get("mime") or "").lower()
    extension = mimetypes.guess_extension(mime) or Path(
        urllib.parse.urlparse(str(info.get("url") or "")).path
    ).suffix
    if extension == ".jpe":
        extension = ".jpg"
    if extension.lower() not in IMAGE_EXTENSIONS:
        raise RuntimeError(f"Unsupported Wiki full-art MIME type: {mime}")
    return extension.lower()


def safe_filename(card_id: str, extension: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", card_id).strip("._")
    if not safe:
        raise ValueError(f"Unsafe empty filename for card_id {card_id!r}")
    return safe + extension


def valid_image(content: bytes, mime: str) -> bool:
    mime = mime.lower()
    if mime == "image/jpeg":
        return content.startswith(b"\xff\xd8")
    if mime == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/webp":
        return len(content) >= 12 and content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    return False


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_original(
    card: dict[str, Any], info: dict[str, Any], dry_run: bool, skip_downloads: bool
) -> tuple[str, bool, dict[str, Any]]:
    extension = extension_for(info)
    filename = safe_filename(str(card["card_id"]), extension)
    destination = UPLOAD_DIR / filename
    local_url = f"{UPLOAD_URL}/{filename}"
    if not dry_run:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        UPLOAD_DIR.chmod(0o755)
    if destination.exists():
        destination.chmod(0o644)
        expected_sha1 = str(info.get("sha1") or "")
        stored_sha1 = str(card.get("wiki_full_art_sha1") or "")
        actual_size = destination.stat().st_size
        actual_sha1 = file_sha1(destination)
        if (
            (stored_sha1 and actual_sha1 == stored_sha1)
            or (
                actual_size == int(info.get("size") or 0)
                and (not expected_sha1 or actual_sha1 == expected_sha1)
            )
        ):
            local_info = dict(info)
            local_info["size"] = actual_size
            local_info["sha1"] = actual_sha1
            return local_url, False, local_info
    if dry_run or skip_downloads:
        return local_url, True, dict(info)

    # imageinfo.url already points at the immutable original file. Appending
    # download parameters can make the CDN return a recompressed derivative
    # whose size and SHA-1 no longer match MediaWiki metadata.
    original_url = str(info["url"])
    content = scrape_do_fetch(
        original_url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "image/*",
            "Accept-Encoding": "identity",
        },
        timeout=180,
    )
    expected_size = int(info.get("size") or 0)
    if expected_size and len(content) < expected_size * 0.5:
        raise RuntimeError(
            f"Wiki full-art payload is unexpectedly small for {card['card_id']}: "
            f"{len(content)} < {expected_size}"
        )
    mime = str(info.get("mime") or "")
    if not valid_image(content, mime):
        raise RuntimeError(f"Unexpected Wiki full-art payload for {card['card_id']} ({mime})")
    expected_sha1 = str(info.get("sha1") or "")
    actual_sha1 = hashlib.sha1(content).hexdigest()
    if expected_sha1 and len(content) == expected_size and actual_sha1 != expected_sha1:
        raise RuntimeError(
            f"Wiki full-art SHA-1 mismatch for {card['card_id']}: {actual_sha1} != {expected_sha1}"
        )
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.chmod(0o644)
    temporary.replace(destination)
    destination.chmod(0o644)
    local_info = dict(info)
    local_info["size"] = len(content)
    local_info["sha1"] = actual_sha1
    return local_url, True, local_info


def save_art(
    conn,
    card_id: str,
    file_title: str,
    info: dict[str, Any],
    local_url: str,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    with conn.cursor() as cursor:
        cursor.execute(
            """
            UPDATE constructed_cards
            SET wiki_full_art_title = %s,
                wiki_full_art_url = %s,
                local_wiki_full_art_url = %s,
                wiki_full_art_file_page_url = %s,
                wiki_full_art_width = %s,
                wiki_full_art_height = %s,
                wiki_full_art_size = %s,
                wiki_full_art_sha1 = %s,
                wiki_full_art_mime = %s,
                wiki_full_art_fetched_at = %s
            WHERE card_id = %s
            """,
            (
                file_title,
                info.get("url"),
                local_url,
                info.get("descriptionurl"),
                info.get("width"),
                info.get("height"),
                info.get("size"),
                info.get("sha1"),
                info.get("mime"),
                utc_now(),
                card_id,
            ),
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import original full-art files for related cards from hearthstone.wiki.gg."
    )
    parser.add_argument("--format", choices=["standard", "wild", "all"], default="all")
    parser.add_argument("--parent-card-id", help="Only import art for companions of one exact parent card ID.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--skip-downloads", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    args.parent_card_id = str(args.parent_card_id or "").strip() or None

    subprocess.check_call(["php", str(APP_ROOT / "scripts" / "ensure_constructed_schema.php")])
    conn = connect_db(load_php_config())
    try:
        cards = load_related_cards(conn, args.format, args.parent_card_id)
        if args.limit is not None:
            cards = dict(list(sorted(cards.items()))[: max(0, args.limit)])
        page_titles = sorted({str(card["page_title"]) for card in cards.values()})
        page_images, aliases = fetch_page_images(page_titles)

        selected: dict[str, str] = {}
        missing: list[str] = []
        for card_id, card in cards.items():
            resolved = resolve_alias(aliases, str(card["page_title"]))
            file_title = pick_full_art(str(card["page_title"]), page_images.get(resolved, []))
            if not file_title:
                missing.append(card_id)
                continue
            selected[card_id] = file_title

        image_info = fetch_image_info(list(selected.values()))
        stats: dict[str, Any] = {
            "format": args.format,
            "parent_card_id": args.parent_card_id,
            "requested": len(cards),
            "discovered": 0,
            "downloaded": 0,
            "already_local": 0,
            "missing": missing,
            "errors": [],
            "dry_run": args.dry_run,
        }
        for card_id, file_title in sorted(selected.items()):
            info = image_info.get(canonical_title(file_title))
            if not info:
                if card_id not in stats["missing"]:
                    stats["missing"].append(card_id)
                continue
            try:
                local_url, downloaded, local_info = download_original(
                    cards[card_id], info, args.dry_run, args.skip_downloads
                )
                save_art(
                    conn,
                    card_id,
                    file_title,
                    local_info,
                    local_url,
                    args.dry_run,
                )
                stats["discovered"] += 1
                stats["downloaded" if downloaded else "already_local"] += 1
            except Exception as exc:
                stats["errors"].append({"card_id": card_id, "error": str(exc)})

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
        print(json.dumps(stats, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return 0 if not stats["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
