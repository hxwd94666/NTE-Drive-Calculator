# 验证同级组单人失败不会抹去其余角色的完整联合配装。
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from src.integrations.allocation.native_allocation import create_allocation_executor
from src.models.equipment import Drive
from src.optimizer.allocation_kernel import AllocationKernelRequest
from src.optimizer.scoring import ScoringEngine


ROOT = Path(__file__).resolve().parents[2]


class GroupFailureIsolationTests(unittest.TestCase):
    def test_failed_row_keeps_one_line_reason_and_shows_saved_peer_count(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
        from src.ui.equipment.equipment_result_rendering import _render_results

        app = QApplication.instance() or QApplication([])
        panel = QWidget()
        layout = QVBoxLayout(panel)
        owner = SimpleNamespace(result_card=panel, result_content_layout=layout)
        _render_results(owner, {
            "D": {
                "valid": False, "reason": "本次有界搜索未找到完整配装",
                "search_status": "bounded_unproven",
                "group_search": {"members": 6, "completed": 5},
            },
        })
        label = layout.itemAt(0).widget()
        self.assertIn("本次有界搜索未找到完整配装", label.text())
        self.assertIn("同级组已完成 5/6 人", label.text())
        self.assertEqual(label.text().count("\n"), 0)
        self.assertIsNotNone(app)

    def test_six_member_group_retains_five_without_missing_member(self):
        names = ("A", "B", "C", "D", "E", "F")
        roles = {
            name: {
                "default_set": "Set", "weights": {"攻击力%": 1.0},
                "board_matrix": [[1]], "crit_source_known": True,
                "non_equipment_crit_rate": 0.0,
            }
            for name in names
        }
        inventory = tuple(
            Drive(
                uid=f"synthetic-drive-{index}", quality="Gold", area=1,
                shape_id="X", set_name="Set",
                main_stats={"攻击力": 42, "生命值": 560},
                sub_stats={"攻击力%": 6.0 - index},
            )
            for index in range(5)
        )
        blueprints = {
            name: [{
                "set_pieces": [], "extra_pieces": ["Y" if name == "D" else "X"],
                "board": [], "set_effect_mode": "none",
            }]
            for name in names
        }
        request = AllocationKernelRequest(
            inventory=inventory, roles_db=roles,
            sets_db={"Set": {"shapes": ["X", "Y"]}}, shapes_db={},
            blueprints_db=blueprints, role_order=names,
            strategy="role_priority", module_set_targets={}, set_effect_modes={},
            core_main_filters={}, core_set_targets={}, stat_priority_configs={},
            property_limits={}, priority_groups=(names,), blueprint_combo_limit=40,
        )
        scorer = ScoringEngine(config_dir=ROOT / "config", roles_db=roles)
        execute = create_allocation_executor(
            static_database_path=ROOT / "data/game_static.sqlite3",
        )
        with_missing = execute(request, scorer)
        without_missing = execute(replace(
            request, role_order=tuple(name for name in names if name != "D"),
            priority_groups=(tuple(name for name in names if name != "D"),),
        ), scorer)
        complete = lambda plans: sum(bool(plan["valid"]) for plan in plans.values())
        self.assertEqual(complete(without_missing), 5)
        self.assertGreaterEqual(complete(with_missing), complete(without_missing))
        self.assertFalse(with_missing["D"]["valid"])
        self.assertEqual(with_missing["D"]["group_search"]["members"], 6)
        self.assertEqual(with_missing["D"]["group_search"]["completed"], 5)
        used = [
            item.uid for plan in with_missing.values() if plan["valid"]
            for item in (*plan.get("assigned_set_drives", ()),
                         *plan.get("assigned_extra_drives", ()))
        ]
        self.assertEqual(len(used), len(set(used)))


if __name__ == "__main__":
    unittest.main()
