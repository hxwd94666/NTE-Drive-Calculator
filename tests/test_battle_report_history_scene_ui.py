# 验证历史战报场景列区分推断、确认与原始上下文回退。
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PySide6.QtWidgets import QApplication, QPushButton

from src.domain.battle_report import BattleReportHistoryEntry
from src.features.battle_report.history_dialog import BattleReportHistoryDialog, _scene_label


def _entry(**changes) -> BattleReportHistoryEntry:
    values = {
        "battle_record_id": 1,
        "retention_kind": "auto",
        "saved_at_utc": "2026-08-29T00:00:00+00:00",
        "combat_context_kind": "non_abyss",
        "abyss_floor": None,
        "has_first_half": False,
        "has_second_half": False,
        "character_ids": (),
        "total_damage": 100.0,
        "total_dps": 10.0,
        "duration_seconds": 10.0,
        "total_hits": 1,
        "capability_level": "hit_axis",
        "source_kind": "nte_core",
    }
    values.update(changes)
    return BattleReportHistoryEntry(**values)


class BattleReportHistorySceneUiTests(unittest.TestCase):
    def test_history_shows_record_id_and_type_and_keeps_actions_bound_to_record(self) -> None:
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory() as root:
            dialog = BattleReportHistoryDialog(game_ui_asset_root=Path(root))
            viewed = []
            deleted = []
            retained = []
            dialog.view_requested.connect(viewed.append)
            dialog.delete_requested.connect(deleted.append)
            dialog.retention_toggle_requested.connect(lambda rid, kind: retained.append((rid, kind)))
            dialog.set_entries((
                _entry(battle_record_id=82, native_capture=True),
                _entry(battle_record_id=81),
            ))
            self.assertEqual("ID", dialog.table.horizontalHeaderItem(0).text())
            self.assertEqual("82", dialog.table.item(0, 0).text())
            self.assertEqual("完整", dialog.table.item(0, 1).text())
            self.assertEqual("部分", dialog.table.item(1, 1).text())
            for row in (0, 1):
                for button in dialog.table.cellWidget(row, 7).findChildren(QPushButton):
                    button.click()
            self.assertEqual([82, 81], viewed)
            self.assertEqual([82, 81], deleted)
            self.assertEqual([(82, "auto"), (81, "auto")], retained)
            dialog.close()
            dialog.deleteLater()
            app.processEvents()

    def test_inferred_environment_is_labeled_without_changing_raw_context(self) -> None:
        entry = _entry(
            environment_name="异象追猎 · 黑之书 · Lv.80",
            environment_source="inferred",
            environment_confidence="高",
        )

        self.assertEqual(
            "异象追猎 · 黑之书 · Lv.80（推断）",
            _scene_label(entry),
        )
        self.assertEqual("non_abyss", entry.combat_context_kind)

    def test_confirmed_and_unknown_environment_keep_distinct_labels(self) -> None:
        self.assertEqual(
            "大世界 · 墨菲斯托（已确认）",
            _scene_label(_entry(
                environment_name="大世界 · 墨菲斯托",
                environment_source="user_confirmed",
            )),
        )
        self.assertEqual("未知场景", _scene_label(_entry()))


if __name__ == "__main__":
    unittest.main()
