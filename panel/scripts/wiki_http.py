"""Bounded retries for free Wiki requests; paid escalation is not implicit."""
from __future__ import annotations

from email.utils import parsedate_to_datetime
import time
from urllib.error import HTTPError
from urllib.request import urlopen


def fetch_bytes(request, timeout=80, *, opener=None, sleep=None, now=None):
    opener, sleep, now = opener or urlopen, sleep or time.sleep, now or time.time
    for attempt in range(3):
        try:
            with opener(request, timeout=timeout) as response:
                return response.read()
        except HTTPError as exc:
            if exc.code not in (429, 502, 503, 504) or attempt == 2:
                raise
            value = (exc.headers or {}).get("Retry-After", "")
            delay = 2 ** (attempt + 1)
            try:
                delay = max(0, float(value))
            except ValueError:
                try:
                    delay = max(0, parsedate_to_datetime(value).timestamp() - now())
                except (ValueError, TypeError, OverflowError):
                    pass
            # A long cooldown must be retried by the scheduler, not ignored
            # or shortened into further rate-limit requests.
            if delay > 60:
                raise
            exc.close()
            sleep(max(1, delay))
    raise AssertionError("unreachable")
