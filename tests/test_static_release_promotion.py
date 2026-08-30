# 测试发行静态库晋升必须绑定本机 dataset、来源哈希和成对替换。

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tools.game_data import promote_static_release as promotion
from tools.game_data.promote_static_release import (
    DATABASE_HARD_LIMIT_BYTES,
    DATABASE_REPOSITORY_BUDGET_BYTES,
    DATABASE_WARNING_BYTES,
    DATABASE_FILENAME,
    IMPORTER_VERSION,
    MANIFEST_FILENAME,
    REPORT_RELATIVE_PATH,
    SCHEMA_VERSION,
    StaticReleasePromotionError,
    finalize_candidate,
    main,
    promote_candidate,
    validate_database_size,
    verify_candidate,
)


class StaticReleasePromotionTests(unittest.TestCase):
    def test_size_policy_has_warning_repository_and_permanent_boundaries(self) -> None:
        validate_database_size(DATABASE_WARNING_BYTES - 1)
        with self.assertRaisesRegex(StaticReleasePromotionError, "默认阻断"):
            validate_database_size(DATABASE_WARNING_BYTES)
        validate_database_size(
            DATABASE_WARNING_BYTES,
            allow_size_warning=True,
        )
        validate_database_size(
            DATABASE_REPOSITORY_BUDGET_BYTES,
            allow_size_warning=True,
        )
        with self.assertRaisesRegex(StaticReleasePromotionError, "仓库绝对预算"):
            validate_database_size(
                DATABASE_REPOSITORY_BUDGET_BYTES + 1,
                allow_size_warning=True,
            )
        with self.assertRaisesRegex(StaticReleasePromotionError, "永久硬上限"):
            validate_database_size(
                DATABASE_HARD_LIMIT_BYTES,
                allow_size_warning=True,
                repository_bound=False,
            )

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

    def test_verify_only_is_readonly_and_checks_final_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_dir, config_path = self._create_candidate(root, "fixture-v1")
            target_dir = root / "release"
            target_dir.mkdir()
            marker = target_dir / "unchanged.txt"
            marker.write_text("keep\n", encoding="utf-8")
            finalized_output = StringIO()
            with redirect_stdout(finalized_output):
                finalize_exit_code = main(
                    [
                        "--candidate-dir", str(candidate_dir),
                        "--local-config", str(config_path),
                        "--target-dir", str(target_dir),
                        "--finalize-only",
                    ]
                )
            before = {
                path.relative_to(candidate_dir): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in candidate_dir.rglob("*")
                if path.is_file()
            }

            result = verify_candidate(candidate_dir, config_path)
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "--candidate-dir", str(candidate_dir),
                        "--local-config", str(config_path),
                        "--target-dir", str(target_dir),
                        "--verify-only",
                    ]
                )

            after = {
                path.relative_to(candidate_dir): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in candidate_dir.rglob("*")
                if path.is_file()
            }
            self.assertTrue(result["verified"])
            self.assertEqual(0, finalize_exit_code)
            self.assertIn('"finalized": true', finalized_output.getvalue())
            self.assertEqual(0, exit_code)
            self.assertIn('"verified": true', output.getvalue())
            self.assertEqual(before, after)
            self.assertEqual("keep\n", marker.read_text(encoding="utf-8"))
            self.assertFalse((target_dir / DATABASE_FILENAME).exists())
            self.assertFalse((target_dir / MANIFEST_FILENAME).exists())

    def test_verify_only_rejects_tampered_final_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_dir, config_path = self._create_candidate(root, "fixture-v1")
            finalize_candidate(candidate_dir, config_path)
            report_path = candidate_dir / REPORT_RELATIVE_PATH
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["database_sha256"] = "0" * 64
            report_path.write_text(json.dumps(report), encoding="utf-8")

            with self.assertRaisesRegex(
                StaticReleasePromotionError,
                "最终报告与数据库不一致",
            ):
                verify_candidate(candidate_dir, config_path)

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
            self.assertEqual(
                (target_dir / DATABASE_FILENAME).stat().st_size,
                manifest["database"]["size_bytes"],
            )
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
            self.assertEqual(
                (target_dir / DATABASE_FILENAME).stat().st_size,
                report["database_size_bytes"],
            )
            self.assertEqual(
                ["localization_keys", "current_language_projection"],
                report["localization_storage_policy"]["included"],
            )
            self.assertEqual(
                ["full_multilingual_source_payloads"],
                report["localization_storage_policy"]["excluded"],
            )
            self.assertFalse(list(target_dir.glob("*.pending.*")))
            self.assertFalse(list(target_dir.glob("*.rollback.*")))

    def test_same_candidate_and_target_directory_fails_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_dir, config_path = self._create_candidate(root, "fixture-v1")
            manifest_path = candidate_dir / MANIFEST_FILENAME
            report_path = candidate_dir / REPORT_RELATIVE_PATH
            report_path.parent.mkdir(parents=True)
            manifest_path.write_bytes(b"manifest-must-stay")
            report_path.write_bytes(b"report-must-stay")
            markdown_path = report_path.with_suffix(".md")
            markdown_path.write_bytes(b"markdown-must-stay")
            protected = (
                candidate_dir / DATABASE_FILENAME,
                manifest_path,
                report_path,
                markdown_path,
            )
            before = {
                path: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in protected
            }

            with self.assertRaisesRegex(
                StaticReleasePromotionError,
                "候选目录不得与正式发行目录相同",
            ):
                promote_candidate(candidate_dir, config_path, candidate_dir)

            after = {
                path: (path.read_bytes(), path.stat().st_mtime_ns)
                for path in protected
            }
            self.assertEqual(before, after)
            self.assertFalse(list(candidate_dir.glob("*.pending.*")))
            self.assertFalse(list(candidate_dir.glob("*.rollback.*")))

    def test_manifest_replace_failure_restores_existing_database_and_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_dir, config_path = self._create_candidate(root, "fixture-v1")
            target_dir = root / "release"
            target_dir.mkdir()
            target_database = target_dir / DATABASE_FILENAME
            target_manifest = target_dir / MANIFEST_FILENAME
            old_database = b"old-database"
            old_manifest = b'{"old": true}\n'
            target_database.write_bytes(old_database)
            target_manifest.write_bytes(old_manifest)
            original_replace = os.replace
            database_was_replaced = False

            def fail_pending_manifest_replace(
                source: str | os.PathLike[str],
                destination: str | os.PathLike[str],
            ) -> None:
                nonlocal database_was_replaced
                source_path = Path(source)
                destination_path = Path(destination)
                if (
                    source_path.name.startswith(f".{DATABASE_FILENAME}.pending.")
                    and destination_path == target_database
                ):
                    database_was_replaced = True
                if (
                    source_path.name.startswith(f".{MANIFEST_FILENAME}.pending.")
                    and destination_path == target_manifest
                ):
                    self.assertTrue(database_was_replaced)
                    self.assertNotEqual(old_database, target_database.read_bytes())
                    raise PermissionError("injected manifest replace failure")
                original_replace(source, destination)

            with (
                patch.object(
                    promotion.os,
                    "replace",
                    side_effect=fail_pending_manifest_replace,
                ),
                self.assertRaisesRegex(
                    PermissionError,
                    "injected manifest replace failure",
                ),
            ):
                promote_candidate(candidate_dir, config_path, target_dir)

            self.assertEqual(old_database, target_database.read_bytes())
            self.assertEqual(old_manifest, target_manifest.read_bytes())
            self.assertFalse(list(target_dir.glob("*.pending.*")))
            self.assertFalse(list(target_dir.glob("*.rollback.*")))

    def test_final_validation_failure_restores_existing_database_and_manifest(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_dir, config_path = self._create_candidate(root, "fixture-v1")
            target_dir = root / "release"
            target_dir.mkdir()
            target_database = target_dir / DATABASE_FILENAME
            target_manifest = target_dir / MANIFEST_FILENAME
            old_database = b"old-database"
            old_manifest = b'{"old": true}\n'
            target_database.write_bytes(old_database)
            target_manifest.write_bytes(old_manifest)
            original_validate_manifest = promotion.validate_manifest

            def fail_target_validation(
                database_path: Path,
                manifest_path: Path,
                summary: dict[str, Any],
            ) -> dict[str, Any]:
                if database_path.resolve() == target_database.resolve():
                    self.assertNotEqual(old_database, target_database.read_bytes())
                    self.assertNotEqual(old_manifest, target_manifest.read_bytes())
                    raise StaticReleasePromotionError(
                        "injected final validation failure"
                    )
                return original_validate_manifest(
                    database_path,
                    manifest_path,
                    summary,
                )

            with (
                patch.object(
                    promotion,
                    "validate_manifest",
                    side_effect=fail_target_validation,
                ),
                self.assertRaisesRegex(
                    StaticReleasePromotionError,
                    "injected final validation failure",
                ),
            ):
                promote_candidate(candidate_dir, config_path, target_dir)

            self.assertEqual(old_database, target_database.read_bytes())
            self.assertEqual(old_manifest, target_manifest.read_bytes())
            self.assertFalse(list(target_dir.glob("*.pending.*")))
            self.assertFalse(list(target_dir.glob("*.rollback.*")))


if __name__ == "__main__":
    unittest.main()
