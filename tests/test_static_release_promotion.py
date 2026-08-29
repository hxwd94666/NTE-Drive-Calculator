# 测试发行静态库晋升必须绑定本机 dataset、来源哈希和成对替换。

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.game_data.promote_static_release import (
    DATABASE_FILENAME,
    IMPORTER_VERSION,
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    StaticReleasePromotionError,
    finalize_candidate,
    promote_candidate,
)


class StaticReleasePromotionTests(unittest.TestCase):
    def _create_candidate(self, root: Path, dataset_id: str) -> tuple[Path, Path]:
        content_root = root / "Content"
        source_path = content_root / "DataTable" / "Fixture.json"
        source_path.parent.mkdir(parents=True)
        source_path.write_text('{"fixture": true}\n', encoding="utf-8")
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()

        candidate_dir = root / "candidate"
        candidate_dir.mkdir()
        database_path = candidate_dir / DATABASE_FILENAME
        with closing(sqlite3.connect(database_path)) as connection:
            connection.executescript(
                """
                CREATE TABLE dataset (
                    dataset_id TEXT PRIMARY KEY,
                    importer_version INTEGER NOT NULL,
                    built_at_utc TEXT NOT NULL
                );
                CREATE TABLE schema_migration (
                    version INTEGER PRIMARY KEY,
                    applied_at_utc TEXT NOT NULL
                );
                CREATE TABLE source_file (
                    source_file_id INTEGER PRIMARY KEY,
                    relative_path TEXT NOT NULL UNIQUE,
                    sha256 TEXT NOT NULL,
                    row_count INTEGER NOT NULL
                );
                CREATE TABLE source_row (
                    source_row_id INTEGER PRIMARY KEY,
                    source_file_id INTEGER NOT NULL REFERENCES source_file(source_file_id),
                    row_key TEXT NOT NULL,
                    payload_json TEXT,
                    content_sha256 TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO dataset VALUES (?, ?, ?)",
                (dataset_id, IMPORTER_VERSION, "2026-08-28T00:00:00+00:00"),
            )
            connection.execute(
                "INSERT INTO schema_migration VALUES (?, ?)",
                (SCHEMA_VERSION, "2026-08-28T00:00:00+00:00"),
            )
            connection.execute(
                "INSERT INTO source_file VALUES (1, ?, ?, 1)",
                ("DataTable/Fixture.json", source_hash),
            )
            connection.execute(
                "INSERT INTO source_row VALUES (1, 1, 'fixture', NULL, ?)",
                (source_hash,),
            )
            connection.commit()

        config_path = root / "local.paths.json"
        config_path.write_text(
            json.dumps(
                {
                    "dataset_id": dataset_id,
                    "official_content_root": str(content_root),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return candidate_dir, config_path

    def test_dataset_mismatch_is_rejected_before_manifest_is_written(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_dir, config_path = self._create_candidate(root, "candidate-v2")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["dataset_id"] = "configured-v3"
            config_path.write_text(json.dumps(config), encoding="utf-8")

            with self.assertRaisesRegex(
                StaticReleasePromotionError,
                "dataset 不一致",
            ):
                finalize_candidate(candidate_dir, config_path)

            self.assertFalse((candidate_dir / MANIFEST_FILENAME).exists())

    def test_changed_official_source_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_dir, config_path = self._create_candidate(root, "fixture-v1")
            config = json.loads(config_path.read_text(encoding="utf-8"))
            source_path = (
                Path(config["official_content_root"])
                / "DataTable"
                / "Fixture.json"
            )
            source_path.write_text('{"fixture": false}\n', encoding="utf-8")

            with self.assertRaisesRegex(
                StaticReleasePromotionError,
                "官方来源不一致",
            ):
                finalize_candidate(candidate_dir, config_path)

    def test_promotion_refreshes_evidence_and_replaces_database_manifest_pair(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_dir, config_path = self._create_candidate(root, "fixture-v1")
            target_dir = root / "release"
            target_dir.mkdir()
            (target_dir / DATABASE_FILENAME).write_bytes(b"old-database")
            (target_dir / MANIFEST_FILENAME).write_text(
                '{"old": true}\n',
                encoding="utf-8",
            )

            result = promote_candidate(candidate_dir, config_path, target_dir)

            self.assertEqual("fixture-v1", result["dataset_id"])
            self.assertEqual(
                hashlib.sha256(
                    (target_dir / DATABASE_FILENAME).read_bytes()
                ).hexdigest().upper(),
                result["database_sha256"],
            )
            manifest = json.loads(
                (target_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
            self.assertEqual("fixture-v1", manifest["database"]["dataset_id"])
            report = json.loads(
                (candidate_dir / "report" / "static_database_report.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(
                result["database_sha256"].lower(),
                report["database_sha256"],
            )
            self.assertIn("finalized_at_utc", report)
            self.assertFalse(list(target_dir.glob("*.pending.*")))
            self.assertFalse(list(target_dir.glob("*.rollback.*")))


if __name__ == "__main__":
    unittest.main()
