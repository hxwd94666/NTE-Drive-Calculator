# 验证第二批人工确认弧盘的前后台、受击前与逐目标血线语义。
from __future__ import annotations

from types import SimpleNamespace
import unittest

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredAction,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_buff_inference_service import (
    BattleBuffInferenceService,
    BattleStaticBuffRule,
)
from src.services.battle_fork_refinement_service import (
    BattleForkRefinementService,
)
from src.services.battle_fork_state_refinement_service import (
    BattleForkStateRefinementService,
)


def _selected(owner_id: str, parameters: dict[str, float]) -> SimpleNamespace:
    return SimpleNamespace(
        effect_definition_id=f"fork_star:{owner_id}:1",
        character_id=1001,
        character_name="弧盘装备者",
        definition={"parameters": parameters},
    )


def _rules(owner_id: str, parameters: dict[str, float]):
    return BattleForkRefinementService.rules_for_selected_effect(
        _selected(owner_id, parameters),
        BattleStaticBuffRule,
    )


def _action(
    ordinal: int,
    character_id: int,
    input_kind: str,
    start_us: int,
    *,
    event_count: int = 1,
) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=f"action:{ordinal}",
        character_id=character_id,
        character_name=f"角色{character_id}",
        action_name=input_kind,
        input_kind=input_kind,
        input_sequence=input_kind,
        start_us=start_us,
        end_us=start_us + 500_000,
        hits=event_count,
        damage=1000.0,
        identity_confidence="中",
        timing_confidence="中",
        inference_basis="test",
        evidence_event_ids=tuple(
            f"action-event:{ordinal}:{index}" for index in range(event_count)
        ),
        gameplay_effect_ids=(f"GE_Test_{input_kind}",),
    )


def _hit(
    ordinal: int,
    character_id: int,
    time_us: int,
    *,
    direction: str = "outgoing",
    target_id: str = "target",
    attack_type: str = "普攻",
    damage_attribute: str = "psyche",
    hp_before: float | None = None,
    max_hp: float | None = None,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"hit:{ordinal}",
        sequence=ordinal,
        relative_time_us=time_us,
        character_id=character_id,
        character_name=f"角色{character_id}",
        skill_name="测试攻击",
        damage_name="测试伤害",
        damage_component="unknown",
        attack_type=attack_type,
        damage_attribute=damage_attribute,
        target_id=target_id,
        target_name=target_id,
        damage=1000.0,
        direction=direction,
        is_follow_up=False,
        classification="direct",
        ability_id="GA_Test_Melee",
        gameplay_effect_id="GE_Test_Melee_Damage",
        target_hp_before=hp_before,
        target_max_hp=max_hp,
    )


class BattleForkStateRefinementServiceTests(unittest.TestCase):
    def test_bit_game_uses_source_cooldowns_and_resets_on_owner_switch(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_BitGame",
            {
                "buff_BitGame2_AtkUp1": 0.10,
                "buff_BitGame2_AtkUp2": 0.02,
                "buff_BitGame2_CD": 2.0,
                "buff_BitGame2_DamageUpPsycheBase1": 0.12,
                "buff_BitGame2_DamageUpPsycheBase2": 0.02,
            },
        )
        actions = (
            _action(1, 2002, "E", 100_000),
            _action(2, 1001, "A", 4_000_000),
            _action(3, 2002, "E", 8_000_000),
            _action(4, 1001, "A", 10_000_000),
        )
        hits = (
            _hit(1, 1001, 1_000_000, target_id="a"),
            _hit(2, 1001, 1_000_000, target_id="b"),
            _hit(3, 1001, 3_100_000),
            _hit(4, 1001, 5_000_000, target_id="a"),
            _hit(5, 1001, 5_000_000, target_id="b"),
            _hit(6, 1001, 5_100_000),
            _hit(7, 1001, 5_400_000),
            _hit(8, 1001, 10_500_000),
        )

        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=actions,
            hits=hits,
            battle_end_us=12_000_000,
        )
        back = tuple(
            row for row in intervals if "后台伤害攻击层数" in row.buff_name
        )
        front = tuple(
            row for row in intervals if "前台普攻心灵层数" in row.buff_name
        )

        self.assertEqual((1, 2), tuple(row.stacks for row in back))
        self.assertEqual((1, 2, 1), tuple(row.stacks for row in front))
        self.assertEqual(10_500_001, front[-1].start_us)
        self.assertEqual("character:2002", back[0].target_scope)
        active_projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(20, 2002, 2_000_000),
            intervals,
        )
        third_projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(21, 3003, 2_000_000),
            intervals,
        )
        self.assertAlmostEqual(
            0.12,
            next(
                row.additive_value
                for row in active_projection.modifiers
                if row.property_id == "AtkUp"
            ),
        )
        self.assertFalse(any(
            row.property_id == "AtkUp" for row in third_projection.modifiers
        ))

    def test_bitter_cake_starts_before_triggering_incoming_hit_calculation(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_BitterCake",
            {
                "buff_BitterCake_DefUp": 0.26,
                "buff_BitterCake_CD": 10.0,
                "buff_BitterCake_CD2": 20.0,
            },
        )
        incoming = (
            _hit(1, 1001, 1_000_000, direction="incoming"),
            _hit(2, 1001, 5_000_000, direction="incoming"),
            _hit(3, 1001, 21_000_000, direction="incoming"),
        )
        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=(),
            hits=incoming,
            battle_end_us=35_000_000,
        )

        self.assertEqual(
            ((1_000_000, 11_000_000), (21_000_000, 31_000_000)),
            tuple((row.start_us, row.end_us) for row in intervals),
        )
        self.assertEqual(
            ("hit:1",),
            BattleBuffInferenceService.active_for_hit(intervals, incoming[0])[0]
            .evidence_event_ids,
        )

    def test_blast_candy_triggering_q_gets_attack_from_q_start(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_BlastCandy",
            {"buff_BlastCandy_AtkUp": 0.25, "buff_BlastCandy_CD": 10.0},
        )
        q = _action(1, 1001, "Q", 2_000_000)
        refresh = _action(2, 1001, "Q", 8_000_000)
        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=(q, refresh),
            hits=(),
            battle_end_us=20_000_000,
        )

        self.assertEqual((2_000_000, 18_000_000), (
            intervals[0].start_us,
            intervals[0].end_us,
        ))
        self.assertEqual(("action:1", "action:2"), intervals[0].evidence_action_ids)

    def test_boxing_candy_uses_each_targets_pre_hit_hp_and_exact_half_is_base(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_BoxingCandy",
            {"buff_BoxingCandy_Up": 0.22, "buff_BoxingCandy_Up2": 0.28},
        )
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(),
            hits=(),
            battle_end_us=10_000_000,
        )
        exact = _hit(
            1,
            1001,
            1_000_000,
            target_id="exact-half",
            hp_before=500.0,
            max_hp=1000.0,
        )
        below = _hit(
            2,
            1001,
            1_000_000,
            target_id="below-half",
            hp_before=499.0,
            max_hp=1000.0,
        )

        exact_projection = BattleBuffAttributeProjectionService.project_hit(
            exact,
            intervals,
        )
        below_projection = BattleBuffAttributeProjectionService.project_hit(
            below,
            intervals,
        )

        self.assertEqual(0.22, exact_projection.modifiers[0].additive_value)
        self.assertEqual(0.28, below_projection.modifiers[0].additive_value)

    def test_black_book_can_crit_and_one_qte_action_unlocks_one_chain(self) -> None:
        selected = _selected(
            "upgradestar_pack_fork_BlackBook",
            {
                "buff_BlackBook2_Unbal": 60.0,
                "buff_BlackBook2_CD": 20.0,
                "buff_BlackBook2_CD2": 5.0,
                "buff_BlackBook2_DamageUpChaosBase": 0.20,
                "buff_BlackBook2_SkillDamage": 2.0,
            },
        )
        semantics = BattleForkStateRefinementService.black_book_semantics(selected)
        assert semantics is not None
        progress = BattleForkStateRefinementService.black_book_chain_progress(
            (
                _action(1, 2002, "QTE", 2_000_000, event_count=8),
                _action(2, 3003, "QTE", 3_000_000, event_count=3),
            ),
            tracking_start_us=1_000_000,
            tracking_end_us=10_000_000,
        )

        self.assertEqual(0.0, semantics.initial_target_delay_seconds)
        self.assertEqual(0.20, semantics.linked_dark_damage_bonus)
        self.assertEqual(2.0, semantics.attack_coefficient)
        self.assertTrue(semantics.derived_hit_can_crit)
        self.assertTrue(semantics.derived_hit_uses_linked_bonus)
        self.assertEqual((0, 1, 2), tuple(
            row.unlocked_chains for row in progress
        ))
        self.assertEqual((False, False, True), tuple(
            row.summon_ready for row in progress
        ))

if __name__ == "__main__":
    unittest.main()
