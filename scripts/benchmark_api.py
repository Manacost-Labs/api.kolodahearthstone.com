#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

DEFAULT_PATHS = (
    "/health",
    "/v1/health",
    "/v1/sources",
    "/v1/battlegrounds/heroes?limit=50",
)


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    path: str
    requests: int
    failures: int
    status_counts: dict[str, int]
    response_bytes_avg: int
    latency_ms_min: float
    latency_ms_p50: float
    latency_ms_p95: float
    latency_ms_p99: float
    latency_ms_max: float


def percentile(samples: Sequence[float], percentile_value: float) -> float:
    if not samples:
        raise ValueError("at least one sample is required")
    if not 0 <= percentile_value <= 100:
        raise ValueError("percentile must be between 0 and 100")
    ordered = sorted(samples)
    position = (len(ordered) - 1) * percentile_value / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def validate_options(
    base_url: str,
    *,
    requests: int,
    concurrency: int,
    allow_remote: bool,
) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("base URL must be an absolute HTTP(S) URL")
    is_local = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if not is_local and not allow_remote:
        raise ValueError("remote targets require --allow-remote")
    request_cap = 2_000 if is_local else 100
    concurrency_cap = 100 if is_local else 20
    if not 1 <= requests <= request_cap:
        raise ValueError(f"requests must be between 1 and {request_cap}")
    if not 1 <= concurrency <= min(requests, concurrency_cap):
        raise ValueError(
            f"concurrency must be between 1 and {min(requests, concurrency_cap)}"
        )


async def run_scenario(
    client: httpx.AsyncClient,
    path: str,
    *,
    requests: int,
    concurrency: int,
) -> ScenarioResult:
    semaphore = asyncio.Semaphore(concurrency)

    async def sample() -> tuple[float, int | None, int]:
        async with semaphore:
            started = time.perf_counter_ns()
            try:
                response = await client.get(
                    path, headers={"Accept": "application/json"}
                )
                status = response.status_code
                response_bytes = len(response.content)
            except httpx.RequestError:
                status = None
                response_bytes = 0
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000
            return elapsed_ms, status, response_bytes

    samples = await asyncio.gather(*(sample() for _ in range(requests)))
    latencies = [sample[0] for sample in samples]
    statuses = Counter(
        "network_error" if sample[1] is None else str(sample[1]) for sample in samples
    )
    failures = sum(1 for _, status, _ in samples if status is None or status >= 400)
    response_bytes = [sample[2] for sample in samples]
    return ScenarioResult(
        path=path,
        requests=requests,
        failures=failures,
        status_counts=dict(sorted(statuses.items())),
        response_bytes_avg=round(sum(response_bytes) / len(response_bytes)),
        latency_ms_min=round(min(latencies), 2),
        latency_ms_p50=round(percentile(latencies, 50), 2),
        latency_ms_p95=round(percentile(latencies, 95), 2),
        latency_ms_p99=round(percentile(latencies, 99), 2),
        latency_ms_max=round(max(latencies), 2),
    )


async def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    timeout = httpx.Timeout(args.timeout)
    limits = httpx.Limits(
        max_connections=args.concurrency,
        max_keepalive_connections=args.concurrency,
    )
    async with httpx.AsyncClient(
        base_url=args.base_url.rstrip("/"),
        timeout=timeout,
        limits=limits,
        follow_redirects=False,
    ) as client:
        scenarios = [
            await run_scenario(
                client,
                path,
                requests=args.requests,
                concurrency=args.concurrency,
            )
            for path in args.path
        ]
    return {
        "generatedAt": datetime.now(UTC).isoformat(),
        "baseUrl": args.base_url.rstrip("/"),
        "requestsPerScenario": args.requests,
        "concurrency": args.concurrency,
        "scenarios": [asdict(scenario) for scenario in scenarios],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Measure public API latency safely.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--path", action="append", default=None)
    parser.add_argument("--requests", type=int, default=20)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--allow-remote", action="store_true")
    args = parser.parse_args()
    args.path = args.path or list(DEFAULT_PATHS)
    if not 0.1 <= args.timeout <= 60:
        parser.error("timeout must be between 0.1 and 60 seconds")
    try:
        validate_options(
            args.base_url,
            requests=args.requests,
            concurrency=args.concurrency,
            allow_remote=args.allow_remote,
        )
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main() -> int:
    report = asyncio.run(benchmark(parse_args()))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if any(item["failures"] for item in report["scenarios"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
