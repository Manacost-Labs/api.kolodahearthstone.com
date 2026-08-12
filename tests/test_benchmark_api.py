from __future__ import annotations

import pytest

from scripts.benchmark_api import percentile, validate_options


def test_percentile_interpolates_sorted_samples() -> None:
    samples = [50.0, 10.0, 40.0, 20.0, 30.0]

    assert percentile(samples, 0) == 10.0
    assert percentile(samples, 50) == 30.0
    assert percentile(samples, 95) == pytest.approx(48.0)
    assert percentile(samples, 100) == 50.0


def test_remote_benchmark_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="--allow-remote"):
        validate_options(
            "https://api.kolodahearthstone.com",
            requests=10,
            concurrency=2,
            allow_remote=False,
        )


def test_remote_benchmark_has_conservative_caps() -> None:
    with pytest.raises(ValueError, match="requests must be between 1 and 100"):
        validate_options(
            "https://api.kolodahearthstone.com",
            requests=101,
            concurrency=2,
            allow_remote=True,
        )
    with pytest.raises(ValueError, match="concurrency must be between 1 and 20"):
        validate_options(
            "https://api.kolodahearthstone.com",
            requests=100,
            concurrency=21,
            allow_remote=True,
        )


def test_local_benchmark_allows_larger_controlled_runs() -> None:
    validate_options(
        "http://127.0.0.1:8000",
        requests=2_000,
        concurrency=100,
        allow_remote=False,
    )
