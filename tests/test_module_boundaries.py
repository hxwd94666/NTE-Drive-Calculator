# 验证重构后公开入口与模块边界的回归测试。
"""重构后公开入口与模块边界的轻量回归测试。"""

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ModuleBoundaryTests(unittest.TestCase):
    def test_user_data_facade_keeps_legacy_surface(self) -> None:
        from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError

        self.assertTrue(hasattr(UserDataDao, "_db"))
        self.assertTrue(hasattr(UserDataDao, "import_inventory_snapshot"))
        self.assertTrue(hasattr(UserDataDao, "replace_active_loadout_plans"))
        self.assertTrue(hasattr(UserDataDao, "create_optimization_profile"))
        self.assertTrue(hasattr(UserDataDao, "create_equipment_apply_job"))
        self.assertTrue(issubclass(UserDataValidationError, ValueError))

    def test_feature_facades_keep_main_window_entrypoints(self) -> None:
        from src.features.inventory import page as inventory_page
        from src.features.official_role import page as role_page
        from src.features.weighted_allocation import page as weighted_page

        self.assertTrue(callable(inventory_page._page_warehouse))
        self.assertTrue(callable(inventory_page._preview_fast_assemble_all_roles))
        self.assertTrue(callable(role_page._page_my_role))
        self.assertTrue(callable(weighted_page.build_weighted_allocation_page))
        self.assertTrue(callable(weighted_page.render_weighted_allocation_result))

    def test_legacy_entry_files_remain_thin(self) -> None:
        for relative_path in (
            "src/storage/sqlite/user_data_dao.py",
            "src/features/inventory/page.py",
            "src/features/weighted_allocation/page.py",
            "src/features/official_role/page.py",
        ):
            with self.subTest(path=relative_path):
                line_count = len((ROOT / relative_path).read_text(encoding="utf-8").splitlines())
                self.assertLessEqual(line_count, 300)

    def test_removed_legacy_role_editor_sources_do_not_return(self) -> None:
        legacy_sources = (
            "src/features/role/base_widget.py",
            "src/features/role/drive_widget.py",
            "src/features/role/marginal_widget.py",
            "src/features/role/page.py",
            "src/features/role/paths.py",
            "src/features/role/tape_widget.py",
            "src/features/role/weapon_widget.py",
            "src/features/role/weight_widget.py",
        )
        for relative_path in legacy_sources:
            with self.subTest(path=relative_path):
                self.assertFalse((ROOT / relative_path).exists())


if __name__ == "__main__":
    unittest.main()
