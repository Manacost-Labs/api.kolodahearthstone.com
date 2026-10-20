from __future__ import annotations

from dataclasses import dataclass

from .budget import allowed
from .models import Settings, digest


@dataclass(frozen=True)
class Job:
    kind: str
    format: str
    rank: str
    period: str = "past_week"
    archetype: str | None = None

    @property
    def source_id(self) -> str:
        suffix = "_" + digest(self.archetype)[:16] if self.archetype else ""
        return f"hsguru_lab_{self.kind}_{self.format}_{self.rank}_{self.period}{suffix}"

    @property
    def job_class(self) -> str:
        return "detail" if self.kind == "details" else "hot"


def hot_jobs(spent: float, settings: Settings) -> list[Job]:
    if not allowed("hot", spent, settings):
        return []
    return [Job("meta", fmt, rank) for fmt in ("standard", "wild") for rank in ("legend", "diamond_4to1", "top_legend")] + [Job("matchups", fmt, "legend") for fmt in ("standard", "wild")]


def detail_jobs(candidates: list[dict], spent: float, settings: Settings) -> list[Job]:
    if not allowed("detail", spent, settings):
        return []
    buckets = {fmt: sorted([r for r in candidates if r["format"] == fmt], key=lambda r: (-(r.get("games") or 0), r["archetype"])) for fmt in ("standard", "wild")}
    chosen, seen = [], set()
    # Allocate opportunities to both formats instead of starving Wild by volume.
    while any(buckets.values()) and len(chosen) < settings.deep_limit:
        for fmt, rows in buckets.items():
            if rows and len(chosen) < settings.deep_limit:
                row = rows.pop(0)
                key = (fmt,row["archetype"].casefold())
                if key not in seen:
                    chosen.append(Job("details", fmt, "legend", archetype=row["archetype"]))
                    seen.add(key)
    return chosen
