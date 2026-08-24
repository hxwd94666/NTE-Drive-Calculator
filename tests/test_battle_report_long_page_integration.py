# 验证最终战报从逐击、配装冻结到长页分析模型的完整只读链路。
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.integrations.nte_core_battle import parse_battle_summary
from src.observability import OperationContext
from src.services.battle_report_history_service import BattleReportHistoryService
from src.services.battle_report_history_projection import analysis_scope_range
from src.services.battle_report_persistence_service import (
    BattleReportPersistenceDependencies,
    BattleReportPersistenceService,
)
from src.storage.sqlite.user_data_dao import UserDataDao


class BattleReportLongPageIntegrationTests(unittest.TestCase):
    def test_half_scope_resolves_complete_upper_and_lower_axis_ranges(self) -> None:
        evidence = {
            "hits": [
                {"relative_time_us": 0, "abyss_half": "upper"},
                {"relative_time_us": 68_000_000, "abyss_half": "upper"},
                {"relative_time_us": 76_000_000, "abyss_half": "lower"},
                {"relative_time_us": 255_000_000, "abyss_half": "lower"},
            ]
        }
        summary = {"abyss": {"active_half": "descending"}}

        self.assertEqual(
            (0, 76_000_000),
            analysis_scope_range(evidence, summary, "first"),
        )
        self.assertEqual(
            (76_000_000, 255_000_001),
            analysis_scope_range(evidence, summary, "second"),
        )
        self.assertEqual(
            (76_000_000, 255_000_001),
            analysis_scope_range(evidence, summary, "current"),
        )

    def test_finalized_axis_builds_formula_ready_history_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database_path = Path(temporary) / "user.sqlite3"
            static_path = Path("data/game_static.sqlite3").resolve()
            with UserDataDao(database_path, account_id="long-page") as dao:
                dao.import_inventory_snapshot(
                    {
                        "method": "event.inventory.snapshot",
                        "params": {
                            "complete": True,
                            "generation": 1,
                            "sequence": 1,
                            "observed_at_unix_ms": 1_787_000_000_000,
                            "item_count": 0,
                            "items": [],
                        },
                    }
                )
            dependencies = BattleReportPersistenceDependencies(
                account_id="long-page",
                user_database_path=database_path,
                generation=1,
                static_database_path=static_path,
            )
            persistence = BattleReportPersistenceService(
                dependencies=dependencies,
                context_is_current=lambda _dependencies: True,
                operation_context=OperationContext.create("battle_report"),
            )
            persistence.begin_capture(
                capture_operation_id="capture-long-page",
                captured_at_utc="2026-08-20T00:00:00+00:00",
            )
            persistence.append_axis_page(
                capture_operation_id="capture-long-page",
                page={
                    "contract_version": 1,
                    "battle_record_id": "battle-long-page",
                    "generation": "1",
                    "complete": True,
                    "total_hits": "1",
                    "retained_hits": 1,
                    "rows": [
                        {
                            "sequence": "1",
                            "timestamp_unix": 100.5,
                            "relative_time_seconds": 0.5,
                            "character_id": 1072,
                            "character_name": "灵可",
                            "character_known": True,
                            "direction": "outgoing",
                            "damage": 100.0,
                            "follow_up_damage": 0.0,
                            "total_damage": 100.0,
                            "ability_name": "GA_Test",
                            "damage_name": "测试伤害",
                            "damage_component": "skill",
                            "attack_type": "skill",
                            "damage_attribute": "nature",
                            "follow_up_labels": [],
                        }
                    ],
                },
            )
            payload = {
                "duration_seconds": 1.0,
                "dps_time_mode": "subtract_time_stop",
                "total_damage": 100.0,
                "total_dps": 100.0,
                "total_damage_taken": 0.0,
                "total_hits": 1,
                "characters": [
                    {
                        "char_id": 1072,
                        "name": "灵可",
                        "hits": 1,
                        "damage": 100.0,
                        "dps": 100.0,
                        "damage_share_percent": 100.0,
                    }
                ],
                "skills": [],
                "abyss": {"detected": False},
                "quality": {},
            }
            outcome = persistence.finalize_summary(
                raw_summary_payload=payload,
                summary=parse_battle_summary(payload),
                capture_operation_id="capture-long-page",
                captured_at_utc="2026-08-20T00:00:00+00:00",
                finalized_at_utc="2026-08-20T00:00:01+00:00",
                raw_record_payload={
                    "contract_version": 1,
                    "battle_record_id": "battle-long-page",
                    "generation": "1",
                    "axis_complete": True,
                    "axis_first_sequence": "1",
                    "axis_total_hits": "1",
                    "time_stop_intervals": [],
                },
            )
            assert outcome.battle_record_id is not None
            history = BattleReportHistoryService(
                dependencies=dependencies,
                context_is_current=lambda _dependencies: True,
            )
            analysis = history.load_analysis(outcome.battle_record_id)

            assert analysis is not None
            self.assertEqual(1, len(analysis.hits))
            self.assertEqual("GA_Test", analysis.hits[0].ability_id)
            self.assertEqual(1, len(analysis.inferred_actions))
            self.assertEqual("灵可", analysis.baselines[0].character_name)
            self.assertIn(
                "AtkBase",
                {row.property_id for row in analysis.baselines[0].stats},
            )
            self.assertEqual(
                "reconstructed_current_static",
                analysis.baselines[0].source,
            )

            editor_data = history.load_build_editor_data(outcome.battle_record_id)
            detail = editor_data["details"][0]
            self.assertIn("battle", detail["equipment_contexts"])
            self.assertIn("current", detail["equipment_contexts"])
            self.assertEqual("battle", detail["selected_equipment_context_key"])

            edited_profile = dict(detail["profile"])
            edited_profile.update(
                {
                    "equipment_context_key": "current",
                    "equipment_context_title": "游戏当前",
                    "equipment_source_kind": "role_page_current",
                    "equipment_override": [
                        {
                            "kind": "core",
                            "item_id": "Core_Test",
                            "uid_slot": 7,
                            "uid_serial": 11,
                            "quality": "orange",
                            "level": 80,
                            "max_level": 80,
                            "stats": [
                                {
                                    "stat_group": "main",
                                    "property_id": "AtkAdd",
                                    "value": 777.0,
                                    "is_percent": False,
                                }
                            ],
                        }
                    ],
                }
            )
            history.save_build_edit(outcome.battle_record_id, [edited_profile])
            edited_analysis = history.load_analysis(outcome.battle_record_id)

            assert edited_analysis is not None
            edited_baseline = edited_analysis.baselines[0]
            self.assertEqual(
                "user_edited_equipment_reconstructed",
                edited_baseline.source,
            )
            self.assertIn(
                ("equipment", "AtkAdd", 777.0),
                {
                    (row.source_group, row.property_id, row.value)
                    for row in edited_baseline.source_stats
                },
            )

            history.sync_role_page_to_build_edit(outcome.battle_record_id)
            with UserDataDao(
                database_path,
                account_id="long-page",
            ) as dao:
                synchronized = dao.load_battle_build_edit(
                    outcome.battle_record_id
                )
            assert synchronized is not None
            self.assertEqual(
                777.0,
                synchronized["characters"][0]["profile"]
                ["equipment_override"][0]["stats"][0]["value"],
            )

            with self.assertRaisesRegex(ValueError, "角色页当前空幕/驱动不可用"):
                history.sync_role_page_to_build_edit(
                    outcome.battle_record_id,
                    include_equipment=True,
                )


if __name__ == "__main__":
    unittest.main()
