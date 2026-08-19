# 验证逐击轴持久化和战后按出场角色物化游戏当前配装。
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

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
                "contract_version": 1,
                "battle_record_id": "battle-1",
                "generation": "3",
                "complete": True,
                "first_available_cursor": "1",
                "next_cursor": "2",
                "total_hits": "1",
                "retained_hits": 1,
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
                        "follow_up_damage": 0.0,
                        "total_damage": 100.0,
                        "follow_up_labels": [],
                    }
                ],
            },
        )
        record_id = self._insert_summary(operation_id)
        result = self.dao.finalize_battle_axis_capture(
            capture_operation_id=operation_id,
            battle_record_id=record_id,
            record={
                "contract_version": 1,
                "battle_record_id": "battle-1",
                "generation": "4",
                "axis_complete": True,
                "axis_first_sequence": "1",
                "axis_total_hits": "1",
                "time_stop_intervals": [],
            },
            observed_characters={},
            source_inventory_snapshot_id=snapshot_id,
            static_dataset_id="fixture",
            static_schema_version=17,
            character_profiles={
                1072: _profile(1072, "fork_GoldRecord"),
                1075: _profile(1075, "fork_worldrain"),
            },
            finalized_at_utc="2026-08-19T00:00:10+00:00",
        )
        build = self.dao.load_battle_build_snapshot(record_id)
        history = self.dao.load_battle_record(record_id)

        self.assertEqual(1, result["stored_hits"])
        self.assertIsNotNone(build)
        assert build is not None and history is not None
        self.assertEqual([1072], [row["character_id"] for row in build["characters"]])
        self.assertEqual(
            "fork_GoldRecord",
            build["characters"][0]["profile"]["fork_id"],
        )
        self.assertEqual(2, len(build["characters"][0]["equipment"]))
        self.assertEqual("hit_axis", history["capability_level"])
        self.assertTrue(history["axis_complete"])

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
