# 验证逐击轴持久化和战后按出场角色物化游戏当前配装。
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.battle_build_stage_support import (
    normalize_frozen_advancement,
)
from src.storage.sqlite.user_data_dao import UserDataDao


def _stat(property_id: str, value: float) -> dict:
    return {
        "property_id": property_id,
        "value": value,
        "percent": True,
        "names": {"zh_cn": property_id},
    }


def _equipped_item(
    serial: int,
    slot: int,
    character_id: int,
    *,
    kind: str = "module",
) -> dict:
    return {
        "uid": {"serial": serial, "slot": slot},
        "kind": kind,
        "item_id": (
            "cell3_style1_1_Orange" if kind == "module" else "Nature_orange"
        ),
        "suit_id": "Suit1",
        "geometry": "ZhiJiao1" if kind == "module" else "Core",
        "grid": 3 if kind == "module" else None,
        "quality": "orange",
        "level": 20,
        "max_level": 20,
        "locked": False,
        "discarded": False,
        "equipped": True,
        "equipped_character_uid": {"serial": 9001, "slot": 9002},
        "equipped_character_id": character_id,
        "equipped_placement": (
            {"row": 2, "column": 3} if kind == "module" else None
        ),
        "names": {"zh_cn": "测试装备"},
        "suit_names": {"zh_cn": "测试套装"},
        "main_stats": [_stat("DamageUpNatureBase", 0.2)],
        "sub_stats": [_stat("CritBase", 0.03)],
    }


def _snapshot(generation: int, items: list[dict]) -> dict:
    return {
        "method": "event.inventory.snapshot",
        "params": {
            "complete": True,
            "generation": generation,
            "sequence": generation,
            "observed_at_unix_ms": 1_787_000_000_000 + generation,
            "item_count": len(items),
            "items": items,
        },
    }


def _profile(character_id: int, fork_id: str) -> dict:
    return {
        "character_id": character_id,
        "character_level": 80,
        "breakthrough_stage": 6,
        "awakening_level": 6,
        "fork_id": fork_id,
        "fork_level": 80,
        "fork_breakthrough_stage": 6,
        "fork_refinement_level": 1,
        "selected_skill_id": "GA_Test",
        "skill_levels": {"GA_Test": 9},
        "ordinal": 0,
        "profile_source": "account_role_page",
    }


class BattleAxisDaoTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary.name) / "user.sqlite3"
        self.dao = UserDataDao(self.database_path, account_id="axis-account")

    def tearDown(self) -> None:
        self.dao.close()
        self.temporary.cleanup()

    def test_freeze_keeps_zero_stage_at_first_cap(self) -> None:
        normalized = normalize_frozen_advancement(
            {
                "character_level": 20,
                "breakthrough_stage": 0,
                "fork_id": "fork_Test",
                "fork_level": 20,
                "fork_breakthrough_stage": 0,
            },
            1072,
        )

        self.assertEqual((20, 0), normalized[:2])
        self.assertEqual(("fork_Test", 20, 0), normalized[2:5])
        self.assertEqual(0, normalized[5]["fork_breakthrough_stage"])

    def _insert_summary(self, operation_id: str) -> int:
        payload = {"total_damage": 100.0, "total_hits": 1}
        raw_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        result = self.dao.insert_auto_summary_snapshot(
            capture_operation_id=operation_id,
            combat_context_kind="non_abyss",
            abyss_floor=None,
            has_first_half=False,
            has_second_half=False,
            captured_at_utc="2026-08-19T00:00:00+00:00",
            finalized_at_utc="2026-08-19T00:00:10+00:00",
            dps_time_mode="subtract_time_stop",
            duration_seconds=10.0,
            total_damage=100.0,
            total_dps=10.0,
            total_damage_taken=0.0,
            total_hits=1,
            character_count=1,
            skill_count=1,
            character_ids=[1072],
            abyss_detected=False,
            abyss_success=False,
            payload_schema_version=1,
            raw_summary_json=raw_json,
            raw_summary_sha256=hashlib.sha256(raw_json.encode()).hexdigest(),
        )
        return int(result["record"]["battle_record_id"])

    def test_target_condition_is_replaceable_and_deleted_with_record(self) -> None:
        record_id = self._insert_summary("target-condition")
        condition = {
            "target_name": "墨菲斯托",
            "enemy_level": 90,
            "scene": "outer_realm",
            "enemy_defense_base": 1050.0,
            "enemy_defense_up": 0.0,
            "enemy_defense_add": 0.0,
            "defense_reduction": 0.0,
            "vulnerability": 0.0,
            "resistances": {
                "normal": 0.28,
                "chaos": 0.2,
                "cosmos": 0.5,
                "incantation": 0.2,
                "lakshana": 0.5,
                "nature": 0.2,
                "psyche": 0.5,
                "psychically": 0.2,
            },
        }

        saved = self.dao.save_battle_target_condition(record_id, condition)
        condition["vulnerability"] = 0.15
        replaced = self.dao.save_battle_target_condition(record_id, condition)

        self.assertEqual(0.5, saved["resistances"]["cosmos"])
        self.assertEqual(0.28, saved["resistances"]["normal"])
        self.assertEqual(1050.0, saved["enemy_defense_base"])
        self.assertEqual(0.15, replaced["vulnerability"])
        self.assertTrue(self.dao.delete_battle_record(record_id))
        self.assertIsNone(self.dao.load_battle_target_condition(record_id))

    def test_target_condition_retains_environment_options_and_witch_buff(self) -> None:
        record_id = self._insert_summary("structured-target-condition")
        condition = {
            "target_name": "争锋赏宴·愿望成真",
            "enemy_level": 90,
            "scene": "outer_realm",
            "enemy_defense_base": 1050.0,
            "enemy_defense_up": 0.0,
            "enemy_defense_add": 0.0,
            "enemy_topple_limit": 70.0,
            "defense_reduction": 0.0,
            "vulnerability": 0.0,
            "resistances": {
                "normal": 0.28,
                "chaos": 0.2,
                "cosmos": 0.5,
                "incantation": 0.2,
                "lakshana": 0.5,
                "nature": 0.2,
                "psyche": 0.5,
                "psychically": 0.2,
            },
            "environment_kind": "feast",
            "environment_ref": "DiyBossStage8",
            "selected_target_ids": ["boss_05_BP_DiyBoss"],
            "selected_target_profiles": [{
                "static_target_id": "boss_05_BP_DiyBoss",
                "selection_target_id": "boss_05_BP_DiyBoss",
                "target_name": "争锋赏宴·愿望成真",
                "monster_class_path": "boss_05_BP_DiyBoss",
                "monster_count": 1,
                "max_hp": 123456.0,
                "monster_level": 90.0,
                "defense_base": 1050.0,
                "defense_up": 0.0,
                "defense_add": 0.0,
                "topple_limit": 70.0,
                "resistances": {"normal": 0.28, "chaos": 0.2},
                "profile_set": "boss",
                "pack_id": "difficulty-4",
            }],
            "primary_target_id": "boss_05_BP_DiyBoss",
            "difficulty_id": 4,
            "feast_options": {"4": "HunOP003_challenge"},
            "witch_buff_id": "Buff_Divination_DamageUpGeneralBase",
            "witch_buff_name_zh": "通用伤害提升15%",
            "witch_buff_property_id": "DamageUpGeneralBase",
            "witch_buff_value": 0.15,
            "witch_buff_is_percent": True,
        }

        saved = self.dao.save_battle_target_condition(record_id, condition)

        self.assertEqual("feast", saved["environment_kind"])
        self.assertEqual(["boss_05_BP_DiyBoss"], saved["selected_target_ids"])
        self.assertEqual(
            "boss_05_BP_DiyBoss",
            saved["selected_target_profiles"][0]["static_target_id"],
        )
        self.assertEqual(123456.0, saved["selected_target_profiles"][0]["max_hp"])
        self.assertEqual({"4": "HunOP003_challenge"}, saved["feast_options"])
        self.assertEqual("DamageUpGeneralBase", saved["witch_buff_property_id"])
        self.assertEqual(0.15, saved["witch_buff_value"])
        self.assertTrue(saved["witch_buff_is_percent"])

    def test_final_axis_replacement_is_atomic_and_preserves_v4_max_hp(self) -> None:
        operation_id = "final-axis-replacement"
        self.dao.begin_battle_axis_capture(
            capture_operation_id=operation_id,
            captured_at_utc="2026-08-24T00:00:00+00:00",
            account_generation=1,
        )
        old_page = {
            "contract_version": 4,
            "battle_record_id": "battle-final",
            "generation": "1",
            "complete": False,
            "first_available_cursor": "1",
            "next_cursor": "2",
            "total_hits": "1",
            "retained_hits": 1,
            "rows": [{
                "sequence": "1",
                "direction": "outgoing",
                "damage": 1.0,
                "overkill_damage": 0.0,
                "max_hp_reduction": 0.0,
                "follow_up_labels": [],
            }],
        }
        self.dao.append_battle_axis_page(
            capture_operation_id=operation_id,
            page=old_page,
        )
        final_page = {
            **old_page,
            "generation": "2",
            "complete": True,
            "next_cursor": None,
            "rows": [{
                **old_page["rows"][0],
                "sequence": "8",
                "damage": 500.0,
                "max_hp_reduction": 100.0,
            }],
        }

        result = self.dao.replace_staged_battle_axis(
            capture_operation_id=operation_id,
            pages=(final_page,),
            source_generation="2",
        )
        connection = self.dao._db()
        rows = connection.execute(
            """
            SELECT sequence_text, max_hp_reduction
            FROM battle_hit_evidence
            """
        ).fetchall()

        self.assertTrue(result["complete"])
        self.assertEqual(1, result["stored_hits"])
        self.assertEqual("8", rows[0]["sequence_text"])
        self.assertEqual(100.0, rows[0]["max_hp_reduction"])

        invalid_page = {**final_page, "generation": "3"}
        with self.assertRaisesRegex(ValueError, "different generation|不同 generation"):
            self.dao.replace_staged_battle_axis(
                capture_operation_id=operation_id,
                pages=(invalid_page,),
                source_generation="2",
            )
        remaining = connection.execute(
            "SELECT sequence_text FROM battle_hit_evidence"
        ).fetchall()
        self.assertEqual(["8"], [row["sequence_text"] for row in remaining])

    def test_incomplete_final_axis_preserves_live_staged_hits(self) -> None:
        operation_id = "incomplete-final-axis"
        self.dao.begin_battle_axis_capture(
            capture_operation_id=operation_id,
            captured_at_utc="2026-08-24T00:00:00+00:00",
            account_generation=1,
        )
        self.dao.append_battle_axis_page(
            capture_operation_id=operation_id,
            page={
                "contract_version": 4,
                "battle_record_id": "battle-incomplete",
                "generation": "7",
                "complete": True,
                "first_available_cursor": "1",
                "next_cursor": None,
                "total_hits": "1",
                "retained_hits": 1,
                "rows": [{
                    "sequence": "1",
                    "direction": "outgoing",
                    "damage": 10.0,
                    "overkill_damage": 0.0,
                    "max_hp_reduction": 0.0,
                    "follow_up_labels": [],
                }],
            },
        )

        result = self.dao.replace_staged_battle_axis(
            capture_operation_id=operation_id,
            pages=(),
            source_generation="8",
            incomplete_reason="final_axis_incomplete",
        )
        connection = self.dao._db()
        rows = connection.execute(
            "SELECT sequence_text FROM battle_hit_evidence"
        ).fetchall()
        capture = connection.execute(
            """
            SELECT axis_complete, stored_hits, source_generation,
                   finalization_incomplete_reason
            FROM battle_axis_capture
            WHERE capture_operation_id = ?
            """,
            (operation_id,),
        ).fetchone()

        self.assertFalse(result["complete"])
        self.assertEqual(1, result["stored_hits"])
        self.assertEqual(["1"], [row["sequence_text"] for row in rows])
        self.assertEqual(0, capture["axis_complete"])
        self.assertEqual(1, capture["stored_hits"])
        self.assertEqual("8", capture["source_generation"])
        self.assertEqual(
            "final_axis_incomplete",
            capture["finalization_incomplete_reason"],
        )

    def test_materializes_only_observed_character_from_post_battle_snapshot(self) -> None:
        operation_id = "axis-operation"
        self.dao.begin_battle_axis_capture(
            capture_operation_id=operation_id,
            captured_at_utc="2026-08-19T00:00:00+00:00",
            account_generation=4,
        )
        snapshot_id = self.dao.import_inventory_snapshot(
            _snapshot(
                10,
                [
                    _equipped_item(101, 11, 1072),
                    _equipped_item(102, 12, 1072, kind="core"),
                    _equipped_item(201, 21, 1075),
                ],
            )
        )
        self.dao.append_battle_axis_page(
            capture_operation_id=operation_id,
            page={
                "contract_version": 3,
                "battle_record_id": "battle-1",
                "generation": "3",
                "complete": True,
                "first_available_cursor": "1",
                "next_cursor": "3",
                "total_hits": "2",
                "retained_hits": 2,
                "rows": [
                    {
                        "sequence": "1",
                        "timestamp_unix": 1_787_000_000.0,
                        "relative_time_seconds": 1.25,
                        "character_id": 1072,
                        "character_name": "灵可",
                        "character_known": True,
                        "direction": "outgoing",
                        "damage": 100.0,
                        "overkill_damage": 10.0,
                        "follow_up_damage": 0.0,
                        "total_damage": 100.0,
                        "follow_up_labels": [],
                    },
                    {
                        "sequence": "2",
                        "timestamp_unix": 1_787_000_000.1,
                        "relative_time_seconds": 1.35,
                        "character_id": 0,
                        "character_name": "未归因",
                        "character_known": False,
                        "direction": "outgoing",
                        "damage": 5.0,
                        "overkill_damage": 0.0,
                        "follow_up_damage": 0.0,
                        "total_damage": 5.0,
                        "damage_name": "Server settlement residual",
                        "follow_up_labels": [],
                    },
                ],
            },
        )
        record_id = self._insert_summary(operation_id)
        result = self.dao.finalize_battle_axis_capture(
            capture_operation_id=operation_id,
            battle_record_id=record_id,
            record={
                "contract_version": 3,
                "battle_record_id": "battle-1",
                "generation": "4",
                "axis_complete": True,
                "axis_first_sequence": "1",
                "axis_total_hits": "2",
                "time_stop_intervals": [
                    {
                        "start_offset_seconds": 2.5,
                        "end_offset_seconds": 4.0,
                        "pause_type_mask": 1 << 6,
                    }
                ],
            },
            observed_characters={},
            source_inventory_snapshot_id=snapshot_id,
            static_dataset_id="fixture",
            static_schema_version=17,
            character_profiles={
                1072: _profile(1072, "fork_GoldRecord"),
                1075: _profile(1075, "fork_worldrain"),
            },
            character_stat_snapshots={
                1072: [
                    {
                        "source_group": "character",
                        "property_id": "AtkBase",
                        "display_name": "基础攻击力",
                        "value": 900.0,
                        "is_percent": False,
                        "ordinal": 0,
                    },
                    {
                        "source_group": "fork",
                        "property_id": "AtkBase",
                        "display_name": "基础攻击力",
                        "value": 100.0,
                        "is_percent": False,
                        "ordinal": 0,
                    },
                    {
                        "source_group": "equipment",
                        "property_id": "AtkUp",
                        "display_name": "攻击力提升",
                        "value": 0.25,
                        "is_percent": True,
                        "ordinal": 0,
                    },
                    {
                        "source_group": "resolved",
                        "property_id": "CritDamageBase",
                        "display_name": "暴击伤害",
                        "value": 0.9,
                        "is_percent": True,
                        "ordinal": 0,
                    }
                ]
            },
            finalized_at_utc="2026-08-19T00:00:10+00:00",
        )
        build = self.dao.load_battle_build_snapshot(record_id)
        axis = self.dao.load_battle_axis_evidence(record_id)
        history = self.dao.load_battle_record(record_id)

        self.assertEqual(2, result["stored_hits"])
        self.assertIsNotNone(build)
        assert build is not None and history is not None
        self.assertEqual([1072], [row["character_id"] for row in build["characters"]])
        self.assertEqual(
            "fork_GoldRecord",
            build["characters"][0]["profile"]["fork_id"],
        )
        self.assertEqual(
            6,
            build["characters"][0]["fork_breakthrough_stage"],
        )
        self.assertEqual(
            6,
            build["characters"][0]["profile"]["fork_breakthrough_stage"],
        )
        self.assertEqual(2, len(build["characters"][0]["equipment"]))
        stat_keys = {
            (row["source_group"], row["property_id"])
            for row in build["characters"][0]["stats"]
        }
        self.assertIn(("character", "AtkBase"), stat_keys)
        self.assertIn(("fork", "AtkBase"), stat_keys)
        self.assertIn(("equipment", "AtkUp"), stat_keys)
        self.assertIn(("resolved", "CritDamageBase"), stat_keys)
        self.assertEqual(
            "frozen_v30",
            build["characters"][0]["stat_snapshot_source"],
        )
        self.assertEqual("battle-counterfactual-v3", build["formula_model_version"])
        self.assertEqual("hit_axis", history["capability_level"])
        self.assertTrue(history["axis_complete"])
        assert axis is not None
        self.assertEqual(10.0, axis["hits"][0]["overkill_damage"])
        self.assertIsNone(axis["hits"][1]["character_id"])
        self.assertEqual(
            {
                "start_offset_seconds": 2.5,
                "end_offset_seconds": 4.0,
                "pause_type_mask": 1 << 6,
            },
            axis["time_stop_intervals"][0]["raw_interval"],
        )
        self.assertEqual(1_500_000, axis["time_stop_intervals"][0]["duration_us"])
        self.assertEqual(1 << 6, axis["time_stop_intervals"][0]["pause_type_mask"])

    def test_in_progress_capture_does_not_pin_pre_battle_snapshot(self) -> None:
        first = self.dao.import_inventory_snapshot(
            _snapshot(1, [_equipped_item(1, 1, 1072)])
        )
        self.dao.import_inventory_snapshot(_snapshot(2, []))
        self.dao.begin_battle_axis_capture(
            capture_operation_id="protected-axis",
            captured_at_utc="2026-08-19T00:00:00+00:00",
            account_generation=1,
        )

        pruned = self.dao.prune_inventory_snapshots(retain_recent=1)
        self.dao.discard_battle_axis_capture("protected-axis")

        self.assertIn(first, pruned["deleted_snapshot_ids"])


if __name__ == "__main__":
    unittest.main()
