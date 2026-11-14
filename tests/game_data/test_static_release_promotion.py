# 测试发行静态库晋升必须绑定本机 dataset、来源哈希和成对替换。

from __future__ import annotations

import hashlib
import json
import lzma
import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tools.game_data import promote_static_release as promotion
from tools.game_data.storage_repack import (
    create_repack_candidate,
    validate_logical_equivalence,
)
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

NTE_TEST_TIER = "core"


class StaticReleasePromotionTests(unittest.TestCase):
    def test_source_hash_match_does_not_allow_paths_outside_content_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = root / "Content"
            content.mkdir()
            outside = root / "outside.json"
            outside.write_text("fixture", encoding="utf-8")
            digest = promotion.sha256(outside)
            paths = ("../outside.json", "combat_blueprint/../outside.json", str(outside), "..\\outside.json", "./inside.json", "dir//inside.json", "C:outside.json", "//host/share/file")
            for relative in paths:
                with self.subTest(relative=relative), self.assertRaises(StaticReleasePromotionError):
                    with patch.object(promotion, "sha256", wraps=promotion.sha256) as read_hash:
                        try:
                            promotion.validate_source_files([(relative, digest)], content)
                        finally:
                            read_hash.assert_not_called()
            inside = content / "inside.json"
            inside.write_text("fixture", encoding="utf-8")
            promotion.validate_source_files([("inside.json#Rows", digest), ("combat_blueprint/inside.json", digest)], content)

    def test_storage_repack_preserves_every_row_and_promotes_as_one_release(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, config_path = self._create_candidate(root, "fixture-v1")
            baseline_database = baseline / DATABASE_FILENAME
            with closing(sqlite3.connect(baseline_database)) as connection:
                connection.execute("CREATE TABLE padding (value TEXT NOT NULL)")
                connection.executemany(
                    "INSERT INTO padding VALUES (?)",
                    [("x" * 2048,) for _ in range(1024)],
                )
                connection.execute("DELETE FROM padding WHERE rowid > 10")
                connection.commit()
            finalize_candidate(baseline, config_path)
            old_hash = promotion.sha256(baseline_database)
            repack = root / "repack"
            create_repack_candidate(baseline, repack)
            self.assertLess(
                (repack / DATABASE_FILENAME).stat().st_size, baseline_database.stat().st_size
            )
            self.assertEqual(
                10, validate_logical_equivalence(baseline_database, repack / DATABASE_FILENAME)["padding"]
            )
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.update(
                storage_baseline_database_path=str(baseline_database),
                storage_baseline_manifest_path=str(baseline / MANIFEST_FILENAME),
            )
            config_path.write_text(json.dumps(config), encoding="utf-8")
            finalize_candidate(repack, config_path)
            self.assertTrue(verify_candidate(repack, config_path)["verified"])
            result = promote_candidate(repack, config_path, baseline)
            self.assertEqual("fixture-v1", result["dataset_id"])
            self.assertNotEqual(old_hash, promotion.sha256(baseline_database))
            self.assertEqual(
                promotion.sha256(baseline_database),
                json.loads((baseline / MANIFEST_FILENAME).read_text(encoding="utf-8"))
                ["database"]["sha256"],
            )

    def test_storage_repack_rejects_changed_row(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            baseline, config_path = self._create_candidate(root, "fixture-v1")
            with closing(sqlite3.connect(baseline / DATABASE_FILENAME)) as connection:
                connection.execute("CREATE TABLE padding (value TEXT NOT NULL)")
                connection.executemany(
                    "INSERT INTO padding VALUES (?)", [("x" * 2048,) for _ in range(64)]
                )
                connection.execute("DELETE FROM padding WHERE rowid > 1")
                connection.commit()
            finalize_candidate(baseline, config_path)
            repack = root / "repack"
            create_repack_candidate(baseline, repack)
            with closing(sqlite3.connect(repack / DATABASE_FILENAME)) as connection:
                connection.execute("UPDATE padding SET value = 'changed'")
                connection.commit()
            with self.assertRaisesRegex(ValueError, "表内容变化"):
                validate_logical_equivalence(baseline / DATABASE_FILENAME, repack / DATABASE_FILENAME)

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
                CREATE TABLE battle_analysis_catalog (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    format_version INTEGER NOT NULL CHECK (format_version = 1),
                    codec TEXT NOT NULL CHECK (codec = 'xz-json'),
                    decoded_bytes INTEGER NOT NULL CHECK (decoded_bytes > 0 AND decoded_bytes <= 67108864),
                    decoded_sha256 TEXT NOT NULL CHECK (length(decoded_sha256) = 64),
                    payload BLOB NOT NULL
                );
                """
            )
            analysis_raw = json.dumps({"target": {}, "axis": {}, "rules": {}}, separators=(",", ":")).encode("utf-8")
            connection.execute(
                "INSERT INTO battle_analysis_catalog VALUES (1, 1, 'xz-json', ?, ?, ?)",
                (len(analysis_raw), hashlib.sha256(analysis_raw).hexdigest(), lzma.compress(analysis_raw)),
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

    def test_manifest_replace_failure_restores_previous_presence_and_cleans_temporary_files(self) -> None:
        for existing in (False, True):
            with self.subTest(existing=existing), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                candidate_dir, config_path = self._create_candidate(root, "fixture-v1")
                target_dir = root / "release"
                target_dir.mkdir()
                target_database = target_dir / DATABASE_FILENAME
                target_manifest = target_dir / MANIFEST_FILENAME
                old = {target_database: b"old-database", target_manifest: b'{"old": true}\n'}
                if existing:
                    for path, content in old.items():
                        path.write_bytes(content)
                original_replace = os.replace
                failure = PermissionError("injected manifest replace failure")

                def fail_pending_manifest_replace(source, destination) -> None:
                    if Path(source).name.startswith(f".{MANIFEST_FILENAME}.pending."):
                        self.assertNotEqual(old[target_database], target_database.read_bytes())
                        raise failure
                    original_replace(source, destination)

                with patch.object(promotion.os, "replace", side_effect=fail_pending_manifest_replace):
                    with self.assertRaises(PermissionError) as caught:
                        promote_candidate(candidate_dir, config_path, target_dir)
                self.assertIs(failure, caught.exception)
                self.assertEqual(
                    {path: path.read_bytes() for path in target_dir.iterdir()},
                    old if existing else {},
                )

    def test_final_validation_failure_restores_members_independently_and_keeps_recovery_pair(self) -> None:
        # 后验失败已经替换两成员：分别覆盖完整回滚、任一恢复失败和全部恢复失败。
        for failed_names in ((), (DATABASE_FILENAME,), (MANIFEST_FILENAME,), (DATABASE_FILENAME, MANIFEST_FILENAME)):
            with self.subTest(failed_names=failed_names), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                candidate_dir, config_path = self._create_candidate(root, "fixture-v1")
                target_dir = root / "release"
                target_dir.mkdir()
                old = {DATABASE_FILENAME: b"old-database", MANIFEST_FILENAME: b'{"old": true}\n'}
                for name, content in old.items():
                    (target_dir / name).write_bytes(content)
                original_validate = promotion.validate_manifest
                original_replace = os.replace
                failure = StaticReleasePromotionError("injected final validation failure")
                attempted = []

                def fail_target_validation(database_path, manifest_path, summary):
                    if database_path.parent == target_dir:
                        raise failure
                    return original_validate(database_path, manifest_path, summary)

                def fail_selected_restore(source, destination):
                    if any(label in Path(source).name for label in (".rollback.", ".restore.")):
                        attempted.append(Path(destination).name)
                        if Path(destination).name in failed_names:
                            raise PermissionError("injected rollback failure: " + Path(destination).name)
                    original_replace(source, destination)

                with (
                    patch.object(promotion, "validate_manifest", side_effect=fail_target_validation),
                    patch.object(promotion.os, "replace", side_effect=fail_selected_restore),
                ):
                    with self.assertRaises(StaticReleasePromotionError) as caught:
                        promote_candidate(candidate_dir, config_path, target_dir)
                self.assertIs(failure, caught.exception)
                self.assertEqual([DATABASE_FILENAME, MANIFEST_FILENAME], attempted)
                for name, content in old.items():
                    expected = (candidate_dir / name).read_bytes() if name in failed_names else content
                    self.assertEqual(expected, (target_dir / name).read_bytes())
                self.assertFalse(list(target_dir.glob("*.pending.*")))
                self.assertFalse(list(target_dir.glob("*.restore.*")))
                backups = list(target_dir.glob("*.rollback.*"))
                if not failed_names:
                    self.assertEqual([], backups)
                    continue
                self.assertEqual(set(old.values()), {path.read_bytes() for path in backups})
                notes = "\n".join(caught.exception.__notes__)
                for backup in backups:
                    self.assertIn(str(backup), notes)
                    self.assertIn(promotion.sha256(backup), notes)
                for name in failed_names:
                    self.assertIn("injected rollback failure: " + name, notes)
                # 顶层命令只输出异常消息；恢复材料也须显示，不依赖 traceback 才能找到。
                output = StringIO()
                with patch.object(promotion, "promote_candidate", side_effect=failure), redirect_stdout(output):
                    exit_code = main(["--candidate-dir", str(candidate_dir), "--local-config", str(config_path),
                                      "--target-dir", str(target_dir)])
                self.assertEqual(1, exit_code)
                self.assertIn(str(failure), output.getvalue())
                for backup in backups:
                    self.assertIn(str(backup), output.getvalue())

    def test_first_install_final_validation_failure_removes_both_new_members(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidate_dir, config_path = self._create_candidate(root, "fixture-v1")
            target_dir = root / "release"
            original_validate = promotion.validate_manifest
            failure = StaticReleasePromotionError("injected first install validation failure")

            def fail_target_validation(database_path, manifest_path, summary):
                if database_path.parent == target_dir:
                    raise failure
                return original_validate(database_path, manifest_path, summary)

            with patch.object(promotion, "validate_manifest", side_effect=fail_target_validation):
                with self.assertRaises(StaticReleasePromotionError) as caught:
                    promote_candidate(candidate_dir, config_path, target_dir)
            self.assertIs(failure, caught.exception)
            self.assertEqual([], list(target_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
