from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.material_repository import MaterialRepository


def test_catalog_keyset_matches_python_order_and_binds_cursor(monkeypatch):
    cursor = MagicMock()
    cursor.execute = AsyncMock()
    cursor.fetchall = AsyncMock(return_value=[{"content": "<b>Текст</b>"}])
    connection = MagicMock()
    connection.cursor.return_value.__aenter__ = AsyncMock(return_value=cursor)
    pool = MagicMock()
    pool.connection.return_value.__aenter__ = AsyncMock(return_value=connection)
    repository = MagicMock()
    repository._get_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(
        "app.material_repository.get_graphql_repository", lambda: repository
    )
    marker = "card:constructed:ABC_01"
    rows = asyncio.run(MaterialRepository().documents(after=marker))
    query, params = cursor.execute.await_args.args
    # Locale ordering previously made the next page end before its cursor in Python.
    assert query.count('COLLATE "C"') == 2
    assert marker not in query
    assert params["after"] == marker
    assert rows[0]["content"].strip() == "Текст"
