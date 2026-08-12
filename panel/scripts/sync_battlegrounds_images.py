#!/opt/wiki-hs-parser/.venv/bin/python
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import email.utils
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backfill_constructed_images import connect_db, load_php_config


APP_ROOT = Path(os.environ.get("KOLODAHS_APP_ROOT") or Path(__file__).resolve().parents[1])
STATE_FILE = APP_ROOT / "var" / "sync" / "battlegrounds-image-state.json"
LOCK_FILE = APP_ROOT / "var" / "sync" / "battlegrounds-images.lock"
HSJ_BGS_RENDER_BASE = "https://art.hearthstonejson.com/v1/bgs/latest/ruRU/512x/"
HEARTHPWN_GOLDEN_BASE = "https://cards.hearthpwn.com/enUS/"
USER_AGENT = "db.kolodahs.ru-bg-image-sync/1.0"
SAFE_CARD_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "assets": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "assets": {}}
    if not isinstance(data, dict) or not isinstance(data.get("assets"), dict):
        return {"version": 1, "assets": {}}
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    state["version"] = 1
    state["updated_at"] = utc_now()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o644)
    tmp.replace(path)


def active_cards(conn, limit: int | None) -> list[dict[str, Any]]:
    sql = """
        SELECT card_id, dbf, card_type, card_image, golden_image
        FROM battlegrounds_cards
        WHERE in_pool = 1
        ORDER BY card_id ASC
    """
    params: list[Any] = []
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def local_path(public_path: str, expected_dir: str) -> Path:
    prefix = f"/uploads/{expected_dir}/"
    if not public_path.startswith(prefix):
        raise ValueError(f"Unexpected local image path: {public_path}")
    path = (APP_ROOT / public_path.lstrip("/")).resolve()
    allowed = (APP_ROOT / "uploads" / expected_dir).resolve()
    if path.parent != allowed:
        raise ValueError(f"Image path escapes {allowed}: {public_path}")
    return path


def request_with_retries(request: urllib.request.Request, attempts: int = 3):
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            return urllib.request.urlopen(request, timeout=45)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def remote_metadata(asset: dict[str, Any]) -> dict[str, Any]:
    candidates = asset.get("source_candidates") or [
        {"url": asset["source_url"], "kind": asset.get("source_kind")}
    ]
    errors = []
    for candidate in candidates:
        source_url = str(candidate["url"])
        request = urllib.request.Request(
            source_url,
            method="HEAD",
            headers={"User-Agent": USER_AGENT, "Accept": "image/png,image/*;q=0.8"},
        )
        try:
            with request_with_retries(request) as response:
                if response.status < 200 or response.status >= 300:
                    raise RuntimeError(f"HTTP {response.status}")
                last_modified = response.headers.get("Last-Modified")
                remote_timestamp = None
                if last_modified:
                    remote_timestamp = email.utils.parsedate_to_datetime(last_modified).timestamp()
                return {
                    **asset,
                    "source_url": source_url,
                    "source_kind": candidate.get("kind"),
                    "etag": (response.headers.get("ETag") or "").strip(),
                    "last_modified": last_modified or "",
                    "remote_timestamp": remote_timestamp,
                    "content_length": response.headers.get("Content-Length") or "",
                }
        except Exception as exc:
            errors.append(f"{candidate.get('kind') or 'source'}: {exc}")
    target = Path(str(asset["target"]))
    if target.is_file() and target.stat().st_size > 0:
        # Some retired legacy cards no longer exist at any upstream source.
        # Their last validated local render is preferable to failing the whole
        # scheduled refresh or deleting a still usable image.
        return {**asset, "preserved_local": True, "preserve_reason": "; ".join(errors)}
    return {**asset, "error": "; ".join(errors)}


def signature(metadata: dict[str, Any]) -> dict[str, str]:
    return {
        "source_url": str(metadata["source_url"]),
        "etag": str(metadata.get("etag") or ""),
        "last_modified": str(metadata.get("last_modified") or ""),
        "content_length": str(metadata.get("content_length") or ""),
    }


def refresh_reason(metadata: dict[str, Any], previous: dict[str, Any] | None, force: bool) -> str | None:
    target = Path(metadata["target"])
    if force:
        return "forced"
    if not target.is_file() or target.stat().st_size <= 0:
        return "missing_local"
    current_signature = signature(metadata)
    if previous:
        previous_signature = {key: str(previous.get(key) or "") for key in current_signature}
        return "remote_changed" if current_signature != previous_signature else None
    remote_timestamp = metadata.get("remote_timestamp")
    if remote_timestamp is None:
        return "bootstrap_without_timestamp"
    return "remote_newer" if float(remote_timestamp) > target.stat().st_mtime + 1 else None


def download(url: str, target: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "image/png,image/*;q=0.8"},
    )
    with request_with_retries(request) as response, target.open("wb") as handle:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"HTTP {response.status}")
        while chunk := response.read(1024 * 1024):
            handle.write(chunk)


def identify_png(path: Path, expected_size: tuple[int, int]) -> None:
    result = subprocess.run(
        ["identify", "-format", "%m %w %h", str(path)],
        check=True,
        capture_output=True,
        text=True,
    )
    expected = f"PNG {expected_size[0]} {expected_size[1]}"
    if result.stdout.strip() != expected:
        raise RuntimeError(f"Unexpected image geometry: {result.stdout.strip()} (expected {expected})")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def replace_normal_card(metadata: dict[str, Any]) -> str:
    target = Path(metadata["target"])
    target.parent.mkdir(parents=True, exist_ok=True)
    source_fd, source_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".source", dir=target.parent)
    output_fd, output_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".png", dir=target.parent)
    os.close(source_fd)
    os.close(output_fd)
    source = Path(source_name)
    output = Path(output_name)
    try:
        download(str(metadata["source_url"]), source)
        subprocess.run(
            ["convert", str(source), "-resize", "256x388!", str(output)],
            check=True,
            capture_output=True,
            text=True,
        )
        identify_png(output, (256, 388))
        os.chmod(output, 0o644)
        os.replace(output, target)
        return sha256_file(target)
    finally:
        source.unlink(missing_ok=True)
        output.unlink(missing_ok=True)


def replace_golden_card(metadata: dict[str, Any]) -> str:
    target = Path(metadata["target"])
    target.parent.mkdir(parents=True, exist_ok=True)
    source_fd, source_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".source", dir=target.parent)
    output_fd, output_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".png", dir=target.parent)
    os.close(source_fd)
    os.close(output_fd)
    source = Path(source_name)
    output = Path(output_name)
    try:
        download(str(metadata["source_url"]), source)
        subprocess.run(
            ["convert", str(source), "-resize", "512x776!", str(output)],
            check=True,
            capture_output=True,
            text=True,
        )
        identify_png(output, (512, 776))
        os.chmod(output, 0o644)
        os.replace(output, target)
        return sha256_file(target)
    finally:
        source.unlink(missing_ok=True)
        output.unlink(missing_ok=True)


def normal_assets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assets = []
    for row in rows:
        card_id = str(row["card_id"])
        public_path = str(row.get("card_image") or "")
        if not public_path.startswith("/uploads/cards/"):
            continue
        assets.append(
            {
                "key": f"normal:{card_id}",
                "card_id": card_id,
                "kind": "normal",
                "source_url": HSJ_BGS_RENDER_BASE + urllib.parse.quote(card_id) + ".png",
                "target": str(local_path(public_path, "cards")),
            }
        )
    return assets


def fetch_blizzard_golden_urls() -> tuple[dict[int, str], str | None]:
    client_id = os.environ.get("BLIZZARD_CLIENT_ID", "")
    client_secret = os.environ.get("BLIZZARD_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        return {}, "missing_credentials"

    token_request = urllib.request.Request(
        "https://oauth.battle.net/token",
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode(),
        headers={
            "Authorization": "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode(),
            "User-Agent": USER_AGENT,
        },
    )
    with request_with_retries(token_request) as response:
        token = json.load(response).get("access_token")
    if not token:
        raise RuntimeError("Blizzard token response does not contain access_token")

    region = os.environ.get("BLIZZARD_REGION", "us")
    locale = os.environ.get("BLIZZARD_LOCALE", "ru_RU")
    page = 1
    urls: dict[int, str] = {}
    while True:
        query = urllib.parse.urlencode(
            {"locale": locale, "gameMode": "battlegrounds", "pageSize": 500, "page": page}
        )
        request = urllib.request.Request(
            f"https://{region}.api.blizzard.com/hearthstone/cards?{query}",
            headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
        )
        with request_with_retries(request) as response:
            payload = json.load(response)
        for card in payload.get("cards", []):
            if not isinstance(card, dict):
                continue
            dbf = int(card.get("id") or 0)
            battlegrounds = card.get("battlegrounds") if isinstance(card.get("battlegrounds"), dict) else {}
            image_gold = battlegrounds.get("imageGold") or card.get("imageGold")
            if dbf and image_gold:
                urls[dbf] = str(image_gold)
        if page >= int(payload.get("pageCount") or page):
            break
        page += 1
    return urls, None


def golden_assets(rows: list[dict[str, Any]], urls: dict[int, str]) -> list[dict[str, Any]]:
    assets = []
    for row in rows:
        if str(row.get("card_type") or "") != "minion":
            continue
        card_id = str(row["card_id"])
        if not SAFE_CARD_ID_RE.fullmatch(card_id):
            continue
        # Golden variants are separate rows in HearthstoneJSON. The public
        # golden toggle belongs to the base card, so avoid constructing invalid
        # names such as BG36_921_G_G_triple.png for variant rows.
        if card_id.endswith("_G") or card_id.endswith("_Gt"):
            continue
        public_path = str(row.get("golden_image") or "")
        blizzard_url = urls.get(int(row.get("dbf") or 0))
        has_local_path = public_path.startswith("/uploads/golden/")

        if not has_local_path:
            public_path = f"/uploads/golden/{card_id}.png"

        # HearthstoneJSON publishes the localized, full-size Battlegrounds
        # triple render under this stable path. Prefer it so new patch cards do
        # not inherit an English image from Blizzard's slower collection feed.
        # The other sources remain ordered fallbacks for propagation gaps.
        hsj_url = HSJ_BGS_RENDER_BASE + urllib.parse.quote(card_id) + "_G_triple.png"
        candidates = [{"url": hsj_url, "kind": "hearthstonejson_ruRU_triple"}]
        if blizzard_url:
            candidates.append({"url": blizzard_url, "kind": "blizzard_fallback"})
        candidates.append(
            {
                "url": HEARTHPWN_GOLDEN_BASE + urllib.parse.quote(card_id + "_G") + ".png",
                "kind": "hearthpwn_fallback",
            }
        )
        assets.append(
            {
                "key": f"golden:{card_id}",
                "card_id": card_id,
                "kind": "golden",
                "source_url": hsj_url,
                "source_kind": "hearthstonejson_ruRU_triple",
                "source_candidates": candidates,
                "public_path": public_path,
                "target": str(local_path(public_path, "golden")),
            }
        )
    return assets


def persist_golden_paths(conn, assets: list[dict[str, Any]]) -> int:
    """Publish refreshed golden files and advance their public cache version."""
    ready = [asset for asset in assets if Path(asset["target"]).is_file() and Path(asset["target"]).stat().st_size > 0]
    if not ready:
        return 0
    updated = 0
    with conn.cursor() as cur:
        for asset in ready:
            updated += cur.execute(
                """
                UPDATE battlegrounds_cards
                SET golden_image = %s,
                    updated_at = CURRENT_TIMESTAMP
                WHERE card_id = %s
                """,
                (asset["public_path"], asset["card_id"]),
            )
    conn.commit()
    return updated


def sync_assets(
    assets: list[dict[str, Any]],
    state: dict[str, Any],
    dry_run: bool,
    force: bool,
    workers: int,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "checked": len(assets),
        "refreshed": 0,
        "unchanged": 0,
        "preserved": 0,
        "errors": [],
        # Kept internal so callers can update cache-busting timestamps only
        # for files that were actually replaced during this run.
        "_refreshed_assets": [],
    }
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        checked = list(executor.map(remote_metadata, assets))

    for metadata in checked:
        key = str(metadata["key"])
        if metadata.get("preserved_local"):
            stats["unchanged"] += 1
            stats["preserved"] += 1
            continue
        if metadata.get("error"):
            stats["errors"].append({"card_id": metadata["card_id"], "kind": metadata["kind"], "error": metadata["error"]})
            continue
        previous = state["assets"].get(key)
        reason = refresh_reason(metadata, previous if isinstance(previous, dict) else None, force)
        entry = {**signature(metadata), "checked_at": utc_now()}
        if reason is None:
            stats["unchanged"] += 1
            entry["local_sha256"] = sha256_file(Path(metadata["target"]))
            state["assets"][key] = entry
            continue
        if dry_run:
            print(json.dumps({"card_id": metadata["card_id"], "kind": metadata["kind"], "action": "would_refresh", "reason": reason}, ensure_ascii=False))
            stats["refreshed"] += 1
            continue
        try:
            if metadata["kind"] == "normal":
                entry["local_sha256"] = replace_normal_card(metadata)
            elif metadata["kind"] == "golden":
                entry["local_sha256"] = replace_golden_card(metadata)
            else:
                raise RuntimeError(f"Unsupported asset kind: {metadata['kind']}")
            entry["refreshed_at"] = utc_now()
            entry["reason"] = reason
            state["assets"][key] = entry
            stats["refreshed"] += 1
            stats["_refreshed_assets"].append(metadata)
            print(json.dumps({"card_id": metadata["card_id"], "kind": metadata["kind"], "action": "refreshed", "reason": reason}, ensure_ascii=False))
        except Exception as exc:
            stats["errors"].append({"card_id": metadata["card_id"], "kind": metadata["kind"], "error": str(exc)})
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh locally cached Battlegrounds card images when their remote assets change.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--state-file", type=Path, default=STATE_FILE)
    args = parser.parse_args()

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as lock_handle:
        try:
            fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "skipped_locked"}))
            return 0

        conn = connect_db(load_php_config())
        try:
            rows = active_cards(conn, args.limit)

            state = load_state(args.state_file)
            normal_stats = sync_assets(normal_assets(rows), state, args.dry_run, args.force, args.workers)
            golden_asset_list: list[dict[str, Any]] = []
            try:
                golden_urls, golden_skip_reason = fetch_blizzard_golden_urls()
                golden_asset_list = golden_assets(rows, golden_urls)
                golden_stats = sync_assets(golden_asset_list, state, args.dry_run, args.force, args.workers)
            except Exception as exc:
                golden_urls = {}
                golden_skip_reason = None
                golden_stats = {"checked": 0, "refreshed": 0, "unchanged": 0, "errors": [{"kind": "golden", "error": str(exc)}]}
            refreshed_golden_assets = golden_stats.pop("_refreshed_assets", [])
            normal_stats.pop("_refreshed_assets", None)
            persisted_golden = 0 if args.dry_run else persist_golden_paths(conn, refreshed_golden_assets)
            if not args.dry_run:
                save_state(args.state_file, state)
            errors = list(normal_stats["errors"]) + list(golden_stats["errors"])
            summary = {
                "status": "ok" if not errors else "error",
                "dry_run": args.dry_run,
                "normal": normal_stats,
                "golden": golden_stats,
                "golden_paths_persisted": persisted_golden,
                "golden_source": {
                    "available": len(golden_urls),
                    "fallbacks": sum(asset.get("source_kind") == "hearthpwn_fallback" for asset in golden_asset_list),
                    "skipped": golden_skip_reason,
                },
            }
            print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
            return 0 if not errors else 1
        finally:
            conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
