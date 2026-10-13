# 验证角色目录身份、隔离、损坏拒绝、新角色养成与页面可操作性。
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from src.app.context import ApplicationPaths
from src.features.official_role.controller import OfficialRoleController
from src.features.official_role.dependencies import OfficialRoleDependencies
from src.integrations.role_catalog_release import read_role_catalog, resolve_role_catalog
from src.services.official_role.profiles import OfficialRoleProfileUpdate
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from tools.game_data.build_role_catalog import RoleCatalogBuilder
from tools.game_data import promote_static_release as promotion
from tools.game_data.promote_role_catalog import promote_role_catalog

NTE_TEST_TIER = "core"
ROOT = next(parent for parent in Path(__file__).resolve().parents
            if (parent / "pyproject.toml").is_file())
CATALOG = Path(os.environ.get("NTE_ROLE_CATALOG_TEST_ROOT", str(ROOT / "data/role_catalog")))


class RoleCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.user = self.root / "user.sqlite3"
        with UserDataDao(self.user, account_id="role-catalog-test"):
            pass

    def wait_for(self, predicate, message):
        # 使用真实 Qt 事件循环释放 Python 执行权，避免短 qWait 轮询饿死查库线程。
        if not predicate():
            loop = QEventLoop()
            poll, timeout = QTimer(), QTimer()
            poll.setInterval(10)
            poll.timeout.connect(lambda: loop.quit() if predicate() else None)
            timeout.setSingleShot(True)
            timeout.timeout.connect(loop.quit)
            poll.start()
            timeout.start(10_000)
            try:
                loop.exec()
            finally:
                poll.stop()
                timeout.stop()
        self.assertTrue(predicate(), message() if callable(message) else message)

    def dependencies(self):
        release = read_role_catalog(CATALOG)
        return OfficialRoleDependencies("role-catalog-test", 7, self.user, release.database_path,
                                        self.root / "shared.sqlite3", release.asset_root, release.sha256)

    def test_paths_and_role_dependencies_keep_game_database_separate(self):
        target = self.root / "data/role_catalog"
        shutil.copytree(CATALOG, target)
        game = self.root / "data/game_static.sqlite3"
        game.write_bytes(b"untouched game database")
        paths = ApplicationPaths.from_roots(root=self.root, app_dir=self.root, data_root=self.root,
            bundled_config_dir=self.root, asset_dir=self.root, app_icon_path=self.root / "icon.ico")
        account = SimpleNamespace(active_account_id="a", user_database_path=self.user)
        deps = OfficialRoleDependencies.from_app_context(SimpleNamespace(paths=paths, account=account, generation=7))
        self.assertEqual(game, paths.static_database_path)
        self.assertEqual(game, deps.static_database_path)
        self.assertEqual(target / "game_ui", deps.asset_root)
        self.assertEqual(game, paths.equipment_allocation_database_path)
        self.assertEqual(b"untouched game database", game.read_bytes())
        self.assertEqual(paths.static_database_path, OfficialRoleDependencies.from_app_context(
            SimpleNamespace(paths=replace(paths, role_catalog=None), account=account, generation=8)).static_database_path)

    def test_absent_catalog_uses_existing_game_path(self):
        self.assertIsNone(resolve_role_catalog(self.root / "data/game_static.sqlite3"))

    def test_corrupt_database_and_wrong_scope_are_rejected(self):
        target = self.root / "data/role_catalog"
        shutil.copytree(CATALOG, target)
        manifest_path = target / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["catalog_scope"] = "game"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_role_catalog(target)
        manifest["catalog_scope"] = "role_page"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with (target / "game_static.sqlite3").open("ab") as stream:
            stream.write(b"changed")
        with self.assertRaises(ValueError):
            read_role_catalog(target)

    def test_corrupt_image_rejects_incomplete_package(self):
        target = self.root / "role_catalog"
        shutil.copytree(CATALOG, target)
        next((target / "game_ui").rglob("*.png")).write_bytes(b"bad image")
        with self.assertRaises(ValueError):
            read_role_catalog(target)

    def test_role_promotion_cannot_overwrite_game_dataset(self):
        target = self.root / "data"
        target.mkdir()
        game = target / "game_static.sqlite3"
        game.write_bytes(b"old")
        finalized = {"database_path": str(CATALOG / "game_static.sqlite3"),
                     "manifest_path": str(CATALOG / "manifest.json"), "summary": {"catalog_scope": "role_page"}}
        with patch.object(promotion, "finalize_candidate", return_value=finalized), patch.object(promotion, "DEFAULT_TARGET_DIR", target):
            with self.assertRaises(promotion.StaticReleasePromotionError):
                promotion.promote_candidate(CATALOG, self.root / "config.json", target)
        self.assertEqual(b"old", game.read_bytes())

    def test_source_selection_does_not_promote_incomplete_rows(self):
        builder = object.__new__(RoleCatalogBuilder)
        builder.overrides_path = self.root / "overrides.json"
        builder.overrides_path.write_text('{"character_overrides":{}}', encoding="utf-8")
        valid = {"ItemName": {"LocalizedString": "测试角色"},
                 "ElementData": {"PropModifyID": "test_base", "UpgradeModifyPackId": "test_lv"}}
        builder.rows = {"character": {"1": valid, "2": {**valid, "ItemName": {}},
                         "3": {**valid, "ElementData": {"PropModifyID": "test_base", "UpgradeModifyPackId": "other_lv"}}},
                        "character_abilities": {"1": {"CharacterAbilityList": ["skill"]}},
                        "equipment_plans": {}, "cultivation_guides": {}}
        builder._select_role_rows()
        self.assertEqual({"1"}, set(builder.rows["character"]))
        self.assertEqual({"missing_official_name", "base_and_growth_identity_conflict"},
                         {row["reason"] for row in builder.excluded_roles})

    def test_failed_promotion_restores_previous_package_and_can_retry(self):
        target = self.root / "data/role_catalog"
        finalized = {"database_path": str(CATALOG / "game_static.sqlite3")}
        promote_role_catalog(finalized, target)
        before = read_role_catalog(target)
        original_replace = os.replace

        def fail_install(source, destination):
            if Path(source).name == "new":
                raise OSError("simulated installation failure")
            return original_replace(source, destination)

        with patch("tools.game_data.promote_role_catalog.os.replace", side_effect=fail_install):
            with self.assertRaises(OSError):
                promote_role_catalog(finalized, target)
        self.assertEqual(before, read_role_catalog(target))
        self.assertFalse((target.parent / ".role_catalog.previous").exists())
        self.assertTrue(promote_role_catalog(finalized, target)["promoted"])

    def test_new_roles_load_and_save_without_modifying_catalogs(self):
        controller = OfficialRoleController(self.dependencies())
        paths = [controller.dependencies.static_database_path, ROOT / "data/game_static.sqlite3"]
        hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
        roles = {row["character_id"] for row in controller.load_index()}
        self.assertTrue({1042, 1057}.issubset(roles))
        self.assertNotIn(1999, roles)
        for character_id, fork_id in ((1042, "fork_twinbirds"), (1057, "fork_prism")):
            detail = controller.load_detail(character_id)
            self.assertEqual(read_role_catalog(CATALOG).scope, detail["catalog_scope"])
            self.assertEqual(86, len(detail["growth_rows"]))
            self.assertIn(fork_id, {row["fork_id"] for row in detail["forks"]})
            self.assertTrue(detail["icon_path"].is_file())
            controller.save_profiles([OfficialRoleProfileUpdate(character_id, 80, 6, 0, (), False,
                fork_id, 80, 6, 1, detail["profile"]["selected_skill_id"], detail["profile"]["skill_levels"], 0)])
            self.assertEqual(fork_id, controller.load_detail(character_id)["profile"]["fork_id"])
        self.assertEqual(hashes, [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths])
        with StaticGameDataDao(ROOT / "data/game_static.sqlite3") as dao:
            self.assertEqual("game", dao.get_catalog_scope())

    def test_complete_game_role_page_shows_calculation_sections(self):
        from PySide6.QtCore import QEvent, QObject
        from PySide6.QtWidgets import QWidget, QVBoxLayout
        from src.app.page_tasks import close_page_tasks
        from src.features.official_role.page import build_official_role_page, refresh_official_role_page

        window = QWidget()
        window.app_context = SimpleNamespace(
            account=SimpleNamespace(active_account_id="role-catalog-test", user_database_path=self.user),
            generation=7,
            paths=SimpleNamespace(static_database_path=ROOT / "data/game_static.sqlite3",
                                  shared_database_path=self.root / "shared.sqlite3", role_catalog=None),
        )
        window._go = lambda _key: None
        before = {id(widget) for widget in self.app.topLevelWidgets()}
        unexpected = []

        class OwnershipProbe(QObject):
            def eventFilter(self, watched, event):
                if (event.type() == QEvent.Show and isinstance(watched, QWidget)
                        and watched.isWindow() and watched is not window and id(watched) not in before):
                    unexpected.append(watched.objectName() or type(watched).__name__)
                return False

        probe = OwnershipProbe()
        self.app.installEventFilter(probe)
        try:
            QVBoxLayout(window).addWidget(build_official_role_page(window))
            window.show()
            self.wait_for(
                lambda: getattr(window, "official_role_tabs", None) is not None
                and window.official_role_tabs.count() > 0,
                lambda: window.my_role_form_area.toolTip() or "角色索引未在预算内就绪",
            )
            tabs = window.official_role_tabs
            selected = next(index for index in range(tabs.count()) if int(tabs.tabBar().tabData(index)) == 1042)
            tabs.setCurrentIndex(selected)
            self.wait_for(
                lambda: tabs.currentWidget().property("loaded"),
                lambda: tabs.currentWidget().toolTip() or "角色详情未在预算内就绪",
            )
            editor = window._official_role_editors[1042]
            self.assertEqual("game", editor["detail"]["catalog_scope"])
            self.assertIsNotNone(editor["detail"]["graduation_template"])
            self.assertTrue(editor["growth"].isEnabled())
            editor["growth"].setValue(70)
            self.assertIn(1042, window._official_role_dirty_ids)
            self.assertGreaterEqual(editor["fork"].findData("fork_twinbirds"), 0)
            refresh_official_role_page(window)
            self.app.processEvents()
            self.assertIs(editor, window._official_role_editors[1042])
            self.assertEqual(70, editor["growth"].value())
            self.assertEqual([], unexpected)
        finally:
            close_page_tasks(window)
            controller = getattr(window, "_official_role_controller", None)
            if controller is not None:
                controller.close()
            # 撤销回调不是物理退场；销毁父窗口和临时库前等待读/写 owner 完成。
            self.wait_for(lambda: not close_page_tasks(window), "角色任务尚未物理退场")
            window.close()
            window.deleteLater()
            self.app.removeEventFilter(probe)
            self.app.processEvents()



if __name__ == "__main__":
    unittest.main()
