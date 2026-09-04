# 验证完整目标数量和初始最大生命多重集只在唯一环境中投影身份。
from __future__ import annotations

import unittest
from pathlib import Path

from src.services.battle_inferred_target_condition_service import (
    INFERRED_ENCOUNTER_ALGORITHM_VERSION,
    BattleInferredTargetConditionService,
)
from src.services.battle_inferred_target_snapshot_service import (
    INFERRED_TARGET_SNAPSHOT_SCHEMA_VERSION,
    BattleInferredTargetSnapshotService,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


STATIC_DATABASE = Path("data/game_static.sqlite3")


def _hit(
    time_us: int,
    max_hp: float,
    target_id: str,
    half: str = "",
    monster_id: str = "",
) -> dict:
    return {
        "relative_time_us": time_us,
        "direction": "outgoing",
        "abyss_half": half,
        "target_id": target_id,
        "target_name": "",
        "target_monster_id": monster_id,
        "target_max_hp": max_hp,
    }


class BattleInferredTargetConditionServiceTests(unittest.TestCase):
    def test_versioned_snapshot_round_trips_complete_inference(self) -> None:
        inferred = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={
                "hits": (
                    _hit(
                        1,
                        1_450_710.0,
                        "enemy-wire:black-book",
                        monster_id="boss_09_BP_WorldBoss",
                    ),
                )
            },
            range_start_us=None,
            range_end_us=None,
        )

        assert inferred is not None
        row = {
            "inference_status": "resolved",
            "payload_schema_version": INFERRED_TARGET_SNAPSHOT_SCHEMA_VERSION,
            "algorithm_version": INFERRED_ENCOUNTER_ALGORITHM_VERSION,
            "static_dataset_id": "dataset-a",
            "static_schema_version": 29,
            "environment_kind": inferred.environment_kind,
            "environment_ref": inferred.environment_ref,
            "environment_name": inferred.environment_name,
            "source_kind": inferred.source_kind,
            "confidence": inferred.confidence,
            "inferred_payload": BattleInferredTargetSnapshotService.payload(
                inferred
            ),
        }
        restored = BattleInferredTargetSnapshotService.restore(
            row,
            static_dataset_id="dataset-a",
            static_schema_version=29,
        )

        self.assertEqual(inferred, restored)
        self.assertIsNone(
            BattleInferredTargetSnapshotService.restore(
                row,
                static_dataset_id="dataset-b",
                static_schema_version=29,
            )
        )

    def test_ui_range_does_not_hide_other_half_from_environment_recognition(self) -> None:
        inferred = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="abyss",
            floor=12,
            evidence={
                "hits": (
                    _hit(1, 2_924_242.0, "enemy-wire:reused", "upper"),
                    _hit(20, 2_924_249.0, "enemy-wire:reused", "lower"),
                )
            },
            range_start_us=0,
            range_end_us=10,
        )

        assert inferred is not None
        self.assertEqual("", inferred.scope_half)
        self.assertTrue(inferred.environment_ref.endswith("|mixed"))
        self.assertEqual(2, len(inferred.identities))
        self.assertEqual(
            {"upper", "lower"},
            {row.scope_half for row in inferred.identities},
        )

    def test_world_boss_black_book_matches_without_general_open_world(self) -> None:
        inferred = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={
                "hits": (
                    _hit(
                        1,
                        1_450_710.0,
                        "enemy-wire:black-book",
                        monster_id="boss_09_BP_WorldBoss",
                    ),
                )
            },
            range_start_us=0,
            range_end_us=10,
        )

        assert inferred is not None
        self.assertEqual("open_world", inferred.environment_kind)
        self.assertEqual("异象追猎 · 黑之书 · Lv.80", inferred.environment_name)
        self.assertEqual("黑之书", inferred.identities[0].target_name)

    def test_same_hp_world_bosses_default_black_book_and_retain_ambiguity(self) -> None:
        inferred = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={
                "hits": (_hit(1, 1_450_710.0, "enemy-wire:ambiguous"),),
            },
            range_start_us=0,
            range_end_us=10,
        )

        assert inferred is not None
        self.assertEqual("异象追猎 · 黑之书 · Lv.80", inferred.environment_name)
        self.assertEqual("低", inferred.confidence)
        self.assertTrue(inferred.ambiguous)
        self.assertEqual("ambiguous_default", inferred.selection_mode)
        self.assertEqual(
            ("异象追猎 · 无首铁驭 · Lv.80",),
            inferred.ambiguity_alternatives,
        )
        self.assertIn("严格候选不唯一", inferred.inference_basis)

    def test_material_clone_uses_full_encounter_instead_of_ui_range(self) -> None:
        hits = (
            _hit(1, 2_443.0, "enemy-wire:1"),
            _hit(2, 2_443.0, "enemy-wire:2"),
            _hit(3, 2_036.0, "enemy-wire:3"),
            _hit(4, 2_036.0, "enemy-wire:4"),
        )
        narrow = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={"hits": hits},
            range_start_us=0,
            range_end_us=2,
        )
        full = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={"hits": hits},
            range_start_us=0,
            range_end_us=100,
        )

        assert narrow is not None and full is not None
        self.assertEqual(full.environment_ref, narrow.environment_ref)
        self.assertEqual("clone", narrow.environment_kind)
        self.assertIn("Trailclone_exp|3", narrow.environment_ref)
        self.assertIn("合订本", narrow.environment_name)

    def test_feast_identity_defaults_hp_and_highest_attack_resistance(self) -> None:
        inferred = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={
                "hits": (
                    _hit(
                        1,
                        6_099_744.0,
                        "enemy-wire:feast",
                        monster_id="boss_05_BP_DiyBoss",
                    ),
                )
            },
            range_start_us=0,
            range_end_us=10,
        )

        assert inferred is not None
        self.assertEqual("feast", inferred.environment_kind)
        self.assertEqual("DiyBossStage8", inferred.environment_ref)
        self.assertEqual("墨菲克斯", inferred.identities[0].target_name)
        self.assertEqual(1, inferred.difficulty_id)
        options = dict(inferred.feast_options)
        self.assertEqual("LifeOP001_challenge", options["1"])
        self.assertEqual("Attack003_challenge", options["2"])
        self.assertEqual("LightOP003_challenge", options["3"])
        self.assertEqual("HunOP003_challenge", options["4"])
        self.assertEqual("XiangOP003_challenge", options["5"])
        assert inferred.target_condition is not None
        resistances = dict(inferred.target_condition.resistances)
        self.assertGreaterEqual(resistances["cosmos"], 0.3)
        self.assertGreaterEqual(resistances["psyche"], 0.3)
        self.assertGreaterEqual(resistances["lakshana"], 0.3)

    def test_mixed_halves_resolve_one_season_without_merging_target_profiles(self) -> None:
        mixed = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="abyss",
            floor=12,
            evidence={
                "hits": (
                    _hit(1, 2_924_242.0, "enemy-wire:reused", "upper"),
                    _hit(2, 2_924_249.0, "enemy-wire:reused", "lower"),
                )
            },
            range_start_us=0,
            range_end_us=10,
        )

        assert mixed is not None
        self.assertEqual("Abyss_8|12|mixed", mixed.environment_ref)
        self.assertEqual("", mixed.scope_half)
        self.assertIsNone(mixed.target_condition)
        self.assertEqual(
            {"upper", "lower"},
            {half for half, _condition in mixed.target_conditions_by_half},
        )
        self.assertEqual(2, len(mixed.identities))

    def test_battle_time_keeps_current_season_when_one_half_target_mapping_conflicts(self) -> None:
        inferred = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="abyss",
            floor=10,
            evidence={
                "hits": (
                    _hit(1, 2_906_977.0, "enemy-wire:upper-boss-1", "upper"),
                    _hit(2, 2_906_977.0, "enemy-wire:upper-boss-2", "upper"),
                    _hit(3, 894_455.0, "enemy-wire:upper-small", "upper"),
                    _hit(4, 2_906_977.0, "enemy-wire:lower-boss", "lower"),
                    _hit(5, 521_765.0, "enemy-wire:lower-small-1", "lower"),
                    _hit(6, 521_765.0, "enemy-wire:lower-small-2", "lower"),
                    _hit(7, 521_765.0, "enemy-wire:lower-small-3", "lower"),
                    _hit(8, 521_765.0, "enemy-wire:lower-small-4", "lower"),
                )
            },
            range_start_us=None,
            range_end_us=None,
            battle_occurred_at_utc="2026-09-04T08:16:46.743+00:00",
        )

        assert inferred is not None
        self.assertEqual("Abyss_9|10|mixed", inferred.environment_ref)
        self.assertIn("幽语环线", inferred.environment_name)
        self.assertEqual("battle_time_partial_mixed", inferred.selection_mode)
        self.assertEqual("中", inferred.confidence)
        self.assertFalse(inferred.ambiguous)
        self.assertEqual(
            {"lower"},
            {half for half, _condition in inferred.target_conditions_by_half},
        )
        self.assertIn("上半目标映射仍冲突", inferred.inference_basis)
        self.assertIn("2026-09-04 16:16:46", inferred.inference_basis)

    def test_non_unique_signature_does_not_match(self) -> None:
        unknown = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={"hits": (_hit(1, 123_456_789.0, "enemy-wire:x"),)},
            range_start_us=0,
            range_end_us=10,
        )

        self.assertIsNone(unknown)

    def test_static_catalog_limits_rotations_and_exposes_all_feast_stages(self) -> None:
        with StaticGameDataDao(STATIC_DATABASE) as static_dao:
            outer = static_dao.list_outer_realm_configs()
            feast = static_dao.list_feast_stages()

        self.assertEqual(["Abyss_8", "Abyss_9"], [row["level_config_id"] for row in outer])
        self.assertEqual([12, 12], [row["max_level"] for row in outer])
        self.assertEqual(
            ["2026-08-21T05:00:00", "2026-09-04T05:00:00"],
            [row["starts_at_mainland"] for row in outer],
        )
        self.assertEqual(
            {f"DiyBossStage{index}" for index in range(1, 9)},
            {row["stage_id"] for row in feast},
        )

    def test_formal_identity_can_select_stage3_from_all_feast_candidates(self) -> None:
        inferred = BattleInferredTargetConditionService.infer(
            static_database_path=STATIC_DATABASE,
            combat_context_kind="non_abyss",
            floor=None,
            evidence={
                "hits": (
                    _hit(
                        1,
                        4_498_005.0,
                        "enemy-wire:boss-07",
                        monster_id="Boss_07_BP_DiyBoss",
                    ),
                )
            },
            range_start_us=0,
            range_end_us=10,
        )

        assert inferred is not None
        self.assertEqual("DiyBossStage3", inferred.environment_ref)
        self.assertEqual("塞润尼缇", inferred.identities[0].target_name)
        self.assertEqual("unique_hard", inferred.selection_mode)

if __name__ == "__main__":
    unittest.main()
