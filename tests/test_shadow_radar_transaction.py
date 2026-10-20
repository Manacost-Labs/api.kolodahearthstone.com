"""Execute the actual PHP insertion, including heredoc's missing final newline."""

import base64
import re
import subprocess
from pathlib import Path

import pytest

SOURCE = (Path(__file__).parents[1] / "platform/scripts/sync-shadow.php").read_text()


def insert_fragment(sql):
    start = SOURCE.index('    $mergeSql = str_replace("\\nCOMMIT;"')
    end = SOURCE.index("    $mergeOutput = runProcess(", start)
    code = (
        "<?php\n$mergeSql=base64_decode('"
        + base64.b64encode(sql.encode()).decode()
        + "');\n"
        '$radarSql="-- FIXTURE_RADAR";\n' + SOURCE[start:end] + "\necho $mergeSql;\n"
    )
    return subprocess.run(
        ["php"], input=code, text=True, capture_output=True, check=False
    )


@pytest.mark.parametrize("ending", ["", "\n"])
def test_actual_heredoc_always_imports_radar_before_commit(ending):
    match = re.search(r"\$mergeSql = <<<'SQL'\n(.*?)\nSQL;", SOURCE, re.DOTALL)
    assert match
    sql = match[1] + ending
    result = insert_fragment(sql)
    assert result.returncode == 0, result.stderr
    assert result.stdout == sql.replace("\nCOMMIT;", "\n-- FIXTURE_RADAR\nCOMMIT;")
    assert result.stdout.count("-- FIXTURE_RADAR") == 1
    assert result.stdout.index("BEGIN;") < result.stdout.index("-- FIXTURE_RADAR")
    assert result.stdout.index("-- FIXTURE_RADAR") < result.stdout.index("COMMIT;")


@pytest.mark.parametrize("sql", ["BEGIN;", "BEGIN;\nCOMMIT;\nCOMMIT;"])
def test_missing_or_ambiguous_boundary_fails_closed(sql):
    result = insert_fragment(sql)
    assert result.returncode != 0
    assert "Expected exactly one radar transaction boundary" in result.stderr
