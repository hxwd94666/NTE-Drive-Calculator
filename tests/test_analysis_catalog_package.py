# 验证分析组件发行只消费主静态库目录及其完整性，不需要内嵌目录副本。
import hashlib
import json
import lzma
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.counterfactual.package_rust_core import validate_catalog_inputs


class AnalysisCatalogPackageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "static.sqlite3"
        connection = sqlite3.connect(self.database)
        connection.executescript(Path("src/storage/sqlite/schema/038_game_static_analysis_projection.sql").read_text(encoding="utf-8"))
        connection.close()

    def store(self, value, *, corrupt_digest=False):
        raw = json.dumps(value).encode("utf-8")
        digest = "0" * 64 if corrupt_digest else hashlib.sha256(raw).hexdigest()
        connection = sqlite3.connect(self.database)
        try:
            with connection:
                connection.execute("DELETE FROM battle_analysis_catalog")
                connection.execute("INSERT INTO battle_analysis_catalog VALUES(1,1,'xz-json',?,?,?)",
                                   (len(raw), digest, lzma.compress(raw)))
        finally:
            connection.close()

    def test_main_static_catalog_needs_no_component_directory(self):
        self.store({"target": {}, "axis": {}, "rules": {}})
        validate_catalog_inputs(self.database)

    def test_missing_corrupt_and_partial_catalogs_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_catalog_inputs(self.database)
        for value, corrupt in (({"target": {}, "axis": {}, "rules": {}}, True),
                               ({"target": {}, "rules": {}}, False)):
            with self.subTest(value=value, corrupt=corrupt):
                self.store(value, corrupt_digest=corrupt)
                with self.assertRaises(ValueError):
                    validate_catalog_inputs(self.database)
