# 验证残缺来源按实际消费范围阻止晋升，报告身份和路径不能被忽略。
from contextlib import closing
import hashlib
import json
import sqlite3
from pathlib import Path
import tempfile
import unittest

from tools.game_data.static_export_quality import validate_export_quality


class StaticExportQualityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.content = self.root / "HT/Content"
        self.report = self.root / "quality.json"
        payload = {"output": str(self.root), "source_packages_unchanged": True,
                   "affected_paths": ["HT/Content/Blueprints/Broken.uasset"],
                   "file_validation_errors": []}
        self.report.write_text(json.dumps(payload), encoding="utf-8")
        self.config = {"export_quality_report": str(self.report),
                       "export_quality_report_sha256": hashlib.sha256(self.report.read_bytes()).hexdigest()}

    def test_consumed_broken_blueprint_is_rejected_case_insensitively(self):
        with self.assertRaisesRegex(ValueError, "禁止晋升"):
            validate_export_quality(self.config, self.content,
                                    [("combat_blueprint/Blueprints/broken.json", "hash")])

    def test_reference_catalog_does_not_acquire_unrelated_battle_gap(self):
        result = validate_export_quality(self.config, self.content, [("DataTable/Character.json", "hash")])
        self.assertEqual(result["failed_sources_outside_scope"], 1)

    def test_report_hash_and_source_identity_are_required(self):
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            validate_export_quality({**self.config, "export_quality_report_sha256": "changed"}, self.content, [])
        with self.assertRaisesRegex(ValueError, "来源不一致"):
            validate_export_quality(self.config, self.root / "Other", [])

    def test_legacy_config_without_report_is_unchanged(self):
        self.assertIsNone(validate_export_quality({}, self.content, []))

    def reviewed_candidate(self):
        digest = "a" * 64
        self.config["reviewed_blueprint_gaps"] = [
            {"path": "Blueprints/Broken.json", "sha256": digest, "reason": "瞄准机制未知"}]
        database = self.root / "candidate.sqlite3"
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.executescript(
                "CREATE TABLE combat_blueprint_asset(asset_path TEXT COLLATE NOCASE);"
                "CREATE TABLE combat_blueprint_reference(target_asset_path TEXT COLLATE NOCASE, target_available INTEGER);"
                "CREATE TABLE combat_effect_buff_link(target_asset_path TEXT COLLATE NOCASE, target_available INTEGER);"
                "INSERT INTO combat_blueprint_reference VALUES('/Game/Blueprints/Broken',0);")
        return database, [("combat_blueprint/Blueprints/Broken.json", digest)]

    def test_reviewed_gap_can_promote_only_as_unknown_without_projection(self):
        database, sources = self.reviewed_candidate()
        result = validate_export_quality(self.config, self.content, sources, database)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["known_gaps"][0]["status"], "unknown")
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("INSERT INTO combat_blueprint_asset VALUES('/Game/Blueprints/Broken')")
        with self.assertRaisesRegex(ValueError, "仍存在机制投影"):
            validate_export_quality(self.config, self.content, sources, database)

    def test_gap_does_not_authorize_changed_bytes_or_available_reference(self):
        database, sources = self.reviewed_candidate()
        with self.assertRaisesRegex(ValueError, "哈希变化"):
            validate_export_quality(self.config, self.content, [(sources[0][0], "b" * 64)], database)
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("UPDATE combat_blueprint_reference SET target_available=1")
        with self.assertRaisesRegex(ValueError, "仍被标为可用"):
            validate_export_quality(self.config, self.content, sources, database)

    def test_gap_requires_report_and_cannot_be_a_stale_blanket_exception(self):
        database, sources = self.reviewed_candidate()
        with self.assertRaisesRegex(ValueError, "未命中"):
            validate_export_quality(self.config, self.content, [], database)
        self.config.pop("export_quality_report")
        with self.assertRaisesRegex(ValueError, "缺少解包审计"):
            validate_export_quality(self.config, self.content, sources, database)
