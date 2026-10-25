from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from app.balance_radar import store
from app.balance_radar.clients import GetXApiClient, RedditApisClient
from app.balance_radar.pipeline import run_balance_radar
from app.balance_radar.schema import RadarResult
from app.main import app
from tests.balance_radar_fixtures import CARDS_EN, loader

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "balance_radar"
NOW = datetime(2026, 10, 25, 10, 0, tzinfo=UTC)
PATCH = {
    "version": "37.0.0.254477",
    "title": "37.0 Patch Notes",
    "url": "https://example.test/37",
    "publishedAt": "2026-10-12T16:55:00+00:00",
}


def fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def social_clients() -> tuple[RedditApisClient, GetXApiClient]:
    search = fixture("reddit_search.json")
    tweets = fixture("x_search.json")

    def reddit_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/account"):
            return httpx.Response(
                200, json={"credits_remaining": 9.0, "total_requests": 10}
            )
        if "/comments" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "post": {},
                    "comments": [
                        {
                            "kind": "t1",
                            "data": {
                                "id": "cmt1",
                                "author": "c",
                                "body": "Zoo Warlock must be nerfed",
                                "score": 40,
                                "permalink": "/r/hearthstone/comments/x/y/cmt1/",
                                "created_utc": 1792900000,
                                "replies": "",
                            },
                        }
                    ],
                },
            )
        return httpx.Response(200, json=search)

    def x_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/account"):
            return httpx.Response(200, json={"balance_total": 0.4, "total_requests": 5})
        return httpx.Response(200, json=tweets)

    no_sleep = {"sleep": lambda _s: None}
    return (
        RedditApisClient(
            "k", transport=httpx.MockTransport(reddit_handler), **no_sleep
        ),
        GetXApiClient("k", transport=httpx.MockTransport(x_handler), **no_sleep),
    )


def ritual_post_id() -> str:
    post = next(
        p
        for p in fixture("reddit_search.json")["posts"]
        if p["title"].startswith("Nerf Ritual")
    )
    return f"reddit:{post['id']}"


class FakeClaude:
    def __init__(self, *, invent: bool = False) -> None:
        self.calls = 0
        self.invent = invent
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.stream))

    def stream(self, **request: Any) -> Any:
        self.calls += 1
        model_input = json.loads(request["messages"][0]["content"])
        assert model_input["format"] == "standard"
        assert {p["id"] for p in model_input["social"]["posts"]} >= {ritual_post_id()}
        nerfs = [
            {
                "ref": "card:BE_RIT",
                "confidence": 78,
                "headline": "Ритуал призыва перегружен",
                "verdict": "55,9% при 18,8% популярности.",
                "reasons": ["винрейт", "популярность"],
                "suggested_change": "Стоимость 2 → 3",
                "sentiment": "negative",
                "community_stance": "wants_nerf",
                "social_summary": "Просят нерф.",
                "evidence_post_ids": [ritual_post_id(), "x:made-up"],
                "precedent_ids": [],
                "key_numbers": ["55,9% при 18,8%"],
                "predicted_changes": [
                    {
                        "change": "Стоимость 2 → 3",
                        "field": "cost",
                        "before": 2,
                        "after": 3,
                        "probability": 60,
                    }
                ],
                "meta_impact": "Zoolock ослабнет.",
                "quotes": [],
                "developer_signal": "",
            },
            {
                "ref": "archetype:standard:Zoolock",
                "confidence": 55,
                "headline": "Zoolock",
                "verdict": "63% винрейта.",
                "reasons": ["винрейт"],
                "suggested_change": "Через карты",
                "sentiment": "negative",
                "community_stance": "wants_nerf",
                "social_summary": "",
                "evidence_post_ids": [],
                "precedent_ids": [],
                "key_numbers": ["55,9% при 18,8%"],
                "predicted_changes": [
                    {
                        "change": "Стоимость 2 → 3",
                        "field": "cost",
                        "before": 2,
                        "after": 3,
                        "probability": 60,
                    }
                ],
                "meta_impact": "Zoolock ослабнет.",
                "quotes": [],
                "developer_signal": "",
            },
        ]
        if self.invent:
            nerfs.append({**nerfs[0], "ref": "card:DOES_NOT_EXIST"})
        output = {
            "summary": "Чернокнижник доминирует.",
            "timing_outlook": "Патч вероятен через неделю.",
            "nerfs": nerfs,
            "buffs": [
                {
                    "ref": "class:standard:DRUID",
                    "confidence": 45,
                    "headline": "Друид слаб",
                    "verdict": "44% винрейта.",
                    "reasons": ["низкий винрейт"],
                    "suggested_change": "Бафф новых карт",
                    "sentiment": "no_data",
                    "community_stance": "no_data",
                    "social_summary": "",
                    "evidence_post_ids": [],
                    "precedent_ids": [],
                    "key_numbers": ["55,9% при 18,8%"],
                    "predicted_changes": [
                        {
                            "change": "Стоимость 2 → 3",
                            "field": "cost",
                            "before": 2,
                            "after": 3,
                            "probability": 60,
                        }
                    ],
                    "meta_impact": "Zoolock ослабнет.",
                    "quotes": [],
                    "developer_signal": "",
                }
            ],
        }
        message = SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text", text=json.dumps(output, ensure_ascii=False)
                )
            ],
            stop_reason="end_turn",
            model="claude-sonnet-5-5",
            usage=SimpleNamespace(
                input_tokens=50_000,
                output_tokens=8_000,
                cache_read_input_tokens=0,
                cache_creation_input_tokens=4_000,
                iterations=[],
            ),
        )

        class Stream:
            def __enter__(self) -> Any:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def get_final_message(self) -> Any:
                return message

        return Stream()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HS_API_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("RADAR_ANALYSIS_MODEL", raising=False)
    return tmp_path


def run(claude: FakeClaude, **kwargs: Any):
    reddit, x = social_clients()
    return run_balance_radar(
        ("standard",),
        reddit=reddit,
        x=x,
        claude=claude,
        loader=loader,
        cards_en=CARDS_EN,
        patch=PATCH,
        now=NOW,
        **{
            "refresh_history": False,
            "translate_names": False,
            "translate": False,
            **kwargs,
        },
    )


def test_full_run_stores_a_contract_valid_result_with_real_post_evidence(
    data_dir,
) -> None:
    claude = FakeClaude(invent=True)
    summary = run(claude)

    assert summary.exit_code() == 0
    outcome = summary.outcomes[0]
    assert (outcome.status, outcome.nerfs, outcome.buffs) == ("ok", 2, 1)
    latest = store.latest_ok("standard")
    payload = latest["payload"]
    RadarResult.model_validate(payload)

    ritual = payload["nerfs"][0]
    assert ritual["ref"] == "card:BE_RIT" and ritual["confidence"] == 78
    # Numbers come from the dataset, not from the model.
    assert ritual["stats"]["winrate"] == 55.9 and ritual["stats"]["popularity"] == 18.8
    assert ritual["name"] == "Ритуал призыва" and ritual["class"] == "WARLOCK"
    top = ritual["social"]["topPosts"]
    assert top[0]["id"] == ritual_post_id() and top[0]["citedByModel"] is True
    assert top[0]["url"].startswith("https://") and top[0]["author"]
    assert ritual["social"]["mentions"]["reddit"] >= 1
    assert all(post["id"] != "x:made-up" for post in top)
    assert ritual["dates"]["firstFlaggedAt"] == payload["generatedAt"]
    assert ritual["dates"]["previousConfidence"] is None

    zoolock = payload["nerfs"][1]
    assert zoolock["stats"]["period"] == "black_empire_ban"
    assert zoolock["social"]["mentions"]["reddit"] >= 1  # the "Zoo Warlock" comment

    assert (
        payload["model"] == "claude-sonnet-5-5"
        and payload["requestedModel"] == "claude-sonnet-5-5"
    )
    assert payload["patch"]["version"] == "37.0.0.254477"
    assert "card:DOES_NOT_EXIST" in " ".join(payload["usage"]["droppedModelItems"])
    usage = payload["usage"]
    assert (
        usage["analysis"]["costUsd"] == 0.19
    )  # 50k × $2 + 8k × $10 + 4k × $2.50 per MTok
    assert usage["social"]["redditapis"]["billableRequests"] > 0
    assert usage["totalCostUsd"] > usage["analysis"]["costUsd"]
    assert {s["kind"] for s in payload["sources"]} == {"stats", "social"}
    assert summary.balances["getxapi"]["balanceUsd"] == 0.4


def test_unchanged_inputs_skip_the_paid_analysis_and_history_keeps_the_streak(
    data_dir,
) -> None:
    claude = FakeClaude()
    run(claude)
    first_generated_at = store.latest_ok("standard")["payload"]["generatedAt"]
    second = run(claude)
    assert claude.calls == 1
    assert second.outcomes[0].status == "skipped"

    third = run(claude, force=True)
    assert claude.calls == 2 and third.outcomes[0].status == "ok"
    rows, total = store.list_runs(format_name="standard")
    assert total == 3 and [row["status"] for row in rows].count("ok") == 2
    ritual = store.latest_ok("standard")["payload"]["nerfs"][0]
    assert ritual["dates"]["previousConfidence"] == 78
    assert ritual["dates"]["firstFlaggedAt"] == first_generated_at


def test_failed_analysis_is_recorded_and_previous_result_keeps_serving(
    data_dir,
) -> None:
    run(FakeClaude())

    class Broken(FakeClaude):
        def stream(self, **request: Any) -> Any:
            stream = super().stream(**request)
            message = stream.get_final_message()
            message.content[0].text = '{"summary": "x"}'
            return stream

    summary = run(Broken(), force=True)
    assert summary.outcomes[0].status == "failed" and summary.exit_code() == 1
    rows, total = store.list_runs(format_name="standard")
    assert total == 2 and rows[0]["status"] == "failed" and "schema" in rows[0]["error"]
    assert store.latest_ok("standard")["payload"]["nerfs"]


def test_rest_endpoints_serve_latest_history_and_one_run(data_dir) -> None:
    client = TestClient(app)
    empty = client.get("/v1/constructed/balance-radar")
    assert empty.status_code == 503

    run(FakeClaude())
    latest = client.get("/v1/constructed/balance-radar", params={"format": "standard"})
    assert latest.status_code == 200
    body = latest.json()
    assert body["meta"]["source_id"] == "balance_radar" and body["meta"]["count"] == 3
    assert (
        body["data"]["format"] == "standard"
        and body["data"]["nerfs"][0]["ref"] == "card:BE_RIT"
    )
    assert latest.headers.get("etag")

    assert (
        client.get(
            "/v1/constructed/balance-radar", params={"format": "wild"}
        ).status_code
        == 503
    )
    assert (
        client.get(
            "/v1/constructed/balance-radar", params={"format": "arena"}
        ).status_code
        == 422
    )

    runs = client.get(
        "/v1/constructed/balance-radar/runs", params={"format": "standard"}
    ).json()
    assert runs["meta"]["count"] == 1 and "inputHash" not in runs["data"][0]
    run_id = runs["data"][0]["runId"]
    one = client.get(f"/v1/constructed/balance-radar/runs/{run_id}").json()
    assert one["data"]["runId"] == run_id and one["run"]["status"] == "ok"
    assert (
        client.get("/v1/constructed/balance-radar/runs/unknown-run-id").status_code
        == 404
    )
    assert client.get("/v1/constructed/balance-radar/runs/bad id!").status_code in {
        404,
        422,
    }


def test_documented_example_matches_the_contract() -> None:
    example = json.loads(
        (ROOT / "docs" / "examples" / "balance-radar-standard.json").read_text(
            encoding="utf-8"
        )
    )
    RadarResult.model_validate(example["data"])
    assert example["meta"]["source_id"] == "balance_radar"


def test_timer_units_follow_the_docker_job_conventions() -> None:
    service = (ROOT / "systemd" / "hs-data-api-docker-balance-radar.service").read_text(
        encoding="utf-8"
    )
    timer = (ROOT / "systemd" / "hs-data-api-docker-balance-radar.timer").read_text(
        encoding="utf-8"
    )
    assert "python -m app.balance_radar run" in service
    assert "SuccessExitStatus=10" in service and "Type=oneshot" in service
    assert "OnCalendar=" in timer and "Persistent=true" in timer
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert (
        "${HS_RADAR_ENV_FILE:-/home/debian/server/secrets/hs-data-api/radar.env}"
        in compose
    )


HISTORY = {
    "generatedAt": "2026-10-25T00:00:00+00:00",
    "coverage": {"from": "2023-12-08", "statsFrom": "2026-04-23"},
    "patterns": {
        "balancePatches": 57,
        "preStats": {"nerf": {"winrate": {"median": 52.5}}},
    },
    "narrative": {
        "summary": "Blizzard нерфит популярные колоды.",
        "patterns": [{"title": "Порог", "detail": "52,5%"}],
    },
    "contentDrops": [],
    "patches": [
        {
            "version": "36.2.2.249896",
            "date": "2026-08-18",
            "officialUrl": "https://news.example/36.2.2",
            "summary": "Ослабили Troublemaker Rogue.",
            "counts": {"nerf": 2},
            "daysSinceContentDrop": 15,
            "changes": [
                {
                    "id": "36.2.2.249896:BE_RIT",
                    "cardId": "BE_RIT",
                    "dbfId": 900001,
                    "name": "Ритуал призыва",
                    "nameEn": "Ritual of Summoning",
                    "class": "WARLOCK",
                    "direction": "nerf",
                    "fields": [{"field": "cost", "before": 1, "after": 2}],
                    "setAgeDays": 15,
                    "archetypes": [
                        {
                            "archetype": "Ritual Warlock",
                            "before": {"winrate": 56.0, "popularity": 15.0},
                            "after": {"winrate": 52.0, "popularity": 9.0},
                        }
                    ],
                },
                {
                    "id": "36.2.2.249896:OTHER",
                    "cardId": "OTHER",
                    "name": "Другая",
                    "nameEn": "Other",
                    "class": "ROGUE",
                    "direction": "nerf",
                    "fields": [{"field": "attack", "before": 3, "after": 2}],
                    "archetypes": [
                        {
                            "archetype": "Zoolock",
                            "before": {"winrate": 61.0, "popularity": 2.0},
                        }
                    ],
                },
            ],
        }
    ],
}


class CitingClaude(FakeClaude):
    def stream(self, **request: Any) -> Any:
        model_input = json.loads(request["messages"][0]["content"])
        history = model_input["history"]
        assert history["patterns"]["summary"] == "Blizzard нерфит популярные колоды."
        assert (
            history["precedents"]["card:BE_RIT"][0]["id"]
            == "precedent:36.2.2.249896:BE_RIT"
        )
        assert "same_card" in history["precedents"]["card:BE_RIT"][0]["similarity"]
        stream = super().stream(**request)
        message = stream.get_final_message()
        output = json.loads(message.content[0].text)
        for item in output["nerfs"] + output["buffs"]:
            item["precedent_ids"] = (
                ["precedent:36.2.2.249896:BE_RIT", "precedent:made-up"]
                if item["ref"] == "card:BE_RIT"
                else []
            )
        message.content[0].text = json.dumps(output, ensure_ascii=False)
        return stream


def test_past_balance_changes_reach_the_prompt_and_the_candidates(data_dir) -> None:
    summary = run(CitingClaude(), balance_history=HISTORY)
    assert summary.outcomes[0].status == "ok"
    payload = store.latest_ok("standard")["payload"]
    RadarResult.model_validate(payload)

    ritual = payload["nerfs"][0]
    first = ritual["precedents"][0]
    assert (
        first["id"] == "precedent:36.2.2.249896:BE_RIT"
        and first["citedByModel"] is True
    )
    assert first["changes"] == [{"field": "cost", "before": 1, "after": 2}]
    assert first["preStats"] == {
        "archetype": "Ritual Warlock",
        "winrate": 56.0,
        "popularity": 15.0,
    }
    assert first["postStats"]["winrate"] == 52.0
    assert first["url"] == "https://news.example/36.2.2"
    assert (
        "nerfs:card:BE_RIT:precedent:precedent:made-up"
        in payload["usage"]["droppedModelItems"]
    )
    # Uncited, but similar: Zoolock gets the 61% / Zoolock-core precedent filled in.
    zoolock = payload["nerfs"][1]
    assert zoolock["precedents"] and zoolock["precedents"][0]["citedByModel"] is False
    assert "same_archetype" in zoolock["precedents"][0]["similarity"]
    assert payload["history"]["balancePatches"] == 57
    assert payload["history"]["patternsUrl"] == "/v1/constructed/balance-radar/patterns"
