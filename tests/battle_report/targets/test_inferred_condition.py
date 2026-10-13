# 验证完整目标数量和初始最大生命多重集只在唯一环境中投影身份。
from __future__ import annotations

import unittest
from pathlib import Path

from src.domain.battle_page_models import INFERRED_ENCOUNTER_ALGORITHM_VERSION
from src.services.battle_report.targets.inferred_condition import BattleInferredTargetConditionService
from src.services.battle_report.targets.inferred_snapshot import (
    INFERRED_TARGET_SNAPSHOT_SCHEMA_VERSION,
    BattleInferredTargetSnapshotService,
)


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
                    _hit(1, 3_570_949.0, "enemy-wire:reused", "upper"),
                    _hit(20, 3_570_949.0, "enemy-wire:reused", "lower"),
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
                    _hit(1, 3_570_949.0, "enemy-wire:reused", "upper"),
                    _hit(2, 3_570_949.0, "enemy-wire:reused", "lower"),
                )
            },
            range_start_us=0,
            range_end_us=10,
        )

        assert mixed is not None
        self.assertEqual("Abyss_10|12|mixed", mixed.environment_ref)
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
                    _hit(1, 999_999.0, "enemy-wire:upper-conflict", "upper"),
                    _hit(2, 3_210_314.0, "enemy-wire:lower-boss", "lower"),
                )
            },
            range_start_us=None,
            range_end_us=None,
            battle_occurred_at_utc="2026-09-19T08:16:46.743+00:00",
        )

        assert inferred is not None
        self.assertEqual("Abyss_10|10|mixed", inferred.environment_ref)
        self.assertIn("星流环线", inferred.environment_name)
        self.assertEqual("battle_time_partial_mixed", inferred.selection_mode)
        self.assertEqual("中", inferred.confidence)
        self.assertFalse(inferred.ambiguous)
        self.assertEqual(
            {"lower"},
            {half for half, _condition in inferred.target_conditions_by_half},
        )
        self.assertIn("上半目标映射仍冲突", inferred.inference_basis)
        self.assertIn("2026-09-19 16:16:46", inferred.inference_basis)

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
