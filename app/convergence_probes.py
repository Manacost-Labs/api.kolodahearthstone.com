"""Free, bounded upstream probes used by convergence recovery chains."""

from __future__ import annotations

from .convergence_store import ConvergenceClaim
from .convergence_worker import RecoveryExecution
from .vicious_syndicate import probe_known_pending_publication

_VICIOUS_RADAR_SOURCE_ID = "vicious_syndicate_radars"


async def execute_upstream_probe(claim: ConvergenceClaim) -> RecoveryExecution:
    if claim.chain.action != "probe_upstream":
        raise ValueError("Convergence claim is not an upstream probe")
    if claim.chain.source_ids != (_VICIOUS_RADAR_SOURCE_ID,):
        raise ValueError("Upstream probe does not support this source set")

    probe = await probe_known_pending_publication(_VICIOUS_RADAR_SOURCE_ID)
    if probe.state == "pending":
        outcome, reason_code, upstream_pending = "skipped", "unavailable", True
    elif probe.state == "ready":
        # The cheap probe proves only that a full refresh is now worthwhile.
        outcome, reason_code, upstream_pending = "failed", "transport", False
    else:
        outcome, reason_code, upstream_pending = "skipped", "unavailable", False
    return RecoveryExecution(
        parser_run_id=None,
        results=(
            {
                "sourceId": _VICIOUS_RADAR_SOURCE_ID,
                "outcome": outcome,
                "reasonCode": reason_code,
                "upstreamPending": upstream_pending,
            },
        ),
    )
