# 治疗事件与治疗触发 Buff 必须共享同一条扣除时停的派生时间轴。
from __future__ import annotations

import unittest
from dataclasses import replace
from typing import Literal

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredAction,
    BattleTreatmentEvent,
)
from src.services.battle_treatment_buff_service import BattleTreatmentBuffService
from src.services.battle_treatment_event_service import BattleTreatmentEventService
from src.services.battle_zankou_form_buff_service import (
    BattleZankouFormBuffService,
    BattleZankouFormConfig,
)


def _action(
    ordinal: int,
    input_kind: str,
    start_us: int,
    *,
    gesture: Literal["tap", "hold"] = "tap",
) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=f"action:{ordinal}",
        character_id=1075,
        character_name="伊洛伊",
        action_name=input_kind,
        input_kind=input_kind,
        input_sequence=input_kind,
        start_us=start_us,
        end_us=start_us + 500_000,
        hits=1,
        damage=100.0,
        identity_confidence="中",
        timing_confidence="中",
        inference_basis="正式动画候选",
        evidence_event_ids=(f"{ordinal}:primary",),
        gameplay_effect_ids=(f"GE_Oneiroi_{input_kind}",),
        input_gesture=gesture,
    )


def _build(stage: int = 4) -> dict:
    return {
        "characters": [{
            "character_id": 1075,
            "observed_name": "伊洛伊",
            "breakthrough_stage": stage,
            "profile": {},
            "stats": [
                {
                    "source_group": "character",
                    "property_id": "AtkBase",
                    "value": 596.0,
                },
                {
                    "source_group": "fork",
                    "property_id": "AtkBase",
                    "value": 570.0,
                },
            ],
        }],
    }


def _hit(
    event_id: str,
    time_us: int,
    character_id: int,
    *,
    ability_id: str,
    damage_name: str,
    damage: float = 1000.0,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=int(event_id.split(":", 1)[0]),
        relative_time_us=time_us,
        character_id=character_id,
        character_name="角色",
        skill_name=damage_name,
        damage_name=damage_name,
        damage_component="skill",
        attack_type="skill",
        damage_attribute="chaos",
        target_id="target",
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id=ability_id,
        gameplay_effect_id=damage_name,
    )


class BattleTreatmentEventServiceTests(unittest.TestCase):
    def test_oneiroi_emits_formal_instant_and_periodic_treatments(self) -> None:
        events = BattleTreatmentEventService.infer(
            build=_build(),
            actions=(
                _action(1, "QTE", 1_000_000),
                _action(2, "E", 3_000_000),
                _action(3, "E", 4_000_000),
                _action(4, "E", 7_000_000, gesture="hold"),
                _action(5, "Q", 11_000_000),
            ),
            battle_end_us=40_000_000,
            time_stop_intervals=((12_000_000, 17_000_000),),
        )

        by_kind = {}
        for event in events:
            by_kind.setdefault(event.treatment_kind, []).append(event)
        self.assertEqual(1, len(by_kind["oneiroi_qte"]))
        self.assertEqual(
            1_784_675,
            by_kind["oneiroi_qte"][0].relative_time_us,
        )
        self.assertEqual(1, len(by_kind["oneiroi_e_tap"]))
        self.assertEqual(1, len(by_kind["oneiroi_q"]))
        self.assertEqual(14, len(by_kind["oneiroi_q_period"]))
        self.assertEqual(
            18_000_000,
            by_kind["oneiroi_q_period"][0].relative_time_us,
        )
        self.assertFalse(any(
            event.source_action_id in {"action:3", "action:4"}
            for event in events
        ))

    def test_treatment_consumers_refresh_passive_and_apply_e_attack(self) -> None:
        events = (
            BattleTreatmentEvent(
                "treatment:e",
                1_000_000,
                1075,
                "伊洛伊",
                "action:e",
                "oneiroi_e_tap",
            ),
            BattleTreatmentEvent(
                "treatment:qte",
                5_000_000,
                1075,
                "伊洛伊",
                "action:qte",
                "oneiroi_qte",
            ),
        )
        intervals = BattleTreatmentBuffService.infer(
            build=_build(),
            treatment_events=events,
            battle_end_us=40_000_000,
        )

        passive = next(row for row in intervals if row.buff_name == "交感性神经系统")
        attack = next(row for row in intervals if row.buff_name == "伊洛伊 E：全队攻击力")
        self.assertEqual((1_000_000, 25_000_000), (passive.start_us, passive.end_us))
        self.assertEqual(
            ("treatment:e", "treatment:qte"),
            passive.evidence_event_ids,
        )
        self.assertEqual(0.05, passive.modifiers[0].magnitude_value)
        self.assertEqual((1_000_000, 21_000_000), (attack.start_us, attack.end_us))
        self.assertAlmostEqual(233.2, attack.modifiers[0].magnitude_value)

    def test_locked_passive_still_keeps_intrinsic_e_attack_buff(self) -> None:
        intervals = BattleTreatmentBuffService.infer(
            build=_build(stage=3),
            treatment_events=(BattleTreatmentEvent(
                "treatment:e",
                1_000_000,
                1075,
                "伊洛伊",
                "action:e",
                "oneiroi_e_tap",
            ),),
            battle_end_us=30_000_000,
        )

        self.assertFalse(any(row.buff_name == "交感性神经系统" for row in intervals))
        self.assertTrue(any(row.buff_name == "伊洛伊 E：全队攻击力" for row in intervals))

    def test_effect_five_consumes_every_treatment_event(self) -> None:
        build = _build()
        build["characters"][0]["profile"] = {
            "awakening_selection_initialized": True,
            "selected_awaken_effect_ids": ["Effect5"],
        }
        intervals = BattleTreatmentBuffService.infer(
            build=build,
            treatment_events=(BattleTreatmentEvent(
                "treatment:q-period",
                2_000_000,
                1075,
                "伊洛伊",
                "action:q",
                "oneiroi_q_period",
            ),),
            battle_end_us=30_000_000,
        )

        effect = next(row for row in intervals if row.buff_name == "清晰")
        self.assertAlmostEqual(174.9, effect.modifiers[0].magnitude_value)
        self.assertEqual(("treatment:q-period",), effect.evidence_event_ids)

    def test_lacrimosa_effect_five_uses_positive_nightmare_windows(self) -> None:
        build = {
            "characters": [{
                "character_id": 1004,
                "observed_name": "安魂曲",
                "profile": {
                    "awakening_selection_initialized": True,
                    "selected_awaken_effect_ids": ["Effect5"],
                },
            }],
        }
        events = BattleTreatmentEventService.infer(
            build=build,
            actions=(),
            hits=(
                _hit(
                    "1:primary",
                    1_000_000,
                    1004,
                    ability_id="GA_Lacrimosa_Melee",
                    damage_name="噩梦",
                ),
                _hit(
                    "2:primary",
                    4_000_000,
                    1004,
                    ability_id="GA_Lacrimosa_Melee",
                    damage_name="噩梦",
                ),
            ),
            battle_end_us=7_000_000,
        )

        periods = tuple(
            row for row in events
            if row.treatment_kind == "lacrimosa_effect5_period"
        )
        self.assertEqual((3_000_000, 6_000_000), tuple(
            row.relative_time_us for row in periods
        ))
        self.assertEqual((15.0, 15.0), tuple(
            row.raw_healing_amount for row in periods
        ))

    def test_edgar_hold_segments_and_q_field_use_actual_axis_boundaries(self) -> None:
        hold_hit = _hit(
            "1:primary",
            1_000_000,
            1021,
            ability_id="GA_Edgar_Skill",
            damage_name="狂流",
        )
        q_hit = _hit(
            "2:primary",
            5_000_000,
            1021,
            ability_id="GA_Edgar_UltraSkill",
            damage_name="芬尼根守灵夜",
        )
        hold = _action(1, "E", 800_000, gesture="hold")
        hold = replace(
            hold,
            character_id=1021,
            character_name="埃德嘉",
            evidence_event_ids=(hold_hit.event_id,),
        )
        q = _action(2, "Q", 4_500_000)
        q = replace(
            q,
            character_id=1021,
            character_name="埃德嘉",
            evidence_event_ids=(q_hit.event_id,),
        )
        events = BattleTreatmentEventService.infer(
            build={"characters": [{"character_id": 1021}]},
            actions=(hold, q),
            hits=(hold_hit, q_hit),
            battle_end_us=9_000_000,
            time_stop_intervals=((5_500_000, 6_500_000),),
        )

        hold_events = tuple(
            row for row in events
            if row.treatment_kind == "edgar_hold_e_segment"
        )
        q_events = tuple(
            row for row in events
            if row.treatment_kind == "edgar_q_field_period"
        )
        self.assertEqual((1_000_000,), tuple(
            row.relative_time_us for row in hold_events
        ))
        self.assertEqual(7_000_000, q_events[0].relative_time_us)
        self.assertTrue(all(row.is_periodic for row in q_events))

    def test_oneiroi_effect_two_uses_other_character_e_and_active_cooldown(self) -> None:
        build = _build()
        build["characters"][0]["profile"] = {
            "awakening_selection_initialized": True,
            "selected_awaken_effect_ids": ["Effect2"],
        }
        actions = tuple(
            replace(
                _action(ordinal, "E", time_us),
                character_id=1003,
                character_name="早雾",
            )
            for ordinal, time_us in ((1, 1_000_000), (2, 3_000_000), (3, 7_000_000))
        )
        events = BattleTreatmentEventService.infer(
            build=build,
            actions=actions,
            battle_end_us=10_000_000,
        )

        awaken_events = tuple(
            row for row in events
            if row.treatment_kind == "oneiroi_awaken2_team"
        )
        self.assertEqual((1_000_000, 7_000_000), tuple(
            row.relative_time_us for row in awaken_events
        ))

    def test_shinku_effect_five_deduplicates_same_time_multi_target_hits(self) -> None:
        build = {
            "characters": [{
                "character_id": 1076,
                "observed_name": "真红",
                "profile": {
                    "awakening_selection_initialized": True,
                    "selected_awaken_effect_ids": ["Effect5"],
                },
                "stats": [
                    {
                        "source_group": "character",
                        "property_id": "AtkBase",
                        "value": 600.0,
                    },
                    {
                        "source_group": "fork",
                        "property_id": "AtkBase",
                        "value": 500.0,
                    },
                ],
            }],
        }
        first = _hit(
            "1:primary",
            2_000_000,
            1076,
            ability_id="GA_Shinku_Skill_Rage",
            damage_name="GE_Player_Shinku_Skill2_Rage_Damage",
        )
        second = replace(first, event_id="2:primary", target_id="target:2")
        events = BattleTreatmentEventService.infer(
            build=build,
            actions=(),
            hits=(first, second),
            battle_end_us=5_000_000,
        )

        treatment = tuple(
            row for row in events
            if row.treatment_kind == "shinku_effect5_rage_e"
        )
        self.assertEqual(1, len(treatment))
        self.assertEqual(3300.0, treatment[0].raw_healing_amount)
        self.assertEqual(
            ("1:primary", "2:primary"),
            treatment[0].evidence_event_ids,
        )

    def test_kuhara_effect_two_requires_observed_q_settlement(self) -> None:
        build = {
            "characters": [{
                "character_id": 1055,
                "observed_name": "九原",
                "profile": {
                    "awakening_selection_initialized": True,
                    "selected_awaken_effect_ids": ["Effect2"],
                },
            }],
        }
        q = replace(
            _action(1, "Q", 1_000_000),
            character_id=1055,
            character_name="九原",
            end_us=1_500_000,
        )
        settlement = _hit(
            "1:primary",
            2_000_000,
            1055,
            ability_id="GA_Kuhara_UltraSkill",
            damage_name="GE_Player_Kuhara_BudBoom_Damage",
        )
        events = BattleTreatmentEventService.infer(
            build=build,
            actions=(q,),
            hits=(settlement,),
            battle_end_us=4_000_000,
        )

        treatment = tuple(
            row for row in events
            if row.treatment_kind == "kuhara_effect2_q_settlement"
        )
        self.assertEqual(1, len(treatment))
        self.assertEqual("team", treatment[0].target_scope)

    def test_zankou_effect_three_uses_huo_interval_and_active_seconds(self) -> None:
        build = {
            "characters": [{
                "character_id": 1036,
                "observed_name": "残虹",
                "profile": {
                    "awakening_selection_initialized": True,
                    "selected_awaken_effect_ids": ["Effect1", "Effect3"],
                },
                "stats": [{
                    "source_group": "character",
                    "property_id": "HPMaxBase",
                    "value": 10_000.0,
                }],
            }],
        }
        config = BattleZankouFormConfig(0.25, 0.25, 8.0, 8.0, 8.0)
        time_stops = ((2_000_000, 4_000_000),)
        form_intervals = BattleZankouFormBuffService.infer(
            build=build,
            actions=(),
            battle_end_us=6_000_000,
            config=config,
            time_stop_intervals=time_stops,
        )

        events = BattleTreatmentEventService.infer(
            build=build,
            actions=(),
            battle_end_us=6_000_000,
            time_stop_intervals=time_stops,
            state_buff_intervals=form_intervals,
            zankou_effect_three_recover_ratio=config.effect_three_recover_ratio,
        )
        zankou = tuple(
            row for row in events
            if row.treatment_kind == "zankou_effect3_huo_period"
        )

        self.assertEqual((1_000_000, 4_000_000, 5_000_000), tuple(
            row.relative_time_us for row in zankou
        ))
        self.assertTrue(all(row.raw_healing_amount == 400.0 for row in zankou))


if __name__ == "__main__":
    unittest.main()
