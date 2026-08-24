# 覆盖单目标最大生命下降的比例结算、机制归因与异常样本保护。
from __future__ import annotations

import unittest

from src.services.battle_target_vital_analysis_service import (
    BattleTargetVitalAnalysisService,
)


def _row(
    sequence: int,
    time_us: int,
    *,
    character_id: int,
    character_name: str,
    effect: str,
    max_hp: float,
    hp_before: float,
    target_id: str = "boss-1",
    abyss_half: str = "",
) -> dict:
    return {
        "sequence_order": sequence,
        "relative_time_us": time_us,
        "character_id": character_id,
        "character_name": character_name,
        "direction": "outgoing",
        "damage": 1,
        "gameplay_effect_name": effect,
        "target_id": target_id,
        "abyss_half": abyss_half,
        "target_name": "墨菲斯托",
        "target_max_hp": max_hp,
        "target_hp_before": hp_before,
    }


class BattleTargetVitalAnalysisServiceTests(unittest.TestCase):
    def test_lacrimosa_description_estimate_is_excluded_from_formal_damage(self) -> None:
        rows = (
            {
                **_row(
                    1,
                    1_000_000,
                    character_id=1004,
                    character_name="安魂曲",
                    effect="GE_Player_Lacrimosa_Blood_Damage",
                    max_hp=1_000,
                    hp_before=500,
                ),
                "damage": 100,
            },
        )
        build = {
            "characters": [
                {
                    "character_id": 1004,
                    "profile": {
                        "awakening_selection_initialized": True,
                        "selected_awaken_effect_ids": ["Effect5"],
                    },
                }
            ]
        }

        estimates = BattleTargetVitalAnalysisService.estimate_from_descriptions(
            rows=rows,
            build=build,
            observed_events=(),
        )

        self.assertEqual(1, len(estimates))
        self.assertEqual(200.0, estimates[0].max_hp_reduction)
        self.assertEqual(100.0, estimates[0].effective_hp_loss)
        self.assertEqual("description_estimated", estimates[0].evidence_kind)
        self.assertFalse(estimates[0].included_in_effective_damage)

    def test_observed_transition_suppresses_covered_description_estimates(self) -> None:
        rows = (
            {
                **_row(
                    1,
                    1_000_000,
                    character_id=1004,
                    character_name="安魂曲",
                    effect="GE_Player_Lacrimosa_Blood_Damage",
                    max_hp=1_000,
                    hp_before=800,
                ),
                "damage": 100,
            },
            {
                **_row(
                    2,
                    1_200_000,
                    character_id=1004,
                    character_name="安魂曲",
                    effect="GE_Player_Lacrimosa_Blood_Damage",
                    max_hp=800,
                    hp_before=640,
                ),
                "damage": 100,
            },
        )
        build = {
            "characters": [
                {
                    "character_id": 1004,
                    "profile": {
                        "awakening_selection_initialized": True,
                        "selected_awaken_effect_ids": ["Effect5"],
                    },
                }
            ]
        }
        observed = BattleTargetVitalAnalysisService.derive(rows=rows, build=build)

        estimates = BattleTargetVitalAnalysisService.estimate_from_descriptions(
            rows=rows,
            build=build,
            observed_events=observed,
        )

        self.assertEqual(1, len(observed))
        self.assertEqual((), estimates)

    def test_fadia_description_estimate_uses_frozen_inherent_hp(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=5_000,
                hp_before=2_500,
            ),
        )
        build = {
            "characters": [
                {
                    "character_id": 1039,
                    "breakthrough_stage": 2,
                    "stats": [
                        {
                            "source_group": "resolved",
                            "property_id": "HPMaxBase",
                            "value": 1_000,
                        }
                    ],
                }
            ]
        }

        estimate = BattleTargetVitalAnalysisService.estimate_from_descriptions(
            rows=rows,
            build=build,
            observed_events=(),
        )[0]

        self.assertEqual(2_000.0, estimate.max_hp_reduction)
        self.assertEqual(1_000.0, estimate.effective_hp_loss)
        self.assertEqual("低", estimate.calculation_confidence)

    def test_fadia_estimate_requires_an_explicit_or_confirmed_target_id(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=5_000,
                hp_before=2_500,
                target_id="",
            ),
        )
        build = {"characters": [{
            "character_id": 1039,
            "breakthrough_stage": 2,
            "stats": [{
                "source_group": "resolved",
                "property_id": "HPMaxBase",
                "value": 1_000,
            }],
        }]}

        estimates = BattleTargetVitalAnalysisService.estimate_from_descriptions(
            rows=rows,
            build=build,
            observed_events=(),
        )

        self.assertEqual((), estimates)

    def test_missing_target_id_does_not_attribute_observed_drop_to_fadia(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=5_000,
                hp_before=2_500,
                target_id="",
            ),
            _row(
                2,
                1_500_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=4_000,
                hp_before=2_000,
                target_id="",
            ),
        )

        events = BattleTargetVitalAnalysisService.derive(
            rows=rows,
            build={"characters": [{
                "character_id": 1039,
                "breakthrough_stage": 2,
            }]},
        )

        self.assertEqual(1, len(events))
        self.assertEqual("unattributed_max_hp_reduction", events[0].mechanic_kind)

    def test_fadia_enemy_reduction_continues_after_five_team_gains(self) -> None:
        rows = tuple(
            _row(
                sequence,
                sequence * 1_000_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=50_000,
                hp_before=25_000,
            )
            for sequence in range(1, 7)
        )
        build = {"characters": [{
            "character_id": 1039,
            "breakthrough_stage": 2,
            "stats": [{
                "source_group": "resolved",
                "property_id": "HPMaxBase",
                "value": 1_000,
            }],
        }]}

        estimates = BattleTargetVitalAnalysisService.estimate_from_descriptions(
            rows=rows,
            build=build,
            observed_events=(),
        )

        self.assertEqual(6, len(estimates))

    def test_lacrimosa_awaken_five_uses_observed_max_drop_and_hp_ratio(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1004,
                character_name="安魂曲",
                effect="GE_Player_Lacrimosa_Blood_Damage",
                max_hp=1_000,
                hp_before=800,
            ),
            _row(
                2,
                1_300_000,
                character_id=1004,
                character_name="安魂曲",
                effect="GE_Player_Lacrimosa_Blood_Damage",
                max_hp=900,
                hp_before=720,
            ),
        )
        build = {
            "characters": [
                {
                    "character_id": 1004,
                    "profile": {
                        "awakening_selection_initialized": True,
                        "selected_awaken_effect_ids": ["Effect5"],
                    },
                }
            ]
        }

        events = BattleTargetVitalAnalysisService.derive(rows=rows, build=build)

        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual("lacrimosa_nightmare_awaken_5", event.mechanic_kind)
        self.assertEqual(100.0, event.max_hp_reduction)
        self.assertEqual(80.0, event.effective_hp_loss)
        self.assertEqual(0.8, event.hp_ratio_before)
        self.assertEqual(1004, event.source_character_id)
        self.assertIn("1:primary", event.evidence_event_ids)
        self.assertIn("2:primary", event.evidence_event_ids)

    def test_lacrimosa_drop_observed_on_next_hit_uses_old_max_frontier(self) -> None:
        rows = (
            {
                **_row(
                    1,
                    1_000_000,
                    character_id=1004,
                    character_name="安魂曲",
                    effect="GE_Player_Lacrimosa_Blood_Damage_LV6",
                    max_hp=1_000,
                    hp_before=800,
                ),
                "damage": 250,
            },
            _row(
                2,
                1_100_000,
                character_id=1036,
                character_name="残虹",
                effect="GE_Player_Zankou_Melee1_Damage",
                max_hp=800,
                hp_before=600,
            ),
        )
        build = {
            "characters": [{
                "character_id": 1004,
                "profile": {
                    "awakening_selection_initialized": True,
                    "selected_awaken_effect_ids": ["Effect5"],
                },
            }]
        }

        event = BattleTargetVitalAnalysisService.derive(
            rows=rows,
            build=build,
        )[0]

        self.assertEqual(800.0, event.hp_before_settlement)
        self.assertEqual(0.8, event.hp_ratio_before)
        self.assertEqual(160.0, event.effective_hp_loss)

    def test_drop_uses_minimum_nearby_old_max_hp_after_as_frontier(self) -> None:
        rows = (
            {
                **_row(
                    1, 1_000_000, character_id=1036, character_name="残虹",
                    effect="hit-a", max_hp=1_000, hp_before=900,
                ),
                "target_hp_after": 850,
            },
            {
                **_row(
                    2, 1_100_000, character_id=1036, character_name="残虹",
                    effect="hit-b", max_hp=1_000, hp_before=900,
                ),
                "target_hp_after": 870,
            },
            {
                **_row(
                    3, 1_200_000, character_id=1036, character_name="残虹",
                    effect="hit-c", max_hp=800, hp_before=760,
                ),
                "target_hp_after": 750,
            },
        )

        event = BattleTargetVitalAnalysisService.derive(rows=rows, build=None)[0]

        self.assertEqual(850.0, event.hp_before_settlement)
        self.assertEqual(170.0, event.effective_hp_loss)

    def test_unselected_lacrimosa_awaken_five_keeps_drop_unattributed(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1004,
                character_name="安魂曲",
                effect="GE_Player_Lacrimosa_Blood_Damage_LV6",
                max_hp=1_000,
                hp_before=1_000,
            ),
            _row(
                2,
                1_200_000,
                character_id=1004,
                character_name="安魂曲",
                effect="GE_Player_Lacrimosa_Blood_Damage_LV6",
                max_hp=900,
                hp_before=900,
            ),
        )
        build = {
            "characters": [
                {
                    "character_id": 1004,
                    "profile": {
                        "awakening_selection_initialized": True,
                        "selected_awaken_effect_ids": [],
                    },
                }
            ]
        }

        event = BattleTargetVitalAnalysisService.derive(rows=rows, build=build)[0]

        self.assertEqual("unattributed_max_hp_reduction", event.mechanic_kind)
        self.assertIsNone(event.source_character_id)
        self.assertEqual(100.0, event.effective_hp_loss)

    def test_fadia_trigger_has_priority_over_nearby_nightmare(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1004,
                character_name="安魂曲",
                effect="GE_Player_Lacrimosa_Blood_Damage",
                max_hp=2_000,
                hp_before=1_500,
            ),
            _row(
                2,
                1_100_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=2_000,
                hp_before=1_500,
            ),
            _row(
                3,
                1_400_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=1_800,
                hp_before=1_500,
            ),
        )

        event = BattleTargetVitalAnalysisService.derive(
            rows=rows,
            build={"characters": [
                {"character_id": 1004},
                {"character_id": 1039, "breakthrough_stage": 2},
            ]},
        )[0]

        self.assertEqual("fadia_dark_star_max_hp_transfer", event.mechanic_kind)
        self.assertEqual(1039, event.source_character_id)
        self.assertEqual(150.0, event.effective_hp_loss)

    def test_locked_fadia_passive_does_not_claim_observed_max_hp_drop(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=2_000,
                hp_before=1_500,
            ),
            _row(
                2,
                1_400_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=1_800,
                hp_before=1_500,
            ),
        )
        build = {"characters": [
            {"character_id": 1039, "breakthrough_stage": 1}
        ]}

        event = BattleTargetVitalAnalysisService.derive(
            rows=rows,
            build=build,
        )[0]
        estimates = BattleTargetVitalAnalysisService.estimate_from_descriptions(
            rows=rows,
            build=build,
            observed_events=(event,),
        )

        self.assertEqual("unattributed_max_hp_reduction", event.mechanic_kind)
        self.assertIsNone(event.source_character_id)
        self.assertEqual((), estimates)

    def test_higher_stale_max_sample_does_not_reset_confirmed_max(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=1_000,
                hp_before=1_000,
            ),
            _row(
                2,
                1_100_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=1_100,
                hp_before=900,
            ),
            _row(
                3,
                1_200_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=900,
                hp_before=810,
            ),
        )

        events = BattleTargetVitalAnalysisService.derive(
            rows=rows,
            build={"characters": [
                {"character_id": 1039, "breakthrough_stage": 2}
            ]},
        )

        self.assertEqual(1, len(events))
        self.assertEqual(1_000.0, events[0].old_max_hp)
        self.assertEqual(900.0, events[0].new_max_hp)

    def test_distinct_target_instances_keep_independent_max_hp_baselines(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=1_000,
                hp_before=900,
                target_id="boss-1",
            ),
            _row(
                2,
                1_100_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=2_000,
                hp_before=1_800,
                target_id="boss-2",
            ),
            _row(
                3,
                1_200_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=900,
                hp_before=810,
                target_id="boss-1",
            ),
            _row(
                4,
                1_300_000,
                character_id=1039,
                character_name="法帝娅",
                effect="Buff_Reaction_4_new",
                max_hp=1_800,
                hp_before=1_620,
                target_id="boss-2",
            ),
        )

        events = BattleTargetVitalAnalysisService.derive(
            rows=rows,
            build={"characters": [
                {"character_id": 1039, "breakthrough_stage": 2}
            ]},
        )

        self.assertEqual(2, len(events))
        self.assertEqual(
            {("boss-1", 1_000.0, 900.0), ("boss-2", 2_000.0, 1_800.0)},
            {
                (event.target_id, event.old_max_hp, event.new_max_hp)
                for event in events
            },
        )

    def test_same_wire_id_reused_by_halves_resets_max_hp_baseline(self) -> None:
        rows = (
            _row(
                1, 1_000_000, character_id=1039, character_name="法帝娅",
                effect="Buff_Reaction_4_new", max_hp=1_000, hp_before=900,
                target_id="enemy-wire:1", abyss_half="upper",
            ),
            _row(
                2, 1_100_000, character_id=1039, character_name="法帝娅",
                effect="Buff_Reaction_4_new", max_hp=900, hp_before=810,
                target_id="enemy-wire:1", abyss_half="upper",
            ),
            _row(
                3, 2_000_000, character_id=1039, character_name="法帝娅",
                effect="Buff_Reaction_4_new", max_hp=2_000, hp_before=1_800,
                target_id="enemy-wire:1", abyss_half="lower",
            ),
            _row(
                4, 2_100_000, character_id=1039, character_name="法帝娅",
                effect="Buff_Reaction_4_new", max_hp=1_800, hp_before=1_620,
                target_id="enemy-wire:1", abyss_half="lower",
            ),
        )

        events = BattleTargetVitalAnalysisService.derive(
            rows=rows,
            build={"characters": [
                {"character_id": 1039, "breakthrough_stage": 2}
            ]},
        )

        self.assertEqual(
            {(1_000.0, 900.0), (2_000.0, 1_800.0)},
            {(event.old_max_hp, event.new_max_hp) for event in events},
        )

    def test_mixed_identity_excludes_unknown_rows_from_vital_derivation(self) -> None:
        known = _row(
            1,
            1_000_000,
            character_id=1039,
            character_name="法帝娅",
            effect="Buff_Reaction_4_new",
            max_hp=1_000,
            hp_before=900,
            target_id="boss-1",
        )
        unknown = _row(
            2,
            1_100_000,
            character_id=1039,
            character_name="法帝娅",
            effect="Buff_Reaction_4_new",
            max_hp=800,
            hp_before=700,
            target_id="",
        )

        events = BattleTargetVitalAnalysisService.derive(
            rows=(known, unknown),
            build={"characters": [
                {"character_id": 1039, "breakthrough_stage": 2}
            ]},
        )

        self.assertEqual((), events)


if __name__ == "__main__":
    unittest.main()
