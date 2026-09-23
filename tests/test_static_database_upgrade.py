# 验证已发行静态库增量升级会重新核对基线、旧表与来源行。
"""Regression tests for additive static-release provenance verification."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tools.game_data.static_database_build_support import (
    StaticDatabaseError,
    canonical_json,
    sha256_bytes,
)
from tools.game_data.upgrade_static_database import (
    PROVENANCE_FILENAME,
    sha256,
    table_fingerprint,
    validate_upgrade_provenance,
)


NTE_TEST_TIER = "core"


def _write_datatable(path: Path, row: dict[str, object]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([{"Rows": {"row-a": row}}], ensure_ascii=False),
        encoding="utf-8",
    )
    return sha256_bytes(canonical_json(row).encode("utf-8"))


def _create_fixture(root: Path) -> dict[str, Path]:
    source_root = root / "Content"
    relative_path = Path("DataTable/Fixture.json")
    row_hash = _write_datatable(source_root / relative_path, {"value": 1})
    baseline = root / "baseline.sqlite3"
    with sqlite3.connect(baseline) as connection:
        connection.executescript(
            """
            CREATE TABLE source_file (
                source_file_id INTEGER PRIMARY KEY,
                relative_path TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                row_count INTEGER NOT NULL
            );
            CREATE TABLE source_row (
                source_row_id INTEGER PRIMARY KEY,
                source_file_id INTEGER NOT NULL,
                row_key TEXT NOT NULL,
                content_sha256 TEXT NOT NULL
            );
            CREATE TABLE preserved_fact (
                fact_id INTEGER PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO source_file VALUES (1, ?, 'whole-file-hash', 1)",
            (relative_path.as_posix(),),
        )
        connection.execute(
            "INSERT INTO source_row VALUES (1, 1, 'row-a', ?)",
            (row_hash,),
        )
        connection.execute("INSERT INTO preserved_fact VALUES (1, 'kept')")
        connection.commit()
        preserved_hash = table_fingerprint(connection, "preserved_fact")

    candidate = root / "candidate.sqlite3"
    candidate.write_bytes(baseline.read_bytes())
    manifest = root / "baseline-manifest.json"
    manifest.write_text('{"fixture":true}\n', encoding="utf-8")
    provenance_path = root / PROVENANCE_FILENAME
    provenance = {
        "format_version": 1,
        "baseline": {
            "database_sha256": sha256(baseline),
            "manifest_sha256": sha256(manifest),
        },
        "candidate": {"database_sha256": sha256(candidate)},
        "verified_source_rows": [{
            "relative_path": relative_path.as_posix(),
            "row_key": "row-a",
            "content_sha256": row_hash,
        }],
        "preserved_table_sha256": {"preserved_fact": preserved_hash},
    }
    provenance_path.write_text(
        json.dumps(provenance, ensure_ascii=False),
        encoding="utf-8",
    )
    return {
        "source_root": source_root,
        "baseline": baseline,
        "manifest": manifest,
        "candidate": candidate,
        "provenance": provenance_path,
    }


def _validate(paths: dict[str, Path]) -> dict[str, object]:
    return validate_upgrade_provenance(
        candidate_database=paths["candidate"],
        provenance_path=paths["provenance"],
        baseline_database=paths["baseline"],
        baseline_manifest=paths["manifest"],
        official_source_root=paths["source_root"],
    )


def test_upgrade_provenance_accepts_exact_baseline_and_source_rows(tmp_path: Path) -> None:
    paths = _create_fixture(tmp_path)

    result = _validate(paths)

    assert result["format_version"] == 1


def test_upgrade_provenance_rejects_preserved_table_change(tmp_path: Path) -> None:
    paths = _create_fixture(tmp_path)
    with sqlite3.connect(paths["candidate"]) as connection:
        connection.execute("UPDATE preserved_fact SET value='changed'")
        connection.commit()
    provenance = json.loads(paths["provenance"].read_text(encoding="utf-8"))
    provenance["candidate"]["database_sha256"] = sha256(paths["candidate"])
    paths["provenance"].write_text(json.dumps(provenance), encoding="utf-8")

    with pytest.raises(StaticDatabaseError, match="候选改写旧表"):
        _validate(paths)


def test_upgrade_provenance_rejects_changed_official_row(tmp_path: Path) -> None:
    paths = _create_fixture(tmp_path)
    _write_datatable(
        paths["source_root"] / "DataTable/Fixture.json",
        {"value": 2},
    )

    with pytest.raises(StaticDatabaseError, match="来源行变化"):
        _validate(paths)
