# 固定词条配装的上下文、不可变结果和跨功能调用边界。
from __future__ import annotations

import ast
import inspect
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.app.context import AccountContext, AppContext, ApplicationPaths
from src.domain.allocation_rating import allocation_grade
from src.features.inventory import equipment_assembly_controller
from src.features.weighted_allocation.dependencies import (
    weighted_allocation_dependencies,
)
from src.features.weighted_allocation import weighted_result_view
from src.features.weighted_allocation import weighted_preferences
from src.features.weighted_allocation import weighted_workflow


ROOT = Path(__file__).resolve().parents[1]


def imported_modules(relative_path: str) -> set[str]:
    path = ROOT / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


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
    def test_feature_modules_do_not_read_runtime_or_other_page_internals(self):
        for relative_path in (
            "src/features/weighted_allocation/dependencies.py",
            "src/features/weighted_allocation/weighted_preferences.py",
            "src/features/weighted_allocation/weighted_result_view.py",
            "src/features/weighted_allocation/weighted_shell.py",
            "src/features/weighted_allocation/weighted_workflow.py",
        ):
            modules = imported_modules(relative_path)
            with self.subTest(path=relative_path):
                self.assertNotIn("src.app.runtime", modules)
                self.assertNotIn(
                    "src.ui.equipment_presentation",
                    modules,
                )
                self.assertNotIn(
                    "src.features.inventory.page",
                    modules,
                )
        preferences_source = (
            ROOT / "src/features/weighted_allocation/weighted_preferences.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("save_account_character_weights", preferences_source)

    def test_dependencies_are_derived_from_app_context(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = make_context(Path(temporary))
            dependencies = weighted_allocation_dependencies(SimpleNamespace(app_context=context))

        self.assertEqual(
            context.account.user_database_path,
            dependencies.user_database_path,
        )
        self.assertEqual(
            context.paths.asset_dir / "game_ui",
            dependencies.game_ui_asset_root,
        )
        self.assertIs(context.account_settings, dependencies.account_settings)

    def test_result_renderer_accepts_one_immutable_preview(self):
        parameters = inspect.signature(weighted_result_view.render_weighted_allocation_result).parameters

        self.assertEqual(
            ["window", "preview"],
            list(parameters)[:2],
        )
        source = inspect.getsource(weighted_result_view._weighted_result_role_detail)
        self.assertNotIn("UserDataDao", source)
        self.assertNotIn("load_official_role_detail", source)

    def test_stale_calculation_result_is_ignored_after_account_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            context = make_context(Path(temporary))
            token = object()
            window = SimpleNamespace(
                app_context=context,
                _weighted_calculation_token=token,
            )
            stale_preview = SimpleNamespace(user_database_path=Path(temporary) / "old" / "user.sqlite3")

            weighted_workflow._on_done(window, token, stale_preview)

        self.assertFalse(hasattr(window, "_weighted_allocation_preview"))

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

    def test_all_roles_have_empty_stat_defaults(self):
        selector = SimpleNamespace(
            get_selected=lambda: ["主角", "角色甲", "角色乙"],
            get_priority_groups=lambda: [["主角"], ["角色甲"], ["角色乙"]],
        )
        window = SimpleNamespace(
            weighted_role_selector=selector,
            _weighted_role_ids={"主角": 1051, "角色甲": 101, "角色乙": 102},
            _weighted_preference_overrides={
                1051: {},
                101: {},
                102: {
                    "core_main_property_id": None,
                    "substat_priorities": [],
                },
            },
            _weighted_default_suits={},
            _weighted_default_property_weights={},
        )

        rows = weighted_preferences._selection_rows(window)

        self.assertEqual([1051, 101, 102], [row["character_id"] for row in rows])
        self.assertEqual(
            [None, None, None],
            [row["core_main_property_id"] for row in rows],
        )
        self.assertEqual(
            [[], [], []],
            [row["substat_priorities"] for row in rows],
        )

    def test_explicit_non_default_stats_remain_unchanged(self):
        preference = {
            "core_main_property_id": "CritBase",
            "substat_priorities": ["CritDamageBase", "AtkUp"],
        }

        self.assertEqual(
            "CritBase",
            weighted_preferences._effective_core_main_property_id(101, preference),
        )
        self.assertEqual(
            ["CritDamageBase", "AtkUp"],
            weighted_preferences._effective_substat_priorities(101, preference),
        )

    def test_saved_empty_preferences_remain_empty(self):
        preference = {
            "core_main_property_id": None,
            "substat_priorities": [],
        }

        self.assertIsNone(
            weighted_preferences._effective_core_main_property_id(1051, preference)
        )
        self.assertEqual(
            [],
            weighted_preferences._effective_substat_priorities(1051, preference),
        )

    def test_saved_protagonist_magbase_preferences_remain_unchanged(self):
        preference = {
            "core_main_property_id": "MagBase",
            "substat_priorities": ["MagBase"],
        }

        self.assertEqual(
            "MagBase",
            weighted_preferences._effective_core_main_property_id(1051, preference),
        )
        self.assertEqual(
            ["MagBase"],
            weighted_preferences._effective_substat_priorities(1051, preference),
        )


if __name__ == "__main__":
    unittest.main()
