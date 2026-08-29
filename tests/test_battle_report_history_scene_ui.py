# 验证历史战报场景列区分推断、确认与原始上下文回退。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleReportHistoryEntry
from src.features.battle_report.history_dialog import _scene_label


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
