# 验证 nte-core battle_record_v1 与 battle_axis_v1 的公开字段边界。
from __future__ import annotations

import unittest

from src.integrations.nte_core import NteCoreProtocolError
from src.integrations.nte_core_battle import parse_battle_axis, parse_battle_record


class NteCoreBattleAxisTests(unittest.TestCase):
    def test_record_preserves_decimal_identity_and_explicit_completeness(self) -> None:
        record = parse_battle_record(
            {
                "contract_version": 1,
                "battle_record_id": "battle-1",
                "capture_operation_id": "capture-1",
                "team_snapshot_id": None,
                "generation": "9007199254740993",
                "state": "finalized",
                "source": "capture",
                "started_at_unix": 100.0,
                "ended_at_unix": 110.0,
                "finalized_at_unix_ms": 110000,
                "axis_complete": False,
                "axis_first_sequence": "7",
                "axis_total_hits": "9",
                "time_stop_intervals": [],
                "abyss": {},
                "summary": {"total_damage": 1.0},
                "quality": {},
            }
        )

        self.assertEqual("9007199254740993", record["generation"])
        self.assertEqual("7", record["axis_first_sequence"])
        self.assertFalse(record["axis_complete"])
        self.assertIsNone(record["team_snapshot_id"])

    def test_axis_normalizes_hit_without_inventing_unsupported_facts(self) -> None:
        page = parse_battle_axis(
            {
                "contract_version": 1,
                "battle_record_id": "battle-1",
                "generation": "2",
                "finalized": False,
                "complete": True,
                "first_available_cursor": "1",
                "cursor": "1",
                "next_cursor": "2",
                "total_hits": "1",
                "retained_hits": 1,
                "rows": [
                    {
                        "battle_record_id": "battle-1",
                        "sequence": "1",
                        "timestamp_unix": 100.0,
                        "relative_time_seconds": 0.5,
                        "abyss_half": None,
                        "character_id": 1072,
                        "character_name": "灵可",
                        "character_known": True,
                        "character_source": "packet",
                        "attribution_status": "known",
                        "attribution_source": "packet",
                        "attribution_unknown_reason": None,
                        "team_snapshot_id": None,
                        "direction": "outgoing",
                        "damage": 10.0,
                        "follow_up_damage": 2.0,
                        "total_damage": 12.0,
                        "follow_up_timestamp_unix": 100.1,
                        "target_id": 123,
                        "target_name": None,
                        "target_name_en": None,
                        "target_name_ja": None,
                        "target_monster_id": None,
                        "target_context": None,
                        "target_hp_before": 100.0,
                        "target_hp_after": 88.0,
                        "target_max_hp": 100.0,
                        "target_hp_percent": 88.0,
                        "gameplay_effect_index": 99,
                        "gameplay_effect_name": "GE_Test",
                        "ability_name": "GA_Test",
                        "damage_name": "测试伤害",
                        "damage_component": "skill",
                        "attack_type": "skill",
                        "damage_attribute": "nature",
                        "follow_up_labels": ["追击"],
                    }
                ],
            }
        )

        hit = page["rows"][0]
        self.assertEqual("1", hit["sequence"])
        self.assertEqual("123", hit["target_id"])
        self.assertEqual(["追击"], hit["follow_up_labels"])
        self.assertNotIn("critical", hit)
        self.assertNotIn("buffs", hit)

    def test_axis_rejects_non_decimal_sequence(self) -> None:
        with self.assertRaises(NteCoreProtocolError):
            parse_battle_axis(
                {
                    "contract_version": 1,
                    "battle_record_id": "battle-1",
                    "generation": "1",
                    "rows": [
                        {
                            "battle_record_id": "battle-1",
                            "sequence": "1.5",
                            "timestamp_unix": 100.0,
                            "relative_time_seconds": 0.5,
                            "direction": "outgoing",
                        }
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
