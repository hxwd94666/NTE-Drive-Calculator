# 验证第三批人工确认弧盘的技能时序、条件边界与逐击暴击叠层。
from __future__ import annotations

from types import SimpleNamespace
import unittest

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction
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
from src.services.battle_fork_trigger_refinement_service import (
    CASTLE_RUNTIME_DURATION_SECONDS,
    ForkCriticalEvent,
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
    input_kind: str,
    start_us: int,
    end_us: int,
    *,
    hit_count: int = 1,
) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=f"action:{ordinal}",
        character_id=1001,
        character_name="弧盘装备者",
        action_name=input_kind,
        input_kind=input_kind,
        input_sequence=input_kind,
        start_us=start_us,
        end_us=end_us,
        hits=hit_count,
        damage=1000.0,
        identity_confidence="中",
        timing_confidence="中",
        inference_basis="test",
        evidence_event_ids=tuple(
            f"action-event:{ordinal}:{index}" for index in range(hit_count)
        ),
        gameplay_effect_ids=(f"GE_Test_{input_kind}",),
    )


def _hit(
    ordinal: int,
    time_us: int,
    *,
    gameplay_effect_id: str,
    damage_attribute: str = "nature",
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"hit:{ordinal}",
        sequence=ordinal,
        relative_time_us=time_us,
        character_id=1001,
        character_name="弧盘装备者",
        skill_name="测试技能",
        damage_name="测试伤害",
        damage_component="skill",
        attack_type="Q技能",
        damage_attribute=damage_attribute,
        target_id="target",
        target_name="目标",
        damage=1000.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id="GA_Test_UltraSkill",
        gameplay_effect_id=gameplay_effect_id,
    )


def _property(projection, property_id: str) -> float | None:
    return next(
        (
            row.additive_value for row in projection.modifiers
            if row.property_id == property_id
        ),
        None,
    )


class BattleForkTriggerRefinementServiceTests(unittest.TestCase):
    def test_butterfly_q_replaces_attachment_value_and_refreshes(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_Butterfly",
            {
                "buff_Butterfly_DamageUpNatureBase": 0.15,
                "buff_Butterfly_attachedup": 0.10,
                "buff_Butterfly_attachedup2": 0.20,
                "buff_Butterfly_CD": 6.0,
            },
        )
        actions = (
            _action(1, "Q", 2_000_000, 3_000_000),
            _action(2, "Q", 5_000_000, 6_000_000),
        )
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=actions,
            hits=(),
            battle_end_us=15_000_000,
        )
        attachment = _hit(
            1,
            2_000_000,
            gameplay_effect_id="GE_Player_Kuhara_BudBoom_Damage",
        )
        normal = _hit(
            2,
            2_000_000,
            gameplay_effect_id="GE_Player_Kuhara_UltraSkill_Damage",
        )
        attachment_projection = BattleBuffAttributeProjectionService.project_hit(
            attachment,
            intervals,
        )
        normal_projection = BattleBuffAttributeProjectionService.project_hit(
            normal,
            intervals,
        )

        self.assertAlmostEqual(
            0.20,
            _property(attachment_projection, "DamageUpGeneralBase"),
        )
        self.assertAlmostEqual(
            0.15,
            _property(attachment_projection, "DamageUpNatureBase"),
        )
        self.assertIsNone(_property(normal_projection, "DamageUpGeneralBase"))
        window = next(row for row in intervals if "替换档" in row.buff_name)
        self.assertEqual((2_000_000, 11_000_000), (window.start_us, window.end_us))

    def test_castle_uses_confirmed_runtime_bug_duration_from_e_end(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_Castle",
            {"buff_Castle_HealUp": 0.12, "buff_Castle_CD": 10.0},
        )
        self.assertEqual(
            CASTLE_RUNTIME_DURATION_SECONDS,
            rules[0].duration_seconds,
        )
        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=(
                _action(1, "E", 2_000_000, 3_000_000),
                _action(2, "E", 20_000_000, 21_000_000),
            ),
            hits=(),
            battle_end_us=80_000_000,
        )

        self.assertEqual(1, len(intervals))
        self.assertEqual(
            (3_000_001, 71_000_001),
            (intervals[0].start_us, intervals[0].end_us),
        )

    def test_crowbar_starts_after_triggering_e_and_refreshes(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_Crowbar",
            {"buff_Crowbar_Unbal": 90.0, "buff_Crowbar_CD": 50.0},
        )
        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=(
                _action(1, "E", 1_000_000, 2_000_000),
                _action(2, "E", 10_000_000, 11_000_000),
            ),
            hits=(),
            battle_end_us=70_000_000,
        )

        self.assertEqual(1, len(intervals))
        self.assertEqual(
            (2_000_001, 61_000_001),
            (intervals[0].start_us, intervals[0].end_us),
        )
        self.assertEqual("UnbalIntensityAdd", intervals[0].modifiers[0].property_id)

    def test_gold_wool_e_ends_late_but_q_begins_immediately(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_GoldWool",
            {
                "buff_GoldWool_Up": 0.20,
                "buff_GoldWool_CritDamageUp": 0.40,
                "buff_GoldWool_CD": 20.0,
            },
        )
        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=(
                _action(1, "E", 1_000_000, 3_000_000),
                _action(2, "Q", 10_000_000, 12_000_000),
            ),
            hits=(),
            battle_end_us=40_000_000,
        )

        self.assertEqual(1, len(intervals))
        self.assertEqual(
            (3_000_001, 30_000_000),
            (intervals[0].start_us, intervals[0].end_us),
        )
        self.assertIn("Q 开始立即生效", intervals[0].inference_basis)

    def test_kite_projects_attack_but_keeps_and_condition_unresolved(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_Kite",
            {
                "buff_Kite_AtkUp": 0.10,
                "buff_Kite_Up": 0.10,
                "buff_Kite_CD": 15.0,
            },
        )
        condition = next(row for row in rules if "延滞且浸染" in row.target_name)
        self.assertEqual("unknown", condition.target_scope)
        self.assertEqual(
            (
                "confirmed-target-state:delay",
                "confirmed-target-state:stain",
            ),
            condition.modifiers[0].target_require_tags,
        )
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(_action(1, "E", 1_000_000, 2_000_000),),
            hits=(),
            battle_end_us=20_000_000,
        )
        projection = BattleBuffAttributeProjectionService.project_hit(
            _hit(
                1,
                3_000_000,
                gameplay_effect_id="GE_Test_Skill_Damage",
                damage_attribute="lakshana",
            ),
            intervals,
        )

        self.assertAlmostEqual(0.10, _property(projection, "AtkUp"))
        self.assertIsNone(_property(projection, "DamageUpLakshanaBase"))
        self.assertTrue(any("作用对象尚未确定" in row for row in projection.exclusion_reasons))

    def test_knight_candy_requires_crit_evidence_and_uses_source_cooldown(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_KnightCandy",
            {
                "buff_KnightCandy_CritDamageUp": 0.04,
                "buff_KnightCandy_CD": 10.0,
            },
        )
        without_evidence = BattleForkRefinementService.infer_specialized(
            rules,
            actions=(),
            hits=(),
            battle_end_us=15_000_000,
        )
        self.assertEqual((), without_evidence)
        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=(),
            hits=(),
            battle_end_us=15_000_000,
            critical_events=(
                ForkCriticalEvent("crit:a", 1_000_000, 1001),
                ForkCriticalEvent("crit:b", 1_000_000, 1001),
                ForkCriticalEvent("crit:c", 1_200_000, 1001),
                ForkCriticalEvent("crit:d", 1_300_000, 1001),
                ForkCriticalEvent("crit:e", 2_000_000, 1001),
            ),
        )

        self.assertEqual((1, 2, 3), tuple(row.stacks for row in intervals))
        self.assertEqual(1_000_001, intervals[0].start_us)
        self.assertEqual(12_000_000, intervals[-1].end_us)
        self.assertEqual(
            ("crit:a", "crit:d", "crit:e"),
            intervals[-1].evidence_event_ids,
        )


if __name__ == "__main__":
    unittest.main()
