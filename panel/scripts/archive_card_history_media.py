#!/usr/bin/env python3
"""Archive observed card images without rewriting immutable version snapshots.

Plan-only by default. Historical mutable URLs remain explicitly unverified.
--apply captures local bytes; --remote also permits bounded public image fetches.
"""

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from backfill_constructed_images import connect_db
from PIL import Image

PUBLIC_HOSTS = frozenset({"api.kolodahearthstone.com"})
REMOTE_HOSTS = frozenset(
    {"art.hearthstonejson.com", "d15f34w2p8l1cc.cloudfront.net", "hearthstone.wiki.gg"}
)
FIELDS = frozenset(
    {
        "animated_image_url",
        "art_image",
        "art_image_url",
        "card_image",
        "card_image_url",
        "crop_image_url",
        "framed_image",
        "golden_image",
        "golden_image_url",
        "hero_image_url",
        "image_diamond_url",
        "image_gold_url",
        "image_signature_url",
        "image_url",
        "local_crop_image_url",
        "local_gold_image_url",
        "local_image_url",
        "static_image_url",
        "wiki_image_url",
        "horizontal_image_url",
        "tile_image_url",
    }
)
MAX_BYTES = 20 * 1024 * 1024
FORMATS = {
    "PNG": ("png", "image/png"),
    "JPEG": ("jpg", "image/jpeg"),
    "WEBP": ("webp", "image/webp"),
    "GIF": ("gif", "image/gif"),
}
DDL = """CREATE TABLE IF NOT EXISTS catalog_card_version_media (
    version_id BIGINT UNSIGNED NOT NULL,
    field_name VARCHAR(64) NOT NULL,
    source_url TEXT NOT NULL,
    status VARCHAR(24) NOT NULL,
    reason VARCHAR(64) DEFAULT NULL,
    archive_url VARCHAR(255) DEFAULT NULL,
    sha256 CHAR(64) DEFAULT NULL,
    byte_size BIGINT UNSIGNED DEFAULT NULL,
    mime_type VARCHAR(64) DEFAULT NULL,
    evidence VARCHAR(40) DEFAULT NULL,
    captured_at DATETIME(6) NOT NULL,
    PRIMARY KEY(version_id, field_name),
    KEY version_media_status(status, captured_at),
    FOREIGN KEY(version_id) REFERENCES catalog_card_versions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"""


class ArchiveError(ValueError):
    """A bounded reason safe for logs and API output."""


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.username or parsed.password or parsed.fragment:
        raise ArchiveError("unsupported_url")
    if parsed.scheme and (parsed.scheme != "https" or parsed.port not in (None, 443)):
        raise ArchiveError("unsupported_url")
    if parsed.netloc and parsed.hostname not in PUBLIC_HOSTS | REMOTE_HOSTS:
        raise ArchiveError("unapproved_host")
    if not parsed.netloc and (
        parsed.scheme or not parsed.path.startswith(("/uploads/", "/assets/"))
    ):
        raise ArchiveError("unsupported_url")
    return parsed


def local_file(url, root):
    parsed = validate_url(url)
    if parsed.netloc and parsed.hostname not in PUBLIC_HOSTS:
        return None
    path = urllib.parse.unquote(parsed.path)
    if not path.startswith(("/uploads/", "/assets/")):
        raise ArchiveError("unsupported_local_path")
    root = Path(root)
    base = (root / path.lstrip("/").split("/")[0]).resolve()
    candidate = (root / path.lstrip("/")).resolve()
    if not candidate.is_relative_to(base):
        raise ArchiveError("invalid_local_path")
    return candidate


class PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        parsed = validate_url(newurl)
        if parsed.hostname not in REMOTE_HOSTS:
            raise ArchiveError("unapproved_redirect")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch_bytes(url, root, remote=False):
    path = local_file(url, root)
    if path is not None:
        with path.open("rb") as source:
            content = source.read(MAX_BYTES + 1)
    else:
        if not remote:
            raise ArchiveError("remote_capture_disabled")
        # Do not inherit authenticated proxy settings from the parser environment.
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), PublicRedirects()
        )
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; KolodaCardHistory/1.0)",
                "Accept": "image/*",
            },
        )
        with opener.open(request, timeout=15) as response:
            content = response.read(MAX_BYTES + 1)
    if not content or len(content) > MAX_BYTES:
        raise ArchiveError("invalid_image_size")
    try:
        with Image.open(io.BytesIO(content)) as image:
            if image.format not in FORMATS or image.width * image.height > 20_000_000:
                raise ArchiveError("unsupported_image")
            extension, mime = FORMATS[image.format]
            image.verify()
    except ArchiveError:
        raise
    except Exception as cause:
        raise ArchiveError("invalid_image") from cause
    return content, extension, mime


def immutable_digest(url):
    match = re.search(
        r"-([a-f0-9]{64})\.(png|jpg|webp|gif)$", urllib.parse.urlsplit(url).path
    )
    return match[1] if match else None


def save_bytes(content, extension, root):
    digest = hashlib.sha256(content).hexdigest()
    directory = Path(root) / "uploads/card-history-media"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (digest + "." + extension)
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ArchiveError("archive_integrity_error")
    else:
        descriptor, temporary = tempfile.mkstemp(prefix=".capture-", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o644)
            # Hard link publishes without overwriting any existing content-addressed file.
            try:
                os.link(temporary, target)
            except FileExistsError:
                if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                    raise ArchiveError("archive_integrity_error")
        finally:
            os.unlink(temporary)
    return "/uploads/card-history-media/" + target.name, digest


def capture(url, latest, root, remote=False):
    expected = immutable_digest(url)
    if not latest and not expected:
        return {"status": "unverified", "reason": "historical_mutable_url"}
    try:
        content, extension, mime = fetch_bytes(url, root, remote)
        digest = hashlib.sha256(content).hexdigest()
        if expected and expected != digest:
            raise ArchiveError("source_digest_mismatch")
        archive, digest = save_bytes(content, extension, root)
        return {
            "status": "archived",
            "archive_url": archive,
            "sha256": digest,
            "byte_size": len(content),
            "mime_type": mime,
            "evidence": "immutable_source_hash"
            if expected
            else "captured_after_observation",
        }
    except ArchiveError as cause:
        return {
            "status": "unverified"
            if str(cause) == "remote_capture_disabled"
            else "failed",
            "reason": str(cause),
        }
    except (OSError, urllib.error.URLError, ValueError):
        return {"status": "failed", "reason": "image_unavailable"}


def archive_batch(
    connection,
    root,
    after=0,
    limit=100,
    apply=False,
    remote=False,
    retry=False,
    newest=False,
):
    summary = {
        "versions": 0,
        "images": 0,
        "archived": 0,
        "failed": 0,
        "unverified": 0,
        "planned": 0,
        "already_archived": 0,
        "last_id": after,
    }
    with connection.cursor() as cursor:
        selector = "v.id>%s"
        if retry:
            selector += (
                " AND EXISTS (SELECT 1 FROM catalog_card_version_media m WHERE m.version_id=v.id "
                "AND (m.status='failed' OR m.reason='remote_capture_disabled') "
                "AND m.captured_at < UTC_TIMESTAMP() - INTERVAL 6 HOUR)"
            )
        if newest:
            selector += (
                " AND v.id=(SELECT MAX(w.id) FROM catalog_card_versions w WHERE w.entity=v.entity AND w.card_id=v.card_id) "
                "AND NOT EXISTS (SELECT 1 FROM catalog_card_version_media m WHERE m.version_id=v.id AND m.field_name='_scan')"
            )
        cursor.execute(
            "SELECT v.id, v.entity, v.card_id, v.snapshot_json, "
            "v.id=(SELECT MAX(w.id) FROM catalog_card_versions w WHERE w.entity=v.entity AND w.card_id=v.card_id) AS latest "
            "FROM catalog_card_versions v WHERE "
            + selector
            + " ORDER BY v.id "
            + ("DESC" if newest else "ASC")
            + " LIMIT %s",
            (after, limit),
        )
        versions = cursor.fetchall()
    connection.rollback()  # release the read snapshot before filesystem/network work
    for version in versions:
        snapshot = json.loads(version["snapshot_json"])
        summary["versions"] += 1
        summary["last_id"] = version["id"]
        for field in sorted(FIELDS & snapshot.keys()):
            url = snapshot[field]
            if not isinstance(url, str) or not url:
                continue
            summary["images"] += 1
            if not apply:
                summary["planned"] += 1
                continue
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status FROM catalog_card_version_media WHERE version_id=%s AND field_name=%s",
                    (version["id"], field),
                )
                existing = cursor.fetchone()
            connection.rollback()
            if existing and existing["status"] == "archived":
                summary["already_archived"] += 1
                continue
            result = capture(url, bool(version["latest"]), root, remote)
            with connection.cursor() as cursor:
                if result["status"] == "archived" and not immutable_digest(url):
                    cursor.execute(
                        "SELECT id AS latest FROM catalog_card_versions WHERE entity=%s AND card_id=%s ORDER BY id DESC LIMIT 1 FOR UPDATE",
                        (version["entity"], version["card_id"]),
                    )
                    if cursor.fetchone()["latest"] != version["id"]:
                        result = {
                            "status": "unverified",
                            "reason": "version_changed_during_capture",
                        }
                fields = (
                    "status",
                    "reason",
                    "archive_url",
                    "sha256",
                    "byte_size",
                    "mime_type",
                    "evidence",
                )
                cursor.execute(
                    "INSERT INTO catalog_card_version_media "
                    "(version_id,field_name,source_url,status,reason,archive_url,sha256,byte_size,mime_type,evidence,captured_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE "
                    + ",".join(
                        name
                        + "=IF(status='archived',"
                        + name
                        + ",VALUES("
                        + name
                        + "))"
                        for name in fields
                        if name != "status"
                    )
                    + ",captured_at=IF(status='archived',captured_at,VALUES(captured_at))"
                    + ",status=IF(status='archived',status,VALUES(status))",
                    (
                        version["id"],
                        field,
                        url,
                        *(result.get(name) for name in fields),
                        datetime.now(timezone.utc).replace(tzinfo=None),
                    ),
                )
            connection.commit()
            summary[result["status"]] += 1
        if apply:
            with connection.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO catalog_card_version_media (version_id,field_name,source_url,status,captured_at) "
                    "VALUES (%s,'_scan','','checked',UTC_TIMESTAMP(6)) ON DUPLICATE KEY UPDATE captured_at=VALUES(captured_at)",
                    (version["id"],),
                )
            connection.commit()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--install", action="store_true")
    parser.add_argument("--remote", action="store_true")
    parser.add_argument(
        "--newest",
        action="store_true",
        help="Prioritize current versions without an image scan",
    )
    parser.add_argument(
        "--retry", action="store_true", help="Retry failed captures after six hours"
    )
    parser.add_argument(
        "--state", type=Path, help="Locked durable cursor for continuous capture"
    )
    parser.add_argument("--after", type=int, default=0)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--root", default="/srv/api-kolodahearthstone/panel/current")
    args = parser.parse_args()
    if args.after < 0 or not 1 <= args.limit <= 500:
        parser.error("after must be nonnegative and limit must be 1..500")
    if args.install and not args.apply:
        parser.error("--install requires --apply")
    if args.state and (not args.apply or args.retry or args.newest):
        parser.error(
            "--state requires --apply and cannot be combined with --retry/--newest"
        )
    lock = None
    if args.state:
        args.state.parent.mkdir(parents=True, exist_ok=True)
        lock = args.state.with_suffix(".lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "already_running"}))
            return
        if args.state.exists():
            state = json.loads(args.state.read_text())
            args.after = int(state["last_id"])
            if args.after < 0:
                raise ValueError("Invalid archive cursor")
    code = "echo json_encode(require '/etc/api-kolodahearthstone/panel-config.php');"
    connection = connect_db(
        json.loads(subprocess.check_output(["php", "-r", code], text=True))
    )
    try:
        if args.install:
            with connection.cursor() as cursor:
                cursor.execute(DDL)
            connection.commit()
        result = archive_batch(
            connection,
            args.root,
            args.after,
            args.limit,
            args.apply,
            args.remote,
            args.retry,
            args.newest,
        )
        if args.state:
            descriptor, temporary = tempfile.mkstemp(
                prefix=".history-cursor-", dir=args.state.parent
            )
            try:
                with os.fdopen(descriptor, "w") as output:
                    json.dump({"last_id": result["last_id"]}, output)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, args.state)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        print(json.dumps(result))
    finally:
        connection.close()
        if lock:
            lock.close()


if __name__ == "__main__":
    main()
