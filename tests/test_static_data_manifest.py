# 测试发行静态数据库清单由实际文件生成并能阻止元数据漂移。

from __future__ import annotations

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.game_data.build_static_database import write_static_manifest
from tools.release.prepare_release import validate_static_manifest
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
        self.assertEqual(
            MANIFEST.read_text(encoding="utf-8"),
            json.dumps(generated, ensure_ascii=False, indent=2) + "\n",
        )

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
            with StaticGameDataDao(database) as dao:
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
