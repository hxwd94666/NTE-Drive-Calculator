# 提供合成账号上下文，并验证仍在用的装配公开边界和评分等级。
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.app.context import AccountContext, AppContext, ApplicationPaths
from src.domain.allocation_rating import allocation_grade
from src.features.inventory.equipment import assembly_controller as equipment_assembly_controller


def make_context(root: Path) -> AppContext:
    paths = ApplicationPaths.from_roots(
        root=root,
        app_dir=root,
        data_root=root / "data-root",
        bundled_config_dir=root / "config",
        asset_dir=root / "assets",
        app_icon_path=root / "icon.ico",
        static_database_path=root / "game_static.sqlite3",
    )
    account = AccountContext(
        active_account_id="account-a",
        active_account_name="账号 A",
        account_data_root=root / "account-a",
        user_database_path=root / "account-a" / "user_data.sqlite3",
        user_config_dir=root / "account-a" / "config",
        screenshot_dir=root / "account-a" / "screenshots",
        log_dir=root / "account-a" / "logs",
    )
    return AppContext(paths, account)


class WeightedAllocationBoundaryTests(unittest.TestCase):


    def test_calculated_result_uses_public_assembly_boundary(self):
        window = SimpleNamespace()
        calls: list[tuple[str, object]] = []
        with (
            patch.object(
                equipment_assembly_controller,
                "_preview_fast_assemble_all_roles",
                side_effect=lambda _window, role_names: calls.append(("fast", role_names)),
            ),
            patch.object(
                equipment_assembly_controller,
                "_preview_automatic_assemble_role",
                side_effect=lambda _window, role: calls.append(("automatic", role)),
            ),
        ):
            equipment_assembly_controller.request_equipment_assembly(
                window,
                role_names=["甲", "乙"],
                method="nte_core",
            )
            equipment_assembly_controller.request_equipment_assembly(
                window,
                role_names=["甲"],
                method="gamepad",
            )

        self.assertEqual(
            [("fast", ["甲", "乙"]), ("automatic", "甲")],
            calls,
        )

    def test_allocation_grade_is_pure_and_area_relative(self):
        self.assertEqual("ACE", allocation_grade(80, 10))
        self.assertEqual("A", allocation_grade(40, 10))
        self.assertEqual("D", allocation_grade(100, 0))




if __name__ == "__main__":
    unittest.main()
