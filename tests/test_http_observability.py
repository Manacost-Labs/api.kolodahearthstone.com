from __future__ import annotations

import asyncio
import json
import re

import httpx
from fastapi import FastAPI, HTTPException
from starlette.testclient import TestClient

from app.http_observability import (
    RequestObservabilityMiddleware,
    current_request_id,
    generic_server_error,
    request_id_from_header,
)

UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
SECRET_QUERY = "token=super-secret"
SECRET_EMAIL = "qa@example.test"
SECRET_DECK_CODE = (
    "AAECAQcI"
    "+AeT0AOb"
    "2AP76APj"
    "6wO8igSI"
    "oASQtwQL"
    "ju0D1ASQ"
    "1ASc1ASm"
    "1ASv1ATB"
    "3gQA"
)


def _test_app(lines: list[str]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestObservabilityMiddleware, writer=lines.append)
    app.add_exception_handler(Exception, generic_server_error)

    @app.get("/ok/{item_id}")
    async def ok(item_id: str) -> dict[str, str | None]:
        before = current_request_id()
        await asyncio.sleep(0)
        return {"item": item_id, "before": before, "after": current_request_id()}

    @app.get("/bad/{item_id}")
    async def bad(item_id: str) -> None:
        raise HTTPException(status_code=422, detail=f"{item_id} {SECRET_EMAIL}")

    @app.get("/boom/{deck_code}")
    async def boom(deck_code: str) -> None:
        raise RuntimeError(
            f"failed deck={deck_code} email={SECRET_EMAIL} {SECRET_QUERY}"
        )

    return app


def _records(lines: list[str]) -> list[dict[str, object]]:
    return [json.loads(line) for line in lines]


def test_request_id_validator_accepts_only_bounded_log_safe_values() -> None:
    assert request_id_from_header("client-request-1") == "client-request-1"
    for invalid in ("invalid request id", "one,two", "has/slash", "x" * 129, ""):
        assert UUID_PATTERN.fullmatch(request_id_from_header(invalid))


def test_valid_invalid_oversize_and_duplicate_request_ids() -> None:
    async def scenario() -> None:
        lines: list[str] = []
        app = _test_app(lines)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            valid = await client.get("/ok/visible-id", headers={"X-Request-ID": "client-request-1"})
            invalid = await client.get("/ok/invalid", headers={"X-Request-ID": "invalid request id"})
            oversize = await client.get("/ok/oversize", headers={"X-Request-ID": "x" * 129})
            duplicate = await client.get(
                "/ok/duplicate",
                headers=[("X-Request-ID", "first-id"), ("X-Request-ID", "second-id")],
            )

        assert valid.headers["X-Request-ID"] == "client-request-1"
        assert valid.json()["before"] == "client-request-1"
        for response in (invalid, oversize, duplicate):
            generated = response.headers["X-Request-ID"]
            assert UUID_PATTERN.fullmatch(generated)
            assert response.json()["before"] == generated
            assert response.json()["after"] == generated
        assert duplicate.headers["X-Request-ID"] not in {"first-id", "second-id"}
        assert current_request_id() is None

    asyncio.run(scenario())


def test_context_isolated_across_concurrent_requests() -> None:
    async def scenario() -> None:
        lines: list[str] = []
        app = _test_app(lines)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            left, right = await asyncio.gather(
                client.get("/ok/left", headers={"X-Request-ID": "parallel-left"}),
                client.get("/ok/right", headers={"X-Request-ID": "parallel-right"}),
            )

        assert left.json()["before"] == left.json()["after"] == "parallel-left"
        assert right.json()["before"] == right.json()["after"] == "parallel-right"
        assert left.headers["X-Request-ID"] == "parallel-left"
        assert right.headers["X-Request-ID"] == "parallel-right"
        assert current_request_id() is None

    asyncio.run(scenario())


def test_application_cors_allows_and_exposes_request_id() -> None:
    from app.main import app

    client = TestClient(app)
    preflight = client.options(
        "/v1/system/sources",
        headers={
            "Origin": "https://api.kolodahearthstone.com",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "X-Request-ID",
            "X-Request-ID": "cors-preflight-request",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["X-Request-ID"] == "cors-preflight-request"
    assert "x-request-id" in preflight.headers["access-control-allow-headers"].lower()

    response = client.get(
        "/openapi.json",
        headers={
            "Origin": "https://api.kolodahearthstone.com",
            "X-Request-ID": "cors-visible-request",
        },
    )
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "cors-visible-request"
    assert "x-request-id" in response.headers["access-control-expose-headers"].lower()


def test_public_cache_does_not_replay_a_previous_request_id() -> None:
    from app.main import app

    client = TestClient(app)
    first = client.get(
        "/v1/system/sources",
        headers={"X-Request-ID": "cache-request-one"},
    )
    assert first.status_code == 200
    assert first.headers["X-Request-ID"] == "cache-request-one"
    assert first.headers.get("ETag")

    not_modified = client.get(
        "/v1/system/sources",
        headers={
            "X-Request-ID": "cache-request-two",
            "If-None-Match": first.headers["ETag"],
        },
    )
    assert not_modified.status_code == 304
    assert not_modified.content == b""
    assert not_modified.headers["X-Request-ID"] == "cache-request-two"


def test_2xx_4xx_5xx_logs_are_correlated_bounded_and_redacted() -> None:
    async def scenario() -> None:
        lines: list[str] = []
        app = _test_app(lines)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            ok = await client.get(
                f"/ok/{SECRET_DECK_CODE}?{SECRET_QUERY}&email={SECRET_EMAIL}",
                headers={
                    "Authorization": "Bearer super-secret",
                    "X-API-Key": "api-super-secret",
                    "X-Request-ID": "request-ok",
                },
            )
            bad = await client.get(
                f"/bad/{SECRET_DECK_CODE}?{SECRET_QUERY}",
                headers={"X-Request-ID": "request-bad"},
            )
            failed = await client.get(
                f"/boom/{SECRET_DECK_CODE}?{SECRET_QUERY}",
                headers={"X-Request-ID": "request-failed"},
            )
            missing = await client.get(
                f"/missing/{SECRET_DECK_CODE}?{SECRET_QUERY}",
                headers={"X-Request-ID": "request-missing"},
            )

        assert ok.status_code == 200
        assert bad.status_code == 422
        assert failed.status_code == 500
        assert failed.text == "Internal Server Error"
        assert missing.status_code == 404
        for response, request_id in (
            (ok, "request-ok"),
            (bad, "request-bad"),
            (failed, "request-failed"),
            (missing, "request-missing"),
        ):
            assert response.headers["X-Request-ID"] == request_id

        records = _records(lines)
        completions = [record for record in records if record["event"] == "http_request"]
        by_id = {record["requestId"]: record for record in completions}
        assert by_id["request-ok"]["status"] == 200
        assert by_id["request-ok"]["route"] == "/ok/{item_id}"
        assert by_id["request-bad"]["status"] == 422
        assert by_id["request-bad"]["route"] == "/bad/{item_id}"
        assert by_id["request-failed"]["status"] == 500
        assert by_id["request-failed"]["route"] == "/boom/{deck_code}"
        assert by_id["request-missing"]["status"] == 404
        assert by_id["request-missing"]["route"] == "/unmatched"

        error = next(
            record
            for record in records
            if record["event"] == "http_request_error"
            and record["requestId"] == "request-failed"
        )
        assert error["errorName"] == "RuntimeError"
        assert error["errorCode"] == "RuntimeError"

        serialized = "\n".join(lines)
        for forbidden in (
            SECRET_DECK_CODE,
            SECRET_QUERY,
            SECRET_EMAIL,
            "Bearer super-secret",
            "api-super-secret",
            "Authorization",
            "X-API-Key",
            "failed deck=",
        ):
            assert forbidden not in serialized

    asyncio.run(scenario())
