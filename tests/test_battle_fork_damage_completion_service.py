# 验证剩余伤害弧盘的消费者、叠层、时序和精炼参数契约。
from __future__ import annotations

from dataclasses import replace
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
from src.services.battle_target_control_policy_service import (
    CONTROL_BLOCKED_BOSS, CONTROL_CONFIRMED_ALL_BOSS,
)


def _selected(
    owner_id: str,
    parameters: dict[str, float],
    *,
    character_id: int = 1001,
) -> SimpleNamespace:
    return SimpleNamespace(
        effect_definition_id=f"fork_star:{owner_id}:1",
        character_id=character_id,
        character_name="弧盘装备者",
        definition={"parameters": parameters},
    )


def _rules(
    owner_id: str,
    parameters: dict[str, float],
    *,
    character_id: int = 1001,
):
    return BattleForkRefinementService.rules_for_selected_effect(
        _selected(owner_id, parameters, character_id=character_id),
        BattleStaticBuffRule,
    )


def _action(
    ordinal: int,
    character_id: int,
    input_kind: str,
    start_us: int,
    end_us: int,
    *,
    action_name: str | None = None,
    gameplay_effect_ids: tuple[str, ...] | None = None,
) -> BattleInferredAction:
    return BattleInferredAction(
        action_id=f"action:{ordinal}",
        character_id=character_id,
        character_name=f"角色{character_id}",
        action_name=action_name or input_kind,
        input_kind=input_kind,
        input_sequence=input_kind,
        start_us=start_us,
        end_us=end_us,
        hits=1,
        damage=1000.0,
        identity_confidence="中",
        timing_confidence="中",
        inference_basis="test",
        evidence_event_ids=(f"event:{ordinal}",),
        gameplay_effect_ids=(
            gameplay_effect_ids
            if gameplay_effect_ids is not None
            else (f"GE_Test_{input_kind}",)
        ),
    )


def _hit(
    ordinal: int,
    time_us: int,
    *,
    input_kind: str = "A",
    damage_attribute: str = "nature",
    channel: str = "direct",
    damage: float = 1000.0,
    gameplay_effect_id: str = "",
    target_id: str = "target",
) -> BattleAnalysisHit:
    attack_types = {
        "A": "普攻",
        "E": "E技能",
        "Q": "Q技能",
        "QTE": "环合·测试",
        "COUNTER": "闪避反击",
    }
    abilities = {
        "A": "GA_Test_Melee",
        "E": "GA_Test_Skill",
        "Q": "GA_Test_UltraSkill",
        "QTE": "GA_Test_QTE",
        "COUNTER": "GA_Test_ExtremEvadeAtk",
    }
    return BattleAnalysisHit(
        event_id=f"hit:{ordinal}",
        sequence=ordinal,
        relative_time_us=time_us,
        character_id=1001,
        character_name="弧盘装备者",
        skill_name=f"测试{input_kind}",
        damage_name="测试伤害",
        damage_component="dot" if channel == "dot" else "skill",
        attack_type=attack_types[input_kind],
        damage_attribute=damage_attribute,
        target_id=target_id,
        target_name="目标",
        damage=damage,
        direction="outgoing",
        is_follow_up=False,
        classification=channel,
        ability_id=abilities[input_kind],
        gameplay_effect_id=(
            gameplay_effect_id or f"GE_Test_{input_kind}_Damage"
        ),
    )


def _property(projection, property_id: str) -> float | None:
    return next(
        (
            row.additive_value
            for row in projection.modifiers
            if row.property_id == property_id
        ),
        None,
    )


def _project(
    rules,
    actions,
    hits,
    hit,
    *,
    battle_end_us=40_000_000,
    time_stop_intervals=(),
    target_control_policy="eligible_default",
):
    intervals = BattleBuffInferenceService.infer(
        rules,
        actions=actions,
        hits=hits,
        battle_end_us=battle_end_us,
        time_stop_intervals=time_stop_intervals,
        target_control_policy=target_control_policy,
    )
    return intervals, BattleBuffAttributeProjectionService.project_hit(hit, intervals)


class BattleForkDamageCompletionServiceTests(unittest.TestCase):
    def test_time_q_crit_window_only_covers_the_consuming_q(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_Time",
            {
                "buff_Time_AtkUp": 0.16,
                "buff_Time_stateCritDamageUp": 0.24,
                "buff_Time_CritDamageUp": 0.08,
                "buff_Time_DefIgnore": 0.12,
                "buff_Time_DefIgnore_Dur": 70.0,
            },
        )
        actions = (
            _action(1, 1001, "E", 1_000_000, 2_000_000),
            _action(2, 2002, "E", 3_000_000, 4_000_000),
            _action(3, 1001, "Q", 8_000_000, 9_000_000),
        )
        intervals, _projection = _project(
            rules,
            actions,
            (),
            _hit(1, 8_500_000, input_kind="Q"),
            battle_end_us=100_000_000,
            time_stop_intervals=((20_000_000, 30_000_000),),
        )

        time_interval = next(
            row for row in intervals if "消耗荒时强化 Q" in row.buff_name
        )
        self.assertEqual(8_000_000, time_interval.start_us)
        self.assertEqual(9_000_000, time_interval.end_us)
        self.assertFalse(any("无视防御" in row.buff_name for row in intervals))

    def test_confirmed_default_stacks_apply_to_mamen_and_jingmo(self) -> None:
        mamen = _rules(
            "upgradestar_pack_fork_mamen",
            {"buff_mamen_fons": 100000.0, "buff_mamen_CosmosUp": 0.025},
        )
        _, mamen_projection = _project(
            mamen, (), (), _hit(1, 1_000_000, damage_attribute="cosmos")
        )
        jingmo = _rules(
            "upgradestar_pack_fork_jingmotingyuan",
            {
                "buff_tingyuan_CritDamageUp": 0.12,
                "buff_tingyuan_HPreduce": 0.05,
                "buff_tingyuan_CD": 5.0,
                "buff_tingyuan_CD2": 25.0,
                "GE_Fork_serenity_Skill1_Damage1": 0.24,
                "GE_Fork_serenity_Skill2_Damage": 0.18,
            },
        )
        _, jingmo_projection = _project(
            jingmo, (), (), _hit(2, 1_000_000)
        )

        self.assertAlmostEqual(
            0.25, _property(mamen_projection, "DamageUpCosmosBase")
        )
        self.assertAlmostEqual(
            0.48, _property(jingmo_projection, "CritDamageBase")
        )
        self.assertIsNone(
            _property(jingmo_projection, "DerivedDamageCoefficient")
        )

    def test_missing_player_state_uses_high_hp_and_no_shield_defaults(self) -> None:
        high_hp = _rules(
            "upgradestar_pack_fork_wuhuakuang",
            {"buff_kudangao_Atk": 0.20, "buff_kudangao_Def": 0.20},
        )
        _, high_hp_projection = _project(
            high_hp, (), (), _hit(1, 1_000_000)
        )
        shield = _rules(
            "upgradestar_pack_fork_snowman",
            {"buff_snowman_AtkUp": 0.18},
        )
        _, shield_projection = _project(
            shield, (), (), _hit(2, 1_000_000)
        )

        self.assertAlmostEqual(0.20, _property(high_hp_projection, "AtkUp"))
        self.assertIsNone(_property(high_hp_projection, "DefUp"))
        self.assertIsNone(_property(shield_projection, "AtkUp"))

    def test_remaining_catalog_entries_have_explicit_completion_rules(self) -> None:
        remaining = {
            "upgradestar_pack_fork_dustbin": {
                "buff_dustbin_Unbal": 60.0,
                "buff_dustbin_CD": 10.0,
                "buff_dustbin_CD2": 20.0,
            },
            "upgradestar_pack_fork_jingmotingyuan": {
                "buff_tingyuan_CritDamageUp": 0.12,
                "buff_tingyuan_HPreduce": 0.05,
                "buff_tingyuan_CD": 5.0,
                "buff_tingyuan_CD2": 25.0,
                "GE_Fork_serenity_Skill1_Damage1": 0.24,
                "GE_Fork_serenity_Skill2_Damage": 0.18,
            },
            "upgradestar_pack_fork_koinobori": {
                "buff_koinobori_AtkUp": 0.10,
                "buff_koinobori_DefUp": 0.10,
                "buff_koinobori_HpMaxUp": 0.10,
            },
            "upgradestar_pack_fork_lingganzhongjiezhe": {
                "buff_xiangdao_energy": 10.0,
                "buff_xiangdao_CD": 20.0,
            },
            "upgradestar_pack_fork_mamen": {
                "buff_mamen_fons": 100000.0,
                "buff_mamen_CosmosUp": 0.025,
            },
            "upgradestar_pack_fork_snowman": {"buff_snowman_AtkUp": 0.18},
            "upgradestar_pack_fork_tuansanlang": {"buff_tuansanlang_Unbal": 70.0},
            "upgradestar_pack_fork_vine": {
                "buff_vine_Hp": 0.12,
                "buff_vine_CD": 20.0,
            },
            "upgradestar_pack_fork_wuhuakuang": {
                "buff_kudangao_Atk": 0.20,
                "buff_kudangao_Def": 0.20,
            },
            "upgradestar_pack_fork_yaodao": {
                "GE_Fork_yaodao_Skill_Damage": 2.0,
            },
            "upgradestar_pack_fork_yuren": {
                "buff_bowenchanggui_HP": 0.10,
                "buff_bowenchanggui_shield": 0.10,
            },
        }

        for owner_id, parameters in remaining.items():
            with self.subTest(owner_id=owner_id):
                self.assertTrue(BattleForkRefinementService.owns_effect(owner_id))
                self.assertTrue(_rules(owner_id, parameters))

    def test_static_skill_consumers_do_not_leak_to_other_attack_types(self) -> None:
        normal_rules = _rules(
            "upgradestar_pack_fork_Prokaryon",
            {"buff_Prokaryon_Up": 0.12},
        )
        skill_rules = _rules(
            "upgradestar_pack_fork_appliance",
            {"buff_appliance_Up": 0.12},
        )
        _, normal = _project(normal_rules, (), (), _hit(1, 1_000_000))
        _, normal_on_e = _project(
            normal_rules, (), (), _hit(2, 1_000_000, input_kind="E")
        )
        _, skill = _project(
            skill_rules, (), (), _hit(3, 1_000_000, input_kind="E")
        )
        _, skill_on_q = _project(
            skill_rules, (), (), _hit(4, 1_000_000, input_kind="Q")
        )

        self.assertAlmostEqual(0.12, _property(normal, "DamageUpGeneralBase"))
        self.assertIsNone(_property(normal_on_e, "DamageUpGeneralBase"))
        self.assertAlmostEqual(0.12, _property(skill, "DamageUpGeneralBase"))
        self.assertIsNone(_property(skill_on_q, "DamageUpGeneralBase"))

    def test_tiger_tally_counts_e_and_q_as_two_normal_or_counter_stacks(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_TigerTally",
            {
                "buff_TigerTally_AtkUp": 0.15,
                "buff_TigerTally_NormalUp": 0.15,
                "buff_TigerTally_CD": 15.0,
                "buff_TigerTally_CD4": 15.0,
                "buff_TigerTally_Qup": 0.10,
                "buff_TigerTally_CD3": 10.0,
            },
        )
        actions = (
            _action(1, 1001, "E", 1_000_000, 2_000_000),
            _action(2, 1001, "Q", 4_000_000, 5_000_000),
        )
        intervals, normal = _project(
            rules, actions, (), _hit(1, 6_000_000),
        )
        _, counter = _project(
            rules, actions, (), _hit(2, 6_000_000, input_kind="COUNTER"),
        )
        _, skill = _project(
            rules, actions, (), _hit(3, 6_000_000, input_kind="E"),
        )

        self.assertAlmostEqual(0.15, _property(normal, "AtkUp"))
        self.assertAlmostEqual(0.30, _property(normal, "DamageUpGeneralBase"))
        self.assertAlmostEqual(0.30, _property(counter, "DamageUpGeneralBase"))
        self.assertIsNone(_property(skill, "DamageUpGeneralBase"))
        self.assertEqual(
            2,
            sum(
                row.stacks
                for row in intervals
                if "普攻与极限反击" in row.buff_name
                and row.start_us <= 6_000_000 < row.end_us
            ),
        )
        commander = next(row for row in intervals if "司令虎符" in row.buff_name)
        self.assertEqual(4_000_000, commander.start_us)

    def test_tiger_commander_starts_when_second_formal_token_arrives(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_TigerTally",
            {
                "buff_TigerTally_AtkUp": 0.15,
                "buff_TigerTally_NormalUp": 0.15,
                "buff_TigerTally_CD": 15.0,
                "buff_TigerTally_CD4": 15.0,
                "buff_TigerTally_Qup": 0.10,
                "buff_TigerTally_CD3": 10.0,
            },
        )
        actions = (
            _action(1, 1001, "E", 1_000_000, 1_500_000),
            _action(2, 1001, "Q", 2_000_000, 2_500_000),
        )
        intervals, unresolved_target = _project(
            rules,
            actions,
            (),
            _hit(5, 2_200_000, input_kind="E"),
        )
        commander = tuple(
            row for row in intervals if "司令虎符" in row.buff_name
        )

        self.assertEqual(1, len(commander))
        self.assertEqual(2_000_000, commander[0].start_us)
        self.assertEqual("中", commander[0].state_confidence)
        self.assertIn("第二枚正式虎符", commander[0].inference_basis)
        self.assertIsNone(_property(unresolved_target, "DamageUpGeneralBase"))
        self.assertEqual(("Con_IsBoss",), commander[0].modifiers[0].target_require_tags)
        self.assertTrue(any(
            "缺少正式目标标签状态" in reason
            for reason in unresolved_target.exclusion_reasons
        ))

    def test_tiger_commander_consumes_formally_resolved_boss_requirement(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_TigerTally",
            {
                "buff_TigerTally_AtkUp": 0.15,
                "buff_TigerTally_NormalUp": 0.15,
                "buff_TigerTally_CD": 15.0,
                "buff_TigerTally_CD4": 15.0,
                "buff_TigerTally_Qup": 0.10,
                "buff_TigerTally_CD3": 10.0,
            },
        )
        actions = (
            _action(1, 1001, "E", 1_000_000, 1_500_000),
            _action(2, 1001, "Q", 2_000_000, 2_500_000),
        )
        intervals, projection = _project(
            rules,
            actions,
            (),
            _hit(3, 2_200_000, input_kind="E"),
            target_control_policy=CONTROL_CONFIRMED_ALL_BOSS,
        )
        commander = next(row for row in intervals if "司令虎符" in row.buff_name)
        self.assertEqual((), commander.modifiers[0].target_require_tags)
        self.assertIn("正式怪物目录", commander.inference_basis)
        self.assertAlmostEqual(0.10, _property(projection, "DamageUpGeneralBase"))

    def test_tiger_cat_damage_fragment_is_not_a_formal_q_token(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_TigerTally",
            {
                "buff_TigerTally_AtkUp": 0.15,
                "buff_TigerTally_NormalUp": 0.15,
                "buff_TigerTally_CD": 15.0,
                "buff_TigerTally_CD4": 15.0,
                "buff_TigerTally_Qup": 0.10,
                "buff_TigerTally_CD3": 10.0,
            },
        )
        actions = (
            _action(1, 1001, "E", 1_000_000, 1_500_000),
            _action(
                2,
                1001,
                "Q",
                2_000_000,
                2_500_000,
                gameplay_effect_ids=("GE_Player_Nanally_Cat_Skill_Damage",),
            ),
        )
        intervals, projection = _project(
            rules, actions, (), _hit(3, 3_000_000),
        )

        self.assertFalse(any("司令虎符" in row.buff_name for row in intervals))
        self.assertAlmostEqual(0.15, _property(projection, "DamageUpGeneralBase"))

    def test_tiger_left_token_is_not_available_until_e_action_finishes(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_TigerTally",
            {
                "buff_TigerTally_AtkUp": 0.15,
                "buff_TigerTally_NormalUp": 0.15,
                "buff_TigerTally_CD": 15.0,
                "buff_TigerTally_CD4": 15.0,
                "buff_TigerTally_Qup": 0.10,
                "buff_TigerTally_CD3": 10.0,
            },
        )
        actions = (
            _action(1, 1001, "E", 1_000_000, 4_000_000),
            _action(2, 1001, "Q", 2_000_000, 2_500_000),
        )
        intervals, _projection = _project(
            rules,
            actions,
            (),
            _hit(5, 3_200_000),
        )

        commander = next(row for row in intervals if "司令虎符" in row.buff_name)
        self.assertEqual(4_000_000, commander.start_us)

    def test_rose_e_immediately_grants_full_crit_damage_stack(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_Rose",
            {
                "buff_Rose_AtkUp": 0.14,
                "buff_Rose_CritDamageUp": 0.06,
                "buff_Rose_CD": 3.0,
                "buff_Rose_UnbalTime": 3.0,
            },
        )
        actions = (_action(1, 1001, "E", 1_000_000, 2_000_000),)
        _, projection = _project(
            rules, actions, (), _hit(1, 1_500_000),
        )

        self.assertAlmostEqual(0.14, _property(projection, "AtkUp"))
        self.assertAlmostEqual(0.60, _property(projection, "CritDamageBase"))

    def test_time_consumes_teammate_actions_for_q_crit_damage(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_Time",
            {
                "buff_Time_AtkUp": 0.16,
                "buff_Time_stateCritDamageUp": 0.24,
                "buff_Time_CritDamageUp": 0.08,
                "buff_Time_DefIgnore": 0.12,
                "buff_Time_DefIgnore_Dur": 70.0,
            },
        )
        actions = (
            _action(1, 1001, "E", 1_000_000, 2_000_000),
            _action(2, 2002, "E", 3_000_000, 4_000_000),
            _action(3, 2003, "QTE", 5_000_000, 6_000_000),
            _action(4, 1001, "Q", 8_000_000, 9_000_000),
        )
        intervals, projection = _project(
            rules, actions, (), _hit(1, 8_500_000, input_kind="Q"),
        )

        self.assertAlmostEqual(0.16, _property(projection, "AtkUp"))
        self.assertAlmostEqual(0.40, _property(projection, "CritDamageBase"))
        self.assertIsNone(_property(projection, "DefIgnore"))
        self.assertTrue(any("消耗 2 层荒时" in row.inference_basis for row in intervals))

    def test_time_three_stacks_splits_q_crit_from_all_damage_def_ignore(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_Time",
            {
                "buff_Time_AtkUp": 0.16,
                "buff_Time_stateCritDamageUp": 0.24,
                "buff_Time_CritDamageUp": 0.08,
                "buff_Time_DefIgnore": 0.12,
                "buff_Time_DefIgnore_Dur": 70.0,
            },
        )
        actions = (
            _action(1, 1001, "E", 1_000_000, 2_000_000),
            _action(2, 2002, "E", 3_000_000, 4_000_000),
            _action(3, 2003, "QTE", 5_000_000, 6_000_000),
            _action(4, 2004, "E", 6_500_000, 7_000_000),
            _action(5, 1001, "Q", 8_000_000, 9_000_000),
        )
        intervals, q_projection = _project(
            rules,
            actions,
            (),
            _hit(1, 8_500_000, input_kind="Q"),
            battle_end_us=100_000_000,
            time_stop_intervals=((20_000_000, 30_000_000),),
        )
        _, later_projection = _project(
            rules,
            actions,
            (),
            _hit(2, 50_000_000, input_kind="E"),
            battle_end_us=100_000_000,
            time_stop_intervals=((20_000_000, 30_000_000),),
        )
        crit = next(row for row in intervals if "强化 Q" in row.buff_name)
        defence = next(row for row in intervals if "无视防御" in row.buff_name)
        self.assertEqual((8_000_000, 9_000_000), (crit.start_us, crit.end_us))
        defence_window = (defence.start_us, defence.end_us)
        self.assertEqual((8_000_000, 88_000_000), defence_window)
        self.assertAlmostEqual(0.48, _property(q_projection, "CritDamageBase"))
        self.assertAlmostEqual(0.12, _property(q_projection, "DefIgnore"))
        self.assertIsNone(_property(later_projection, "CritDamageBase"))
        self.assertAlmostEqual(0.12, _property(later_projection, "DefIgnore"))

    def test_time_counts_overlapping_fragments_of_one_teammate_e_once(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_Time",
            {
                "buff_Time_AtkUp": 0.16,
                "buff_Time_stateCritDamageUp": 0.24,
                "buff_Time_CritDamageUp": 0.08,
                "buff_Time_DefIgnore": 0.12,
                "buff_Time_DefIgnore_Dur": 70.0,
            },
        )
        nanally_e = {
            "action_name": "变轨技能：柯林斯·嗷呜术",
            "gameplay_effect_ids": ("GE_Player_Nanally_Skill1_Damage",),
        }
        actions = (
            _action(1, 1001, "E", 1_000_000, 2_000_000),
            _action(2, 2002, "QTE", 3_000_000, 4_000_000),
            _action(3, 2002, "E", 5_000_000, 5_531_000, **nanally_e),
            _action(4, 2002, "E", 5_134_000, 5_680_000, **nanally_e),
            _action(5, 2002, "E", 5_268_000, 6_680_000, **nanally_e),
            _action(6, 1001, "Q", 8_000_000, 9_000_000),
        )
        intervals, projection = _project(
            rules, actions, (), _hit(1, 8_500_000, input_kind="Q"),
        )

        self.assertAlmostEqual(0.40, _property(projection, "CritDamageBase"))
        self.assertIsNone(_property(projection, "DefIgnore"))
        time_interval = next(
            row for row in intervals if "消耗荒时强化 Q" in row.buff_name
        )
        self.assertIn("消耗 2 层荒时", time_interval.inference_basis)
        self.assertEqual(
            ("action:1", "action:2", "action:3", "action:4", "action:5", "action:6"),
            time_interval.evidence_action_ids,
        )

    def test_mofeikesi_defaults_unknown_target_to_controlled_after_q_hit(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_mofeikesi",
            {
                "buff_mofeikesi_ChargeGetEfficiency": 0.18,
                "buff_mofeikesi_CD": 20.0,
                "buff_mofeikesi_Atk": 0.10,
                "buff_mofeikesi_Up": 0.06,
            },
            character_id=1003,
        )
        actions = (_action(
            1,
            1003,
            "Q",
            1_000_000,
            2_000_000,
            gameplay_effect_ids=("GE_Player_Sagiri_UltraSkill1_Damage",),
        ),)
        first_q_hit = replace(
            _hit(1, 1_300_000, input_kind="Q", target_id="unknown"),
            character_id=1003,
            ability_id="GA_Sagiri_UltraSkill",
            gameplay_effect_id="GE_Player_Sagiri_UltraSkill1_Damage",
        )
        second_q_hit = replace(
            _hit(2, 1_500_000, input_kind="Q", target_id="unknown"),
            character_id=1003,
            ability_id="GA_Sagiri_UltraSkill",
            gameplay_effect_id="GE_Player_Sagiri_UltraSkill2_Damage",
        )
        intervals, projection = _project(
            rules,
            actions,
            (first_q_hit, second_q_hit),
            _hit(3, 3_000_000, input_kind="E"),
        )

        self.assertEqual(3, len(rules))
        self.assertAlmostEqual(0.16, _property(projection, "AtkUp"))
        self.assertTrue(any(
            "控制触发后额外攻击" in row.buff_name
            and row.target_scope == "team"
            and row.start_us == second_q_hit.relative_time_us
            for row in intervals
        ))

    def test_mofeikesi_does_not_default_confirmed_boss_to_controlled(self) -> None:
        rules = _rules(
            "upgradestar_pack_fork_mofeikesi",
            {
                "buff_mofeikesi_ChargeGetEfficiency": 0.18,
                "buff_mofeikesi_CD": 20.0,
                "buff_mofeikesi_Atk": 0.10,
                "buff_mofeikesi_Up": 0.06,
            },
            character_id=1003,
        )
        first_q_hit = replace(
            _hit(1, 1_300_000, input_kind="Q", target_id="boss-wire"),
            character_id=1003,
            ability_id="GA_Sagiri_UltraSkill",
            gameplay_effect_id="GE_Player_Sagiri_UltraSkill1_Damage",
        )
        second_q_hit = replace(
            _hit(2, 1_500_000, input_kind="Q", target_id="boss-wire"),
            character_id=1003,
            ability_id="GA_Sagiri_UltraSkill",
            gameplay_effect_id="GE_Player_Sagiri_UltraSkill2_Damage",
        )
        intervals, projection = _project(
            rules,
            (_action(
                1,
                1003,
                "Q",
                1_000_000,
                2_000_000,
                gameplay_effect_ids=("GE_Player_Sagiri_UltraSkill1_Damage",),
            ),),
            (first_q_hit, second_q_hit),
            _hit(3, 3_000_000, input_kind="E"),
            target_control_policy=CONTROL_BLOCKED_BOSS,
        )

        self.assertAlmostEqual(0.10, _property(projection, "AtkUp"))
        blocked = next(
            row for row in intervals if "控制触发后额外攻击" in row.buff_name
        )
        self.assertEqual("unknown", blocked.target_scope)
        self.assertIn("Boss", blocked.inference_basis)

    def test_mofeikesi_requires_formal_control_producer_and_keeps_q_window_end(
        self,
    ) -> None:
        rules = _rules(
            "upgradestar_pack_fork_mofeikesi",
            {
                "buff_mofeikesi_ChargeGetEfficiency": 0.18,
                "buff_mofeikesi_CD": 20.0,
                "buff_mofeikesi_Atk": 0.10,
                "buff_mofeikesi_Up": 0.06,
            },
            character_id=1003,
        )
        action = _action(
            1,
            1003,
            "Q",
            1_000_000,
            2_000_000,
            gameplay_effect_ids=("GE_Player_Sagiri_UltraSkill1_Damage",),
        )
        no_control, projection = _project(
            rules,
            (action,),
            (),
            _hit(2, 3_000_000, input_kind="E"),
        )
        self.assertAlmostEqual(0.10, _property(projection, "AtkUp"))
        self.assertFalse(any(
            "控制触发后额外攻击" in row.buff_name for row in no_control
        ))

        late_action = replace(action, end_us=20_000_000)
        first_control_hit = replace(
            _hit(3, 18_500_000, input_kind="Q"),
            character_id=1003,
            ability_id="GA_Sagiri_UltraSkill",
            gameplay_effect_id="GE_Player_Sagiri_UltraSkill1_Damage",
        )
        late_control_hit = replace(
            _hit(4, 19_000_000, input_kind="Q"),
            character_id=1003,
            ability_id="GA_Sagiri_UltraSkill",
            gameplay_effect_id="GE_Player_Sagiri_UltraSkill2_Damage",
        )
        intervals, _ = _project(
            rules,
            (late_action,),
            (first_control_hit, late_control_hit),
            _hit(5, 20_000_000, input_kind="E"),
        )
        base = next(row for row in intervals if "Q 后全队攻击力" in row.buff_name)
        extra = next(
            row for row in intervals if "控制触发后额外攻击" in row.buff_name
        )
        self.assertEqual(base.end_us, extra.end_us)

    def test_moon_and_oula_use_hit_timed_independent_stacks(self) -> None:
        moon_rules = _rules(
            "upgradestar_pack_fork_moon",
            {
                "buff_moon_PsycheUp": 0.12,
                "buff_moon_CritDamageUp": 0.02,
                "buff_moon_CD": 5.0,
            },
        )
        psyche_hits = (
            _hit(1, 1_000_000, damage_attribute="psyche"),
            _hit(2, 1_200_000, damage_attribute="psyche"),
        )
        _, moon = _project(
            moon_rules,
            (),
            psyche_hits,
            _hit(3, 2_000_000, damage_attribute="psyche"),
        )
        oula_rules = _rules(
            "upgradestar_pack_fork_oulaquantao",
            {"buff_wujinjieti_CD": 10.0, "buff_wujinjieti_Up": 0.02},
        )
        normal_hits = tuple(_hit(index, index * 100_000) for index in range(1, 13))
        _, oula = _project(
            oula_rules, (), normal_hits, _hit(20, 2_000_000),
        )

        self.assertAlmostEqual(0.12, _property(moon, "DamageUpPsycheBase"))
        self.assertAlmostEqual(0.04, _property(moon, "CritDamageBase"))
        self.assertAlmostEqual(0.20, _property(oula, "DamageUpGeneralBase"))


if __name__ == "__main__":
    unittest.main()
