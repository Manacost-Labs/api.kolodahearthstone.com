"""Small, opt-in SQLite queue for persisted acquisition detail work."""

from __future__ import annotations

import ipaddress
import json
import math
import re
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

_TABLE = "acquisition_detail_queue"
_META = "acquisition_detail_queue_meta"
_ERROR = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_FINAL = {"succeeded", "absent", "retry", "failed"}
_ALLOWED_TABLES = {_TABLE, _META, "sqlite_sequence"}


@dataclass(frozen=True)
class Claim:
    scope_id: str
    entity_id: str
    url: str
    lease_token: str
    attempts: int


class DetailQueue:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time):
        self.path, self.clock = Path(path), clock
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("pragma busy_timeout = 5000")
        return c

    def _initialize(self) -> None:
        c = self._connect()
        try:
            c.execute("begin immediate")
            names = {
                r[0]
                for r in c.execute(
                    "select name from sqlite_master where type = 'table'"
                )
            }
            if names - _ALLOWED_TABLES:
                raise ValueError("queue database contains foreign tables")
            c.execute(f"create table if not exists {_META} (version integer not null)")
            versions = list(c.execute(f"select version from {_META}"))
            if not versions:
                c.execute(f"insert into {_META} values (1)")
            elif len(versions) != 1 or versions[0][0] != 1:
                raise ValueError("unsupported queue database version")
            c.execute(f"""create table if not exists {_TABLE} (
              scope_id text not null, entity_id text not null, url text not null, max_attempts integer not null,
              status text not null, attempts integer not null default 0, payload text, error_code text,
              credits_spent integer not null default 0, unknown_cost_attempts integer not null default 0,
              available_at real not null default 0, lease_token text, lease_until real,
              primary key (scope_id, entity_id))""")
            c.execute("commit")
        except Exception:
            if c.in_transaction:
                c.execute("rollback")
            raise
        finally:
            c.close()

    @staticmethod
    def _text(value: object, label: str) -> str:
        if not isinstance(value, str) or not value or len(value) > 256:
            raise ValueError(f"invalid {label}")
        return value

    @staticmethod
    def _attempts(value: object) -> int:
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 1 <= value <= 10
        ):
            raise ValueError("max_attempts must be between 1 and 10")
        return value

    @staticmethod
    def _url(value: object) -> str:
        if not isinstance(value, str) or not value or len(value) > 2048:
            raise ValueError("invalid url")
        try:
            p = urlsplit(value)
            host = p.hostname
            if (
                p.scheme != "https"
                or not p.netloc
                or p.username
                or p.password
                or p.fragment
                or not host
            ):
                raise ValueError
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                if host.lower() == "localhost":
                    raise ValueError
            else:
                if not address.is_global:
                    raise ValueError
            _ = p.port
        except ValueError as e:
            raise ValueError(
                "url must be public https without userinfo or fragment"
            ) from e
        return value

    def enqueue(
        self, scope_id: str, items: Iterable[tuple[str, str]], *, max_attempts: int = 3
    ) -> int:
        scope_id, max_attempts = (
            self._text(scope_id, "scope_id"),
            self._attempts(max_attempts),
        )
        raw = list(islice(items, 10_001))
        if len(raw) > 10_000:
            raise ValueError("batch exceeds 10000 items")
        rows, seen = [], set()
        for item in raw:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("items must be entity_id, url pairs")
            entity_id, url = self._text(item[0], "entity_id"), self._url(item[1])
            if entity_id in seen:
                raise ValueError("duplicate entity_id in batch")
            seen.add(entity_id)
            rows.append((entity_id, url))
        c = self._connect()
        try:
            c.execute("begin immediate")
            added = 0
            for entity_id, url in rows:
                old = c.execute(
                    f"select url,max_attempts from {_TABLE} where scope_id=? and entity_id=?",
                    (scope_id, entity_id),
                ).fetchone()
                if old:
                    if old["url"] != url or old["max_attempts"] != max_attempts:
                        raise ValueError(
                            "entity_id already has different url or policy"
                        )
                    continue
                c.execute(
                    f"insert into {_TABLE}(scope_id,entity_id,url,max_attempts,status) values(?,?,?,?, 'pending')",
                    (scope_id, entity_id, url, max_attempts),
                )
                added += 1
            c.execute("commit")
            return added
        except Exception:
            c.execute("rollback")
            raise
        finally:
            c.close()

    @staticmethod
    def _lease(value: object) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 0 < value <= 3600
        ):
            raise ValueError("invalid lease_seconds")
        return float(value)

    def claim(self, scope_id: str, *, lease_seconds: float = 120) -> Claim | None:
        scope_id, lease_seconds, now = (
            self._text(scope_id, "scope_id"),
            self._lease(lease_seconds),
            self.clock(),
        )
        c = self._connect()
        try:
            c.execute("begin immediate")
            c.execute(
                f"""update {_TABLE} set status=case when attempts>=max_attempts then 'failed' else 'retry' end,
              available_at=?,lease_token=null,lease_until=null,unknown_cost_attempts=unknown_cost_attempts+1
              where scope_id=? and status='running' and lease_until <= ?""",
                (now, scope_id, now),
            )
            row = c.execute(
                f"select entity_id,url,attempts from {_TABLE} where scope_id=? and status in ('pending','retry') and available_at<=? order by entity_id limit 1",
                (scope_id, now),
            ).fetchone()
            if not row:
                c.execute("commit")
                return None
            token = str(uuid4())
            attempts = row["attempts"] + 1
            c.execute(
                f"update {_TABLE} set status='running',attempts=?,lease_token=?,lease_until=? where scope_id=? and entity_id=?",
                (attempts, token, now + lease_seconds, scope_id, row["entity_id"]),
            )
            c.execute("commit")
            return Claim(scope_id, row["entity_id"], row["url"], token, attempts)
        except Exception:
            c.execute("rollback")
            raise
        finally:
            c.close()

    @staticmethod
    def _error(value: str | None, required: bool = False) -> str | None:
        if value is None and not required:
            return None
        if not isinstance(value, str) or not _ERROR.fullmatch(value):
            raise ValueError("invalid error_code")
        return value

    def finish(
        self,
        claim: Claim,
        *,
        status: str,
        payload: dict | None = None,
        error_code: str | None = None,
        retry_after: float = 60,
        request_cost: int | None = None,
    ) -> None:
        if not isinstance(claim, Claim) or status not in _FINAL:
            raise ValueError("invalid claim or completion status")
        if (
            isinstance(retry_after, bool)
            or not isinstance(retry_after, (int, float))
            or not math.isfinite(retry_after)
            or not 0 <= retry_after <= 86400
        ):
            raise ValueError("invalid retry_after")
        if (
            isinstance(request_cost, bool)
            or request_cost is not None
            and (not isinstance(request_cost, int) or request_cost < 0)
        ):
            raise ValueError("invalid request_cost")
        encoded = None
        if payload is not None:
            if not isinstance(payload, dict):
                raise ValueError("payload must be a dict")
            try:
                encoded = json.dumps(
                    payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False
                )
            except (TypeError, ValueError) as e:
                raise ValueError("payload must be JSON") from e
            if len(encoded.encode()) > 256 * 1024:
                raise ValueError("payload exceeds 256KiB")
        if status == "succeeded" and encoded is None:
            raise ValueError("succeeded requires payload")
        error_code = self._error(error_code, status == "absent")
        now = self.clock()
        c = self._connect()
        try:
            c.execute("begin immediate")
            row = c.execute(
                f"select attempts,max_attempts from {_TABLE} where scope_id=? and entity_id=? and status='running' and lease_token=? and lease_until>?",
                (claim.scope_id, claim.entity_id, claim.lease_token, now),
            ).fetchone()
            if not row:
                raise ValueError("claim is no longer active")
            final = (
                "failed"
                if status == "retry" and row["attempts"] >= row["max_attempts"]
                else status
            )
            c.execute(
                f"""update {_TABLE} set status=?,payload=?,error_code=?,available_at=?,lease_token=null,lease_until=null,
              credits_spent=credits_spent+?,unknown_cost_attempts=unknown_cost_attempts+? where scope_id=? and entity_id=?""",
                (
                    final,
                    encoded,
                    error_code,
                    now + float(retry_after) if final == "retry" else now,
                    request_cost or 0,
                    int(request_cost is None),
                    claim.scope_id,
                    claim.entity_id,
                ),
            )
            c.execute("commit")
        except Exception:
            c.execute("rollback")
            raise
        finally:
            c.close()

    def items(self, scope_id: str) -> list[dict]:
        c = self._connect()
        try:
            rows = c.execute(
                f"select entity_id,url,status,attempts,payload,error_code,credits_spent,unknown_cost_attempts from {_TABLE} where scope_id=? order by entity_id",
                (self._text(scope_id, "scope_id"),),
            )
            return [
                {
                    **dict(r),
                    "payload": json.loads(r["payload"]) if r["payload"] else None,
                }
                for r in rows
            ]
        finally:
            c.close()
