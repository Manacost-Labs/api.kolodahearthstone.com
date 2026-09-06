import json
from email.message import Message
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest

from app import scrape_do_backend as backend
from app.browser_acquisition import BrowserAction, BrowserPlan


def response(monkeypatch, payload):
    class Response:
        status = 200
        headers = Message()
        headers["scrape.do-request-cost"] = "5"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return (
                payload.encode()
                if isinstance(payload, str)
                else json.dumps(payload).encode()
            )

    request = Mock(return_value=Response())
    monkeypatch.setattr(backend, "scrape_do_token", lambda: "fixture-token")
    monkeypatch.setattr(backend.urllib.request, "urlopen", request)
    return request


def test_click_and_wait_are_sent_and_results_checked(monkeypatch):
    request = response(
        monkeypatch,
        {
            "content": '<table id="rows"></table>',
            "actionResults": [{"success": True}, {"success": True}],
        },
    )
    plan = BrowserPlan(
        wait_selector="#rows",
        actions=(
            BrowserAction("Click", selector="#next"),
            BrowserAction("WaitSelector", selector="#rows", timeout_ms=1000),
        ),
    )
    result = backend.scrape_url_sync("https://example.com", browser_plan=plan)
    params = parse_qs(urlsplit(request.call_args.args[0].full_url).query)
    assert params["waitSelector"] == ["#rows"]
    assert params["returnJSON"] == ["true"]
    assert json.loads(params["playWithBrowser"][0]) == [
        {"Action": "Click", "Selector": "#next"},
        {"Action": "WaitSelector", "WaitSelector": "#rows", "Timeout": 1000},
    ]
    assert result.actions_completed == 2


@pytest.mark.parametrize(
    "results",
    [
        None,
        [],
        [{"success": False, "error": "secret"}],
        [{"success": "true"}],
        [{"success": True}, {"success": True}],
    ],
)
def test_http_200_does_not_hide_failed_actions(monkeypatch, results):
    response(monkeypatch, {"content": "<html>listing</html>", "actionResults": results})
    with pytest.raises(backend.ScrapeDoContentError) as error:
        backend.scrape_url_sync(
            "https://example.com",
            browser_plan=BrowserPlan(
                wait_selector="#rows",
                actions=(BrowserAction("Click", selector="#next"),),
            ),
        )
    assert error.value.scrape.request_cost == 5
    assert "secret" not in str(error.value)


def test_selector_missing_even_after_wait_rejects_billed_response(monkeypatch):
    response(monkeypatch, "<html>loading</html>")
    with pytest.raises(backend.ScrapeDoContentError, match="readiness"):
        backend.scrape_url_sync(
            "https://example.com", browser_plan=BrowserPlan(wait_selector="#rows")
        )


@pytest.mark.parametrize(
    "action",
    [
        lambda: BrowserAction("Execute", value="secret"),
        lambda: BrowserAction("Click", selector="["),
        lambda: BrowserAction("ScrollY", value=True),
        lambda: BrowserAction("WaitSelector", selector="#x", timeout_ms=-1),
    ],
)
def test_invalid_action_rejected(action):
    with pytest.raises(ValueError):
        action()


def test_browser_requires_render_before_network(monkeypatch):
    request = response(monkeypatch, "unused")
    with pytest.raises(ValueError):
        backend.scrape_url_sync(
            "https://example.com",
            render=False,
            browser_plan=BrowserPlan(wait_selector="#rows"),
        )
    request.assert_not_called()


def test_action_limit():
    with pytest.raises(ValueError):
        BrowserPlan(
            wait_selector="#rows", actions=(BrowserAction("Click", selector="#x"),) * 11
        )


@pytest.mark.parametrize(
    "action",
    [
        BrowserAction("Click", selector="#next"),
        BrowserAction("WaitSelector", selector="#rows", timeout_ms=1000),
    ],
)
def test_action_only_plan_requires_final_readiness_selector(action):
    with pytest.raises(ValueError, match="final readiness"):
        BrowserPlan(actions=(action,))


def test_successful_actions_still_require_final_table(monkeypatch):
    request = response(
        monkeypatch,
        {"content": "<html>loading</html>", "actionResults": [{"success": True}]},
    )
    plan = BrowserPlan(
        wait_selector="#rows",
        actions=(BrowserAction("WaitSelector", selector="#rows", timeout_ms=1000),),
    )
    with pytest.raises(backend.ScrapeDoContentError, match="readiness") as error:
        backend.scrape_url_sync("https://example.com", browser_plan=plan)
    assert request.call_count == 1
    assert error.value.scrape.request_cost == 5
