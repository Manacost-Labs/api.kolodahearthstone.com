"""Opt-in historical collection; never publishes into the latest-20 dataset.

WordPress paging contract:
https://developer.wordpress.org/rest-api/using-the-rest-api/pagination/
Requires the candidate ParsesUnix runner; the released dependency stays usable
for existing acquisition routes until a separately authorized core release.
"""

from importlib import import_module
from typing import Any

import httpx

from . import hearthstone_decks as decks


def _runner() -> Any:
    try:
        return import_module("web_scraper.pagination.runner")
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "ParsesUnix pagination runner is required for historical acquisition"
        ) from exc


async def collect_wordpress_history(
    client: httpx.AsyncClient,
    *,
    format_name: str,
    category_id: int,
    per_page: int = 100,
    limits: Any = None,
    resume: Any = None,
    checkpoint: Any = None,
) -> Any:
    """Collect a bounded chunk and return an explicit complete/incomplete result.

    Resume state/checkpoint storage are internal caller responsibilities. A
    logical request budget is not a Scrape.do credit budget. No hidden retries,
    redirects, provider fallback, scheduler activation or publication happen here.
    """
    engine = _runner()  # Capability check before I/O, including on the first page.
    if (format_name, category_id) not in decks._WORDPRESS_FORMATS or type(
        category_id
    ) is not int:
        raise ValueError("unsupported WordPress format/category")
    if type(per_page) is not int or not 1 <= per_page <= 100:
        raise ValueError("per_page must be between 1 and 100")
    scope_id = f"wordpress:hearthstone-decks:v1:{category_id}:{per_page}:id:asc"

    async def fetch_page(token: str) -> Any:
        if (
            not token.isascii()
            or not token.isdigit()
            or len(token) > 9
            or int(token) < 1
        ):
            raise ValueError("invalid page token")
        page_number = int(token)
        params = {
            "categories": category_id,
            "per_page": per_page,
            "page": page_number,
            "orderby": "id",
            "order": "asc",
            "_fields": decks._WORDPRESS_FIELDS,
        }
        response = await client.get(
            decks._WORDPRESS_API_URL,
            params=params,
            headers=decks._WORDPRESS_HEADERS,
            follow_redirects=False,
        )
        response.raise_for_status()
        expected_url = httpx.URL(decks._WORDPRESS_API_URL, params=params)
        if response.url != expected_url:
            raise ValueError("WordPress response scope mismatch")
        media_type = (
            response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        )
        if media_type != "application/json" and not media_type.endswith("+json"):
            raise ValueError("WordPress response is not JSON")
        totals = []
        for name in ("x-wp-total", "x-wp-totalpages"):
            value = response.headers.get(name, "")
            if not value.isascii() or not value.isdigit() or len(value) > 9:
                raise ValueError("missing or invalid WordPress coverage headers")
            totals.append(int(value))
        total, pages = totals
        if pages != (total + per_page - 1) // per_page or page_number > max(1, pages):
            raise ValueError("inconsistent WordPress coverage")
        expected_rows = min(per_page, max(0, total - (page_number - 1) * per_page))
        rows = decks._parse_wordpress_posts(
            response.json(),
            format_name=format_name,
            category_id=category_id,
            limit=expected_rows,
            minimum_codes=(expected_rows * 19 + 19) // 20,
        )
        exhausted = page_number >= pages
        return engine.Page(
            tuple(rows),
            next_token=None if exhausted else str(page_number + 1),
            exhausted=exhausted,
            expected_count=total,
        )

    return await engine.collect_pages(
        scope_id=scope_id,
        first_token="1",
        fetch_page=fetch_page,
        record_key=lambda row: str(row["wordpress_post_id"]),
        limits=limits if limits is not None else engine.Limits(),
        resume=resume,
        checkpoint=checkpoint,
    )
