# 测试发行静态数据库清单由实际文件生成并能阻止元数据漂移。

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

from tools.game_data.build_static_database import write_static_manifest
from tools.release import prepare_release
from tools.release.prepare_release import (
    validate_game_ui_asset_manifest,
    validate_static_manifest,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "game_static.sqlite3"
MANIFEST = ROOT / "data" / "manifest.json"


class StaticDataManifestTests(unittest.TestCase):
    def test_committed_manifest_matches_distribution_database(self) -> None:
        manifest = validate_static_manifest(DATABASE, MANIFEST)

        self.assertEqual(1, manifest["format_version"])
        self.assertTrue(manifest["database"]["source_payloads_omitted"])
        self.assertEqual(
            "tools/game_data/build_static_database.py",
            manifest["build_tool"]["path"],
        )

    def test_generator_writes_manifest_from_actual_database(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "manifest.json"

            generated = write_static_manifest(DATABASE, output)
            loaded = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(generated, loaded)
        committed = json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(DATABASE.stat().st_size, generated["database"]["size_bytes"])
        self.assertEqual(committed, generated)

    def test_schema_29_legacy_manifest_is_accepted_but_wrong_size_is_not(self) -> None:
        manifest = validate_static_manifest(DATABASE, MANIFEST)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "manifest.json"
            changed = json.loads(json.dumps(manifest))
            changed["database"]["size_bytes"] = DATABASE.stat().st_size + 1
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "不一致"):
                validate_static_manifest(DATABASE, path)

    def test_schema_30_manifest_requires_exact_size_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "game_static.sqlite3"
            database.write_bytes(b"schema-30-fixture")
            summary = {
                "schema_version": 30,
                "dataset": {"dataset_id": "fixture-v30"},
            }
            dao = MagicMock()
            dao.__enter__.return_value.summary.return_value = summary
            manifest = {
                "database": {
                    "filename": database.name,
                    "dataset_id": "fixture-v30",
                    "schema_version": 30,
                    "sha256": prepare_release.sha256(database),
                    "source_payloads_omitted": True,
                }
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with (
                patch.object(prepare_release, "StaticGameDataDao", return_value=dao),
                self.assertRaisesRegex(RuntimeError, "缺少 database.size_bytes"),
            ):
                validate_static_manifest(database, manifest_path)

            manifest["database"]["size_bytes"] = database.stat().st_size + 1
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with (
                patch.object(prepare_release, "StaticGameDataDao", return_value=dao),
                self.assertRaisesRegex(RuntimeError, "不一致"),
            ):
                validate_static_manifest(database, manifest_path)

    def test_game_ui_asset_manifest_checks_hash_size_and_file_set(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "characters" / "fixture.png"
            image.parent.mkdir()
            image.write_bytes(b"png-fixture")
            manifest = {
                "manifest_version": 2,
                "files": {
                    "characters/fixture.png": {
                        "bytes": image.stat().st_size,
                        "sha256": prepare_release.sha256(image),
                    }
                },
                "total_files": 1,
                "total_bytes": image.stat().st_size,
            }
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            validate_game_ui_asset_manifest(root, manifest_path)
            image.write_bytes(b"tampered")
            with self.assertRaisesRegex(RuntimeError, "与清单不一致"):
                validate_game_ui_asset_manifest(root, manifest_path)

    def test_packaged_artifacts_require_the_same_resource_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_assets = root / "source-assets"
            bundled_assets = root / "bundled-assets"
            source_image = source_assets / "fixture.png"
            bundled_image = bundled_assets / "fixture.png"
            source_assets.mkdir()
            bundled_assets.mkdir()
            source_image.write_bytes(b"fixture-image")
            bundled_image.write_bytes(source_image.read_bytes())
            asset_manifest = {
                "files": {
                    "fixture.png": {
                        "bytes": source_image.stat().st_size,
                        "sha256": prepare_release.sha256(source_image),
                    }
                },
                "total_files": 1,
                "total_bytes": source_image.stat().st_size,
            }
            source_asset_manifest = source_assets / "manifest.json"
            bundled_asset_manifest = bundled_assets / "manifest.json"
            manifest_text = json.dumps(asset_manifest, sort_keys=True)
            source_asset_manifest.write_text(manifest_text, encoding="utf-8")
            bundled_asset_manifest.write_text(manifest_text, encoding="utf-8")

            source_static_manifest = root / "source-static.json"
            bundled_static_manifest = root / "bundled-static.json"
            source_static_manifest.write_text("{}", encoding="utf-8")
            bundled_static_manifest.write_text("{}", encoding="utf-8")
            source_database = root / "source.sqlite3"
            bundled_database = root / "bundled.sqlite3"
            source_database.touch()
            bundled_database.touch()

            replacements = {
                "STATIC_MANIFEST": source_static_manifest,
                "BUNDLED_STATIC_DATABASE": bundled_database,
                "BUNDLED_STATIC_MANIFEST": bundled_static_manifest,
                "GAME_UI_ASSET_ROOT": source_assets,
                "GAME_UI_ASSET_MANIFEST": source_asset_manifest,
                "BUNDLED_GAME_UI_ASSET_ROOT": bundled_assets,
                "BUNDLED_GAME_UI_ASSET_MANIFEST": bundled_asset_manifest,
            }
            with (
                patch.multiple(prepare_release, **replacements),
                patch.object(prepare_release, "validate_static_database"),
                patch.object(prepare_release, "validate_static_manifest"),
            ):
                prepare_release.validate_packaged_release_artifacts()
                bundled_asset_manifest.write_text(
                    manifest_text + "\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(RuntimeError, "游戏 UI 资源 manifest"):
                    prepare_release.validate_packaged_release_artifacts()

    def test_replaced_database_without_manifest_update_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "game_static.sqlite3"
            manifest = root / "manifest.json"
            shutil.copy2(DATABASE, database)
            shutil.copy2(MANIFEST, manifest)
            with database.open("ab") as stream:
                stream.write(b"manifest-drift")

            with self.assertRaisesRegex(RuntimeError, "不一致"):
                validate_static_manifest(database, manifest)

    def test_runtime_static_dao_rejects_writes_without_wal_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "game_static.sqlite3"
            shutil.copy2(DATABASE, database)
            with closing(sqlite3.connect(database)) as connection:
                schema_version = int(connection.execute(
                    "SELECT MAX(version) FROM schema_migration"
                ).fetchone()[0])
            with StaticGameDataDao(
                database,
                expected_schema_version=schema_version,
            ) as dao:
                with self.assertRaises(sqlite3.OperationalError):
                    dao._connection.execute(
                        """UPDATE logical_character_shape_bonus
                           SET shape_label = 'Type-999'"""
                    )

            self.assertFalse(
                database.with_name(database.name + "-wal").exists()
            )
            self.assertFalse(
                database.with_name(database.name + "-shm").exists()
            )


if __name__ == "__main__":
    unittest.main()
