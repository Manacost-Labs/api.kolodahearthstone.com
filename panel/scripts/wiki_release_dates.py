from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Iterable


WIKI_API_URL = "https://hearthstone.wiki.gg/api.php"


def fetch_release_dates(
    dbfs: Iterable[int | str | None],
    *,
    user_agent: str,
    chunk_size: int = 100,
) -> dict[int, str]:
    """Return the earliest Wiki `added` patch date for each Hearthstone DBF."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")

    normalized_dbfs = sorted(
        {
            int(dbf)
            for dbf in dbfs
            if dbf is not None and str(dbf).isdigit() and int(dbf) > 0
        }
    )
    release_dates: dict[int, str] = {}
    for offset in range(0, len(normalized_dbfs), chunk_size):
        chunk = normalized_dbfs[offset : offset + chunk_size]
        params = {
            "action": "cargoquery",
            "format": "json",
            "tables": "CardChange,CustomPatches",
            "join_on": "CardChange.build=CustomPatches.build",
            "fields": "CardChange.dbfId=Dbf,MIN(CustomPatches.releaseDate)=ReleaseDate",
            "where": (
                f"CardChange.dbfId IN ({','.join(map(str, chunk))}) "
                "AND CardChange.type='added' "
                "AND CustomPatches.releaseDate IS NOT NULL"
            ),
            "group_by": "CardChange.dbfId",
            "order_by": "CardChange.dbfId ASC",
            "limit": str(chunk_size),
        }
        request = urllib.request.Request(
            WIKI_API_URL + "?" + urllib.parse.urlencode(params),
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=80) as response:
            payload = json.loads(response.read())
        if "error" in payload:
            raise RuntimeError(payload["error"].get("info", "Wiki Cargo release date query failed"))

        for item in payload.get("cargoquery", []):
            row = item.get("title") if isinstance(item, dict) else None
            if not isinstance(row, dict) or not str(row.get("Dbf") or "").isdigit():
                continue
            match = re.match(r"^(\d{4}-\d{2}-\d{2})", str(row.get("ReleaseDate") or ""))
            if match:
                dbf = int(row["Dbf"])
                date = match.group(1)
                release_dates[dbf] = min(release_dates.get(dbf, date), date)

    return release_dates
