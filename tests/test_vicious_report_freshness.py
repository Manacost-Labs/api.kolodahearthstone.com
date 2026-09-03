from __future__ import annotations

import pytest

from app.vicious_syndicate import parse_latest_report_metadata


def test_parse_latest_report_metadata_uses_highest_issue() -> None:
    html = """
    <article>
      <a href="https://www.vicioussyndicate.com/vs-data-reaper-report-351/">Report 351</a>
      <span class="entry-meta-date">June 4, 2026</span>
    </article>
    <article>
      <a href="//www.vicioussyndicate.com/vs-data-reaper-report-352/">Report 352</a>
      <span class="entry-meta-date">June 18, 2026</span>
    </article>
    """

    metadata = parse_latest_report_metadata(html)

    assert metadata == {
        "latest_report_issue": "352",
        "latest_report_url": "https://www.vicioussyndicate.com/vs-data-reaper-report-352/",
        "latest_report_published_at": "2026-06-18",
    }


def test_parse_latest_report_metadata_fails_closed_on_layout_breakage() -> None:
    with pytest.raises(RuntimeError, match="contained no Data Reaper reports"):
        parse_latest_report_metadata("<html><body>No reports here</body></html>")
