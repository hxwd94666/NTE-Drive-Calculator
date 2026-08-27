# 验证已人工确认弧盘规则的触发先后、叠层与作用范围。
from __future__ import annotations

from types import SimpleNamespace
import unittest

from src.domain.battle_report import BattleAnalysisHit, BattleInferredAction
from src.services.battle_buff_inference_service import (
    BattleBuffInferenceService,
    BattleStaticBuffRule,
)
from src.services.battle_fork_refinement_service import (
    BattleForkRefinementService,
    ForkTreatmentEvent,
)


def _selected(owner_id: str, parameters: dict[str, float]) -> SimpleNamespace:
    return SimpleNamespace(
        effect_definition_id=f"fork_star:{owner_id}:1",
        character_id=1001,
        character_name="测试角色",
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
    event_count: int = 1,
) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=f"action:{ordinal}",
        character_id=1001,
        character_name="测试角色",
        action_name=input_kind,
        input_kind=input_kind,
        input_sequence=input_kind,
        start_us=start_us,
        end_us=end_us,
        hits=event_count,
        damage=1000.0,
        identity_confidence="中",
        timing_confidence="中",
        inference_basis="test",
        evidence_event_ids=tuple(
            f"event:{ordinal}:{index}" for index in range(event_count)
        ),
        gameplay_effect_ids=(f"GE_Test_{input_kind}",),
    )


def _hit(
    ordinal: int,
    time_us: int,
    *,
    target_id: str = "target",
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"hit:{ordinal}",
        sequence=ordinal,
        relative_time_us=time_us,
        character_id=1001,
        character_name="测试角色",
        skill_name="测试攻击",
        damage_name="测试伤害",
        damage_component="unknown",
        attack_type="普攻",
        damage_attribute="incantation",
        target_id=target_id,
        target_name=target_id,
        damage=1000.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        gameplay_effect_id="GE_Test_Incantation",
    )


class BattleForkRefinementServiceTests(unittest.TestCase):
    def test_catalog_owns_all_manually_audited_batches(self) -> None:
        for owner_id in (
            "upgradestar_pack_Fork_wushoutieyu",
            "upgradestar_pack_fork_Arachne",
            "upgradestar_pack_fork_DemonBlade",
            "upgradestar_pack_fork_Door",
            "upgradestar_pack_fork_GoldRecord",
        ):
            with self.subTest(owner_id=owner_id):
                self.assertTrue(BattleForkRefinementService.owns_effect(
                    f"fork_star:{owner_id}:1"
                ))
        for owner_id in (
            "upgradestar_pack_fork_BitGame",
            "upgradestar_pack_fork_BitterCake",
            "upgradestar_pack_fork_BlackBook",
            "upgradestar_pack_fork_BlastCandy",
            "upgradestar_pack_fork_BoxingCandy",
            "upgradestar_pack_fork_Butterfly",
            "upgradestar_pack_fork_Castle",
            "upgradestar_pack_fork_Crowbar",
            "upgradestar_pack_fork_GoldWool",
            "upgradestar_pack_fork_Kite",
            "upgradestar_pack_fork_KnightCandy",
            "upgradestar_pack_fork_LunarPhase",
            "upgradestar_pack_fork_MotorCandy",
            "upgradestar_pack_fork_Nakupeda",
            "upgradestar_pack_fork_NestBird",
            "upgradestar_pack_fork_PaperPlane",
            "upgradestar_pack_fork_PoliceRat",
        ):
            with self.subTest(owner_id=owner_id):
                self.assertTrue(BattleForkRefinementService.owns_effect(
                    f"fork_star:{owner_id}:1"
                ))

    def test_arachne_triggering_q_uses_q_begin_interval(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_Arachne",
            {
                "buff_Arachne_Hp": 0.20,
                "buff_Arachne_Up": 0.10,
                "buff_Arachne_CD": 10.0,
            },
        )
        q = _action(1, "Q", 1_000_000, 2_000_000)
        intervals = BattleBuffInferenceService.infer(
            rules,
            actions=(q,),
            hits=(),
            battle_end_us=20_000_000,
        )

        dynamic = next(
            row for row in intervals if row.buff_name == "永恒圆舞曲：心灵伤害"
        )
        self.assertEqual((1_000_000, 11_000_000), (
            dynamic.start_us,
            dynamic.end_us,
        ))

    def test_wushoutieyu_e_stack_starts_after_action_end_and_caps_at_two(self) -> None:
        rules = _rules(
            "upgradestar_pack_Fork_wushoutieyu",
            {
                "buff_wushoutieyu_Up": 0.10,
                "buff_wushoutieyu_CD": 10.0,
                "buff_wushoutieyu_Up2": 0.05,
                "buff_wushoutieyu_UpLakshana": 0.15,
            },
        )
        actions = (
            _action(1, "Q", 1_000_000, 2_000_000),
            _action(2, "E", 3_000_000, 4_000_000),
            _action(3, "E", 5_000_000, 6_000_000),
            _action(4, "E", 7_000_000, 8_000_000),
        )
        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=actions,
            hits=(),
            battle_end_us=20_000_000,
        )
        q_window = next(
            row for row in intervals if "Q 后 E/Q" in row.buff_name
        )
        stack_intervals = tuple(
            row for row in intervals if "E 增伤层数" in row.buff_name
        )

        self.assertEqual((1_000_000, 11_000_000), (
            q_window.start_us,
            q_window.end_us,
        ))
        self.assertEqual(4_000_001, stack_intervals[0].start_us)
        self.assertEqual((1, 2, 2), tuple(
            row.stacks for row in stack_intervals
        ))

    def test_wushoutieyu_q_window_never_refreshes_while_active(self) -> None:
        rules = _rules(
            "upgradestar_pack_Fork_wushoutieyu",
            {
                "buff_wushoutieyu_Up": 0.10,
                "buff_wushoutieyu_CD": 10.0,
                "buff_wushoutieyu_Up2": 0.05,
                "buff_wushoutieyu_UpLakshana": 0.15,
            },
        )
        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=(
                _action(1, "Q", 1_000_000, 2_000_000),
                _action(2, "Q", 5_000_000, 6_000_000),
            ),
            hits=(),
            battle_end_us=20_000_000,
        )

        main = next(row for row in intervals if "Q 后 E/Q 增伤" in row.buff_name)
        self.assertEqual((1_000_000, 11_000_000), (main.start_us, main.end_us))

    def test_gold_record_counts_one_stack_per_qte_action_not_per_hit(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_GoldRecord",
            {
                "buff_GoldRecord_AtkUp": 0.24,
                "buff_GoldRecord_QteCritDmaUp": 0.66,
                "buff_GoldRecord_QCritDmaUp": 0.22,
                "buff_GoldRecord_CD": 3.0,
            },
        )
        actions = (
            _action(1, "QTE", 1_000_000, 1_500_000, event_count=8),
            _action(2, "QTE", 2_000_000, 2_400_000, event_count=3),
        )
        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=actions,
            hits=(),
            battle_end_us=10_000_000,
        )
        stacks = tuple(
            row.stacks
            for row in intervals
            if "Q 暴击伤害层数" in row.buff_name
        )

        self.assertEqual((1, 2), stacks)
        stack_intervals = tuple(
            row for row in intervals if "Q 暴击伤害层数" in row.buff_name
        )
        self.assertEqual(1_500_000, stack_intervals[0].start_us)
        self.assertEqual(2_400_000, stack_intervals[-1].start_us)

    def test_door_treatment_does_not_require_positive_effective_healing(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_Door",
            {
                "buff_Door_Atk": 0.16,
                "buff_Door_OtherUp": 0.15,
                "buff_Door_Up": 0.30,
                "buff_Door_CD": 20.0,
            },
        )
        events = (
            ForkTreatmentEvent("treatment:full-hp", 1_000_000, 1001),
            ForkTreatmentEvent("treatment:refresh", 5_000_000, 1001),
        )
        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=(
                _action(1, "E", 2_000_000, 3_000_000),
                _action(2, "Q", 7_000_000, 8_000_000),
            ),
            hits=(),
            battle_end_us=30_000_000,
            treatment_events=events,
        )
        dynamic = tuple(
            row for row in intervals if row.buff_name.startswith("错误的门：")
            and row.duration_policy == "HasDuration"
        )

        self.assertEqual(2, len(dynamic))
        self.assertEqual({"self", "team_others"}, {
            row.target_scope for row in dynamic
        })
        self.assertEqual({(1_000_000, 25_000_000)}, {
            (row.start_us, row.end_us) for row in dynamic
        })
        self.assertTrue(all(
            row.evidence_event_ids
            == ("treatment:full-hp", "treatment:refresh")
            for row in dynamic
        ))
        self.assertTrue(all(not row.evidence_action_ids for row in dynamic))

    def test_door_does_not_guess_treatment_from_owner_actions(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_Door",
            {
                "buff_Door_Atk": 0.16,
                "buff_Door_OtherUp": 0.15,
                "buff_Door_Up": 0.30,
                "buff_Door_CD": 20.0,
            },
        )
        actions = (
            _action(1, "E", 1_000_000, 2_000_000),
            _action(2, "A", 3_000_000, 4_000_000),
            _action(3, "Q", 5_000_000, 6_000_000),
        )
        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=actions,
            hits=(),
            battle_end_us=30_000_000,
        )
        dynamic = tuple(
            row for row in intervals if row.buff_name.startswith("错误的门：")
            and row.duration_policy == "HasDuration"
        )

        self.assertEqual((), dynamic)

    def test_demon_blade_multi_target_hit_still_adds_only_one_stack(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_DemonBlade",
            {
                "buff_DemonBlade_Crit": 0.16,
                "buff_DemonBlade_CritDamageUp": 0.09,
                "buff_DemonBlade_CD": 15.0,
            },
        )
        hits = (
            _hit(1, 1_000_000, target_id="target:a"),
            _hit(2, 1_000_000, target_id="target:b"),
        )
        intervals = BattleForkRefinementService.infer_specialized(
            rules,
            actions=(),
            hits=hits,
            battle_end_us=20_000_000,
        )
        probe = _hit(3, 1_000_001, target_id="target:a")
        active = BattleBuffInferenceService.active_for_hit(intervals, probe)

        self.assertEqual(1, len(active))
        self.assertEqual(1, active[0].stacks)


if __name__ == "__main__":
    unittest.main()
