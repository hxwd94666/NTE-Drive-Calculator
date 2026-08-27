# 验证手选环境按半场、target_id 与最大生命映射逐目标敌方属性。
from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace

from src.domain.battle_report import BattleAnalysisSnapshot, BattleTargetCondition
from src.domain.battle_target import BattleSelectedTargetProfile
from src.services.battle_target_instance_mapping_service import (
    BattleTargetInstanceMappingService,
)
from src.services.battle_target_profile_snapshot_service import (
    battle_target_profile_snapshots,
)


def _profile(
    monster_id: str,
    hp: float,
    defense: float,
    resistance: float,
) -> BattleSelectedTargetProfile:
    return BattleSelectedTargetProfile(
        static_target_id=monster_id,
        selection_target_id=monster_id,
        target_name=monster_id,
        monster_class_path=monster_id,
        monster_count=1,
        max_hp=hp,
        monster_level=90.0,
        defense_base=defense,
        defense_up=0.0,
        defense_add=0.0,
        topple_limit=50.0,
        resistances=(("chaos", resistance),),
    )


def _condition(*profiles: BattleSelectedTargetProfile) -> BattleTargetCondition:
    primary = profiles[0]
    return BattleTargetCondition(
        target_name=primary.target_name,
        enemy_level=90.0,
        scene="outer_realm",
        defense_reduction=0.0,
        vulnerability=0.0,
        resistances=primary.resistances,
        enemy_defense_base=primary.defense_base,
        environment_kind="outer_realm",
        selected_target_ids=tuple(row.selection_target_id for row in profiles),
        primary_target_id=primary.selection_target_id,
        selected_target_profiles=profiles,
    )


def _evidence(*rows: tuple[str, str, float]) -> dict:
    return {"hits": [
        {
            "direction": "outgoing",
            "abyss_half": half,
            "target_id": target_id,
            "target_max_hp": hp,
            "relative_time_us": index,
        }
        for index, (half, target_id, hp) in enumerate(rows)
    ]}


def _analysis(condition, resolutions) -> BattleAnalysisSnapshot:
    return BattleAnalysisSnapshot(
        battle_record_id=1,
        capability_level="formal_hit",
        axis_complete=True,
        formula_model_version="fixture",
        name_mapping_version="fixture",
        action_inference_version="fixture",
        timeline_projection_version="fixture",
        battle_start_us=0,
        battle_end_us=1,
        timeline_end_us=1,
        range_start_us=0,
        range_end_us=1,
        duration_seconds=0.000001,
        total_damage=0.0,
        total_dps=0.0,
        timeline_hits=(),
        inferred_actions=(),
        inferred_inputs=(),
        timeline_damage_groups=(),
        hits=(),
        roles=(),
        skills=(),
        targets=(),
        baselines=(),
        target_condition=condition,
        target_instance_resolutions=resolutions,
        target_instance_mapping_required=True,
    )


class BattleTargetInstanceMappingServiceTests(unittest.TestCase):
    def test_open_world_selection_freezes_each_hp_variant(self) -> None:
        snapshots = battle_target_profile_snapshots({
            "target_id": "mon_001",
            "name_zh": "测试目标",
            "variants": [
                {
                    "monster_id": "Mon_001_A",
                    "monster_level": 10,
                    "profile": {
                        "health_base": 1000.0,
                        "defense_base": 100.0,
                        "resistances": {"chaos": 0.1},
                    },
                },
                {
                    "monster_id": "Mon_001_B",
                    "monster_level": 20,
                    "profile": {
                        "health_base": 2000.0,
                        "defense_base": 200.0,
                        "resistances": {"chaos": 0.2},
                    },
                },
            ],
        })

        self.assertEqual([1000.0, 2000.0], [row["max_hp"] for row in snapshots])
        self.assertEqual([100.0, 200.0], [row["defense_base"] for row in snapshots])

    def test_two_targets_use_their_own_defense_and_resistance(self) -> None:
        condition = _condition(
            _profile("mon_001", 1000.0, 900.0, 0.10),
            _profile("mon_002", 2000.0, 1300.0, 0.45),
        )
        resolutions = BattleTargetInstanceMappingService.resolve(
            _evidence(("upper", "7", 1000.0), ("upper", "8", 2000.0)),
            condition,
        )
        self.assertTrue(all(
            row.default_monster_id == row.resolved_monster_id
            for row in resolutions
        ))
        self.assertTrue(all(
            row.resolution_mode == "environment_hp_unique"
            for row in resolutions
        ))

        first = BattleTargetInstanceMappingService.analysis_for_hit(
            _analysis(condition, resolutions),
            SimpleNamespace(scope_half="upper", target_id="7"),
        ).target_condition
        second = BattleTargetInstanceMappingService.analysis_for_hit(
            _analysis(condition, resolutions),
            SimpleNamespace(scope_half="upper", target_id="8"),
        ).target_condition

        assert first is not None and second is not None
        self.assertEqual(900.0, first.enemy_defense_base)
        self.assertEqual("mon_001", first.resolved_monster_id)
        self.assertEqual(0.10, dict(first.resistances)["chaos"])
        self.assertEqual(1300.0, second.enemy_defense_base)
        self.assertEqual("mon_002", second.resolved_monster_id)
        self.assertEqual(0.45, dict(second.resistances)["chaos"])

        changed_primary = replace(
            condition,
            target_name="mon_002",
            enemy_defense_base=1300.0,
            resistances=(("chaos", 0.45),),
            primary_target_id="mon_002",
        )
        changed = BattleTargetInstanceMappingService.resolve(
            _evidence(("upper", "7", 1000.0), ("upper", "8", 2000.0)),
            changed_primary,
        )
        self.assertEqual(
            {"7": 900.0, "8": 1300.0},
            {
                row.captured_target_id: (
                    None
                    if row.target_condition is None
                    else row.target_condition.enemy_defense_base
                )
                for row in changed
            },
        )

    def test_equal_hp_equal_profile_keeps_monster_id_ambiguous_but_replays(self) -> None:
        first = _profile("mon_014", 5000.0, 1050.0, 0.20)
        second = _profile("mon_029", 5000.0, 1050.0, 0.20)
        resolution = BattleTargetInstanceMappingService.resolve(
            _evidence(("", "target-a", 5000.0)),
            _condition(first, second),
        )[0]

        self.assertEqual("profile_equivalent", resolution.resolution_mode)
        self.assertEqual("", resolution.resolved_monster_id)
        self.assertEqual("mon_014", resolution.default_monster_id)
        self.assertEqual(("mon_014", "mon_029"), resolution.possible_monster_ids)
        self.assertIsNotNone(resolution.target_condition)
        replay_condition = BattleTargetInstanceMappingService.analysis_for_hit(
            _analysis(_condition(first, second), (resolution,)),
            SimpleNamespace(scope_half="", target_id="target-a"),
        ).target_condition
        assert replay_condition is not None
        self.assertEqual("", replay_condition.resolved_monster_id)
        self.assertEqual(1050.0, replay_condition.enemy_defense_base)
        self.assertEqual(0.20, dict(replay_condition.resistances)["chaos"])

        reversed_resolution = BattleTargetInstanceMappingService.resolve(
            _evidence(("", "target-a", 5000.0)),
            _condition(second, first),
        )[0]
        self.assertEqual("mon_014", reversed_resolution.default_monster_id)
        self.assertEqual(
            resolution.possible_monster_ids,
            reversed_resolution.possible_monster_ids,
        )
        self.assertEqual("", reversed_resolution.resolved_monster_id)
        assert reversed_resolution.target_condition is not None
        self.assertEqual("mon_014", reversed_resolution.target_condition.target_name)
        self.assertEqual(
            1050.0,
            reversed_resolution.target_condition.enemy_defense_base,
        )

    def test_core_exact_default_equals_resolved_identity(self) -> None:
        condition = _condition(_profile("mon_001", 1000.0, 900.0, 0.10))
        resolution = BattleTargetInstanceMappingService.resolve(
            {"hits": [{
                "direction": "outgoing",
                "abyss_half": "",
                "target_id": "target-a",
                "target_monster_id": "mon_001",
                "target_max_hp": 1000.0,
                "relative_time_us": 0,
            }]},
            condition,
        )[0]

        self.assertEqual("core_exact", resolution.resolution_mode)
        self.assertEqual("mon_001", resolution.resolved_monster_id)
        self.assertEqual("mon_001", resolution.default_monster_id)

    def test_equal_hp_different_profile_does_not_fall_back_to_primary(self) -> None:
        resolution = BattleTargetInstanceMappingService.resolve(
            _evidence(("lower", "target-a", 5000.0)),
            _condition(
                _profile("mon_014", 5000.0, 1050.0, 0.20),
                _profile("mon_029", 5000.0, 1250.0, 0.40),
            ),
        )[0]

        hit_analysis = BattleTargetInstanceMappingService.analysis_for_hit(
            _analysis(_condition(
                _profile("mon_014", 5000.0, 1050.0, 0.20),
                _profile("mon_029", 5000.0, 1250.0, 0.40),
            ), (resolution,)),
            SimpleNamespace(scope_half="lower", target_id="target-a"),
        )

        self.assertEqual("ambiguous", resolution.resolution_mode)
        self.assertEqual("", resolution.resolved_monster_id)
        self.assertEqual("", resolution.default_monster_id)
        self.assertEqual(("mon_014", "mon_029"), resolution.possible_monster_ids)
        self.assertIsNone(hit_analysis.target_condition)

    def test_same_target_id_in_two_halves_is_resolved_independently(self) -> None:
        condition = _condition(
            _profile("mon_001", 1000.0, 900.0, 0.10),
            _profile("mon_002", 2000.0, 1300.0, 0.45),
        )
        resolutions = BattleTargetInstanceMappingService.resolve(
            _evidence(("upper", "7", 1000.0), ("lower", "7", 2000.0)),
            condition,
        )

        self.assertEqual(
            [("lower", "7", "mon_002"), ("upper", "7", "mon_001")],
            sorted(
                (row.scope_half, row.captured_target_id, row.resolved_monster_id)
                for row in resolutions
            ),
        )


if __name__ == "__main__":
    unittest.main()
