"""Explicit, bounded read-only acquisition interactions; no arbitrary JavaScript.

Provider contract: https://scrape.do/documentation/headless-browser/browser-interactions/
Plans are internal adapter configuration, never untrusted public API input.
"""

from dataclasses import dataclass

from bs4 import BeautifulSoup


def _selector(value: str | None) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("invalid browser selector")
    try:
        BeautifulSoup("", "html.parser").select_one(value)
    except Exception as exc:
        raise ValueError("invalid browser selector") from exc


@dataclass(frozen=True)
class BrowserAction:
    action: str
    selector: str | None = None
    value: str | int | None = None
    timeout_ms: int | None = None

    def __post_init__(self) -> None:
        if self.action not in {"Click", "Select", "ScrollY", "WaitSelector"}:
            raise ValueError("unsupported browser action")
        if self.action != "ScrollY":
            _selector(self.selector)
        elif self.selector is not None:
            raise ValueError("ScrollY does not accept a selector")
        if self.action == "ScrollY":
            if type(self.value) is not int or not 1 <= self.value <= 100_000:
                raise ValueError("invalid scroll distance")
        elif self.action == "Select":
            if not isinstance(self.value, str) or len(self.value) > 512:
                raise ValueError("invalid selection value")
        elif self.value is not None:
            raise ValueError("unexpected action value")
        if self.action == "WaitSelector":
            if type(self.timeout_ms) is not int or not 1 <= self.timeout_ms <= 10_000:
                raise ValueError("invalid action timeout")
        elif self.timeout_ms is not None:
            raise ValueError("unexpected action timeout")

    def provider_value(self) -> dict[str, str | int]:
        result: dict[str, str | int] = {"Action": self.action}
        if self.selector is not None:
            result["WaitSelector" if self.action == "WaitSelector" else "Selector"] = (
                self.selector
            )
        if self.value is not None:
            result["Value"] = self.value
        if self.timeout_ms is not None:
            result["Timeout"] = self.timeout_ms
        return result


@dataclass(frozen=True)
class BrowserPlan:
    wait_selector: str | None = None
    actions: tuple[BrowserAction, ...] = ()

    def __post_init__(self) -> None:
        if self.wait_selector is None:
            raise ValueError("browser plan requires a final readiness selector")
        _selector(self.wait_selector)
        if (
            not isinstance(self.actions, tuple)
            or len(self.actions) > 10
            or any(not isinstance(action, BrowserAction) for action in self.actions)
        ):
            raise ValueError("invalid browser actions")
        if sum(action.timeout_ms or 0 for action in self.actions) > 60_000:
            raise ValueError("browser action wait budget exceeded")

    def ready(self, html: str) -> bool:
        return (
            BeautifulSoup(html, "html.parser").select_one(self.wait_selector)
            is not None
        )
