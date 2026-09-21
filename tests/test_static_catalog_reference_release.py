# 验证独立图鉴的搜索、详情、无任务轨外与旧战报隔离。
from __future__ import annotations

import hashlib
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from src.features.static_catalog.dependencies import build_static_catalog_domain_pages, build_static_catalog_providers
from src.integrations.role_catalog_release import read_role_catalog
from src.integrations.static_catalog_release import StaticCatalogReleaseError
from src.services.game_ui_asset_catalog import GameUiAssetCatalog
from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.services.static_catalog_character_release_metadata import CharacterReleaseMetadataService
from src.services.static_catalog_terminology_service import StaticCatalogTerminologyService
from src.services.static_catalog_service import StaticCatalogService
from src.storage.sqlite.static_catalog_character_queries import StaticCatalogCharacterQueries
from src.storage.sqlite.static_catalog_monster_queries import StaticCatalogMonsterQueries
from src.ui.equipment_presentation import EquipmentPresentation
from tools.game_data.static_database_build_support import StaticDatabaseError
from tools.game_data.static_database_progression_imports import ProgressionImportMixin

NTE_TEST_TIER = "core"
ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "data/game_static.sqlite3"
CATALOG = Path(os.environ.get("NTE_ROLE_CATALOG_TEST_ROOT", str(ROOT / "data/role_catalog")))


class ReferenceCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def service(self, release):
        service = StaticCatalogService(
            static_database_path=MAIN,
            providers=build_static_catalog_providers(MAIN, role_catalog=release),
            domain_database_paths={key: release.database_path for key in release.catalog_domains},
        )
        self.addCleanup(service.close)
        return service

    def test_public_search_and_detail_use_new_roles_and_forks(self):
        release = read_role_catalog(CATALOG)
        service = self.service(release)
        request = service.start_request()
        self.assertNotEqual(request.release.dataset_id, request.release_for("character").dataset_id)
        self.assertEqual(request.release, request.release_for("formula"))
        for domain, key, name in (("character", "1042", "黑羽"), ("character", "1057", "明音凛"),
                                  ("fork", "fork_twinbirds", "罪与罚"), ("fork", "fork_prism", "月的对跖点")):
            with self.subTest(name=name):
                page = service.search(request, domain_key=domain, query=name, offset=0, limit=20)
                self.assertIn(key, {item.record_id for item in page.items})
                detail = service.detail(request, domain_key=domain, record_id=key)
                self.assertIsNotNone(detail)
                self.assertIn(name, detail.item.title)
        page = service.search(request, domain_key="all", query="黑羽", offset=0, limit=20)
        self.assertIn(("character", "1042"), {(item.domain_key, item.record_id) for item in page.items})

    def test_new_role_progression_and_official_art_are_available(self):
        release = read_role_catalog(CATALOG)
        queries = StaticCatalogCharacterQueries(release.database_path)
        self.addCleanup(queries.close)
        service = StaticCatalogCharacterService(queries)
        assets = GameUiAssetCatalog(release.asset_root)
        metadata = CharacterReleaseMetadataService(queries, StaticCatalogTerminologyService(queries))
        for identity in (1042, 1057):
            detail = service.get_character_detail(identity)
            self.assertIsNotNone(detail)
            self.assertIsNotNone(detail.progression)
            self.assertTrue(detail.progression.upgrade_levels)
            self.assertTrue(detail.progression.breakthrough_stages)
            self.assertTrue(assets.character_icon(identity).is_file())
            self.assertTrue(assets.character_art(identity).is_file())
            self.assertEqual("S", metadata.metadata(identity).quality)
            self.assertEqual("limited", metadata.metadata(identity).acquisition_type)

    def test_new_outer_rule_does_not_require_task_or_become_battle_components(self):
        release = read_role_catalog(CATALOG)
        query = StaticCatalogMonsterQueries(release.database_path)
        self.addCleanup(query.close)
        rule = query.outer_realm_season_buff("Abyss_10")
        self.assertEqual("星流环线", rule["season_name_zh"])
        self.assertEqual("星明如昼", rule["buff_name_zh"])
        self.assertIn("30%", rule["description_zh"])
        self.assertEqual((), rule["components"])
        with closing(sqlite3.connect(f"{release.database_path.as_uri()}?mode=ro", uri=True)) as connection:
            self.assertIsNone(connection.execute("SELECT 1 FROM outer_realm_rotation WHERE level_config_id='Abyss_10'").fetchone())
            self.assertEqual(0, connection.execute("SELECT COUNT(*) FROM outer_realm_season_buff_component").fetchone()[0])
        self.assertEqual("夕照环线", query.outer_realm_season_buff("Abyss_11")["season_name_zh"])
        self.assertEqual("澄明环线", query.outer_realm_season_buff("Abyss_12")["season_name_zh"])
        old = StaticCatalogMonsterQueries(MAIN)
        self.addCleanup(old.close)
        self.assertTrue(old.outer_realm_season_buff("Abyss_8")["components"])
        self.assertIsNone(old.outer_realm_season_buff("Abyss_10"))

    def test_changed_reference_release_invalidates_frozen_request(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "game_static.sqlite3"
            shutil.copy2(CATALOG / "game_static.sqlite3", database)
            shutil.copy2(CATALOG / "manifest.json", root / "manifest.json")
            # 通过正式 provider 选择副本，使一次请求同时冻结两个数据集。
            from src.features.static_catalog.providers.character import CharacterCatalogProvider
            service = StaticCatalogService(static_database_path=MAIN, providers=(CharacterCatalogProvider(database),),
                                           domain_database_paths={"character": database})
            try:
                request = service.start_request()
                with closing(sqlite3.connect(database)) as connection:
                    connection.execute("UPDATE dataset SET built_at_utc='changed'")
                    connection.commit()
                with self.assertRaises(StaticCatalogReleaseError):
                    service.detail(request, domain_key="character", record_id="1042")
            finally:
                service.close()

    def test_real_domain_pages_open_new_records_without_changing_main_database(self):
        digest = hashlib.sha256(MAIN.read_bytes()).hexdigest()
        release = read_role_catalog(CATALOG)
        owner = QWidget()
        context = SimpleNamespace(paths=SimpleNamespace(asset_dir=ROOT / "assets"))
        specs = build_static_catalog_domain_pages(MAIN, ROOT / "assets/game_ui", role_catalog=release,
            equipment_presentation=EquipmentPresentation(app_context=context, dialog_parent=owner))
        try:
            for key, identity, name in (("character", "1042", "黑羽"), ("character", "1057", "明音凛"),
                                        ("fork", "fork_twinbirds", "罪与罚"), ("fork", "fork_prism", "月的对跖点")):
                page = next(spec for spec in specs if spec.domain_key == key).build(owner)
                if key == "character":
                    page.open_character(int(identity))
                else:
                    page.open_fork(identity)
                self.app.processEvents()
                self.assertTrue(any(name in label.text() for label in page.findChildren(QLabel)), name)
            monster = next(spec for spec in specs if spec.domain_key == "monsters").build(owner)
            self.assertTrue(monster.open_record("outer_buff|Abyss_10"))
            self.assertTrue(any("星明如昼" in label.text() for label in monster.findChildren(QLabel)))
            self.assertTrue(any("25%" in label.text() and "15秒" in label.text() for label in monster.findChildren(QLabel)))
        finally:
            for spec in reversed(specs):
                spec.close()
            owner.deleteLater()
        self.assertEqual(digest, hashlib.sha256(MAIN.read_bytes()).hexdigest())

    def test_fork_campaigns_accept_new_count_but_reject_duplicate_identity(self):
        with closing(sqlite3.connect(f"{(CATALOG / 'game_static.sqlite3').resolve().as_uri()}?mode=ro", uri=True)) as connection:
            self.assertGreater(connection.execute("SELECT COUNT(*) FROM fork_lottery_campaign").fetchone()[0], 8)
        importer = ProgressionImportMixin()
        importer.rows = {"fork_lottery_data": {"1": {"PoolIDMap": [{"Value": "same"}, {"Value": "same"}]}}}
        with self.assertRaises(StaticDatabaseError):
            importer._import_fork_lottery_campaigns()


if __name__ == "__main__":
    unittest.main()
