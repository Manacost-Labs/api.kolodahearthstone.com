from __future__ import annotations

from pathlib import Path
import re

from app.sources import SOURCES


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "docs" / "DATA_CATALOG.md"


def test_data_catalog_covers_every_registered_source_and_public_api_family() -> None:
    text = CATALOG.read_text(encoding="utf-8")

    missing = [source.id for source in SOURCES if f"`{source.id}`" not in text]
    assert missing == []
    for path in (
        "/datasets/{source_id}",
        "/v1/constructed/decks",
        "/v1/constructed/archetypes",
        "/v1/hsguru/meta",
        "/v1/battlegrounds/heroes",
        "/v1/battlegrounds/minions",
        "/v1/arena/classes",
        "/v1/sources",
        "/api/db/archetypes/{id}",
        "/api/db/bg/minions/{dbfId}",
        "/api/bg/trinkets",
        "/api/patches",
    ):
        assert path in text


def test_documentation_markdown_links_resolve_locally() -> None:
    documents = [ROOT / "README.md", ROOT / "docs" / "API.md", CATALOG]
    missing: list[str] = []
    for document in documents:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]+\]\(([^)#]+)(?:#[^)]+)?\)", text):
            if "://" in target or target.startswith("/"):
                continue
            if not (document.parent / target).resolve().exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")
    assert missing == []
