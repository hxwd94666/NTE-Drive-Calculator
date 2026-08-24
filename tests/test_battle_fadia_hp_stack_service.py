# 验证法帝娅固有生命与黯星生命汲取的逐击重放规则。
from __future__ import annotations

import unittest
from dataclasses import replace

from src.domain.battle_report import BattleAnalysisHit, BattleMaxHpReductionEvent
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_fadia_hp_stack_service import (
    BattleFadiaHpStackService,
    resolve_fadia_inherent_hp,
)


def _hit(sequence: int, time_us: int, *, dark_star: bool) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"{sequence}:primary",
        sequence=sequence,
        relative_time_us=time_us,
        character_id=1039,
        character_name="法帝娅",
        skill_name="黯星" if dark_star else "终结技",
        damage_name="黯星" if dark_star else "痛苦让位于狂喜",
        damage_component="skill",
        attack_type="skill",
        damage_attribute="psychically" if dark_star else "psyche",
        target_id="boss-1",
        target_name="墨菲斯托",
        damage=100.0,
        direction="outgoing",
        is_follow_up=False,
        classification="reaction" if dark_star else "direct",
        gameplay_effect_id="Buff_Reaction_4_new" if dark_star else "GE_Fadia_Q",
    )


def _build(*, effect_three: bool = True) -> dict:
    return {
        "characters": [{
            "character_id": 1039,
            "breakthrough_stage": 2,
            "profile": {
                "awakening_selection_initialized": True,
                "selected_awaken_effect_ids": ["Effect3"] if effect_three else [],
            },
            "stats": [
                {
                    "source_group": "character",
                    "property_id": "HPMaxBase",
                    "value": 10_000.0,
                },
                {
                    "source_group": "fork",
                    "property_id": "HPMaxBase",
                    "value": 5_000.0,
                },
                {
                    "source_group": "resolved",
                    "property_id": "HPMaxBase",
                    "value": 17_000.0,
                },
                {
                    "source_group": "resolved",
                    "property_id": "PanelHP",
                    "value": 20_000.0,
                },
            ],
        }]
    }


def _observed_transfer(reduction: float) -> BattleMaxHpReductionEvent:
    return BattleMaxHpReductionEvent(
        event_id="max-hp:1",
        target_id="boss-1",
        target_name="墨菲斯托",
        observed_at_us=1_500_000,
        old_max_hp=1_000_000.0,
        new_max_hp=1_000_000.0 - reduction,
        max_hp_reduction=reduction,
        hp_before_settlement=900_000.0,
        hp_ratio_before=0.9,
        effective_hp_loss=reduction * 0.9,
        source_character_id=1039,
        source_character_name="法帝娅",
        mechanic_kind="fadia_dark_star_max_hp_transfer",
        mechanic_name="法帝娅被动·黯星生命上限汲取",
        source_skill_name="黯星",
        evidence_event_ids=("1:primary",),
        attribution_confidence="高",
        calculation_confidence="高",
        inference_basis="fixture",
    )


class BattleFadiaHpStackServiceTests(unittest.TestCase):
    def test_inherent_hp_uses_character_and_fork_base_only(self) -> None:
        self.assertEqual(19_500.0, resolve_fadia_inherent_hp(_build()))
        self.assertEqual(
            15_000.0,
            resolve_fadia_inherent_hp(_build(effect_three=False)),
        )

    def test_observed_max_hp_transfer_overrides_stale_build_configuration(self) -> None:
        self.assertEqual(
            52_781.25,
            resolve_fadia_inherent_hp(
                _build(effect_three=False),
                observed_events=(_observed_transfer(105_562.5),),
            ),
        )

    def test_each_dark_star_adds_one_persistent_team_hp_stack(self) -> None:
        dark_star_one = _hit(1, 1_000_000, dark_star=True)
        dark_star_two = _hit(3, 3_000_000, dark_star=True)
        intervals = BattleFadiaHpStackService.infer(
            build=_build(),
            hits=(dark_star_one, dark_star_two),
            battle_end_us=5_000_000,
        )

        before = BattleBuffAttributeProjectionService.project_hit(
            replace(_hit(0, 500_000, dark_star=False), character_id=1004),
            intervals,
        )
        after_one = BattleBuffAttributeProjectionService.project_hit(
            replace(_hit(2, 2_000_000, dark_star=False), character_id=1004),
            intervals,
        )
        after_two = BattleBuffAttributeProjectionService.project_hit(
            replace(_hit(4, 4_000_000, dark_star=False), character_id=1004),
            intervals,
        )

        self.assertEqual((), before.modifiers)
        self.assertEqual("HPMaxAdd", after_one.modifiers[0].property_id)
        self.assertEqual(1_950.0, after_one.modifiers[0].additive_value)
        self.assertEqual(3_900.0, after_two.modifiers[0].additive_value)

    def test_formal_target_drop_calibrates_each_team_hp_stack(self) -> None:
        intervals = BattleFadiaHpStackService.infer(
            build=_build(effect_three=False),
            hits=(_hit(1, 1_000_000, dark_star=True),),
            battle_end_us=3_000_000,
            max_hp_events=(_observed_transfer(105_562.5),),
        )

        projection = BattleBuffAttributeProjectionService.project_hit(
            replace(_hit(2, 2_000_000, dark_star=False), character_id=1004),
            intervals,
        )

        self.assertEqual(5_278.125, projection.modifiers[0].additive_value)
        team_interval = next(row for row in intervals if row.target_scope == "team")
        self.assertEqual("高", team_interval.value_confidence)

    def test_formal_target_drop_calibrates_stale_fadia_hp_before_first_stack(
        self,
    ) -> None:
        intervals = BattleFadiaHpStackService.infer(
            build=_build(effect_three=False),
            hits=(_hit(1, 1_000_000, dark_star=True),),
            battle_end_us=3_000_000,
            max_hp_events=(_observed_transfer(105_562.5),),
        )

        projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(0, 500_000, dark_star=False),
            intervals,
        )

        self.assertEqual(1, len(projection.modifiers))
        self.assertEqual("HPMaxAdd", projection.modifiers[0].property_id)
        self.assertEqual(37_781.25, projection.modifiers[0].additive_value)
        self.assertIn("实测补正", projection.modifiers[0].buff_names[0])

    def test_dark_star_hit_itself_does_not_consume_resulting_stack(self) -> None:
        dark_star = _hit(1, 1_000_000, dark_star=True)
        intervals = BattleFadiaHpStackService.infer(
            build=_build(),
            hits=(dark_star,),
            battle_end_us=2_000_000,
        )

        projection = BattleBuffAttributeProjectionService.project_hit(
            dark_star,
            intervals,
        )

        self.assertEqual((), projection.modifiers)

    def test_stack_count_is_capped_at_five(self) -> None:
        intervals = BattleFadiaHpStackService.infer(
            build=_build(),
            hits=tuple(
                _hit(index, index * 1_000_000, dark_star=True)
                for index in range(1, 8)
            ),
            battle_end_us=9_000_000,
        )

        self.assertEqual(5, len(intervals))

    def test_team_stack_resets_when_the_abyss_half_changes(self) -> None:
        upper = tuple(
            replace(
                _hit(index, index * 1_000_000, dark_star=True),
                scope_half="upper",
            )
            for index in range(1, 6)
        )
        lower = replace(
            _hit(6, 10_000_000, dark_star=True),
            scope_half="lower",
        )
        intervals = BattleFadiaHpStackService.infer(
            build=_build(),
            hits=(*upper, lower),
            battle_end_us=12_000_000,
        )

        projection = BattleBuffAttributeProjectionService.project_hit(
            replace(
                _hit(7, 11_000_000, dark_star=False),
                character_id=1004,
                scope_half="lower",
            ),
            intervals,
        )

        self.assertEqual(6, len(intervals))
        self.assertEqual(1_950.0, projection.modifiers[0].additive_value)

    def test_locked_passive_does_not_create_team_hp_intervals(self) -> None:
        build = _build()
        build["characters"][0]["breakthrough_stage"] = 1

        intervals = BattleFadiaHpStackService.infer(
            build=build,
            hits=(_hit(1, 1_000_000, dark_star=True),),
            battle_end_us=3_000_000,
        )

        self.assertEqual((), intervals)
