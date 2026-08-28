# 验证援护技自身直伤和真正环合伤害不会因“环合·”展示前缀混淆。
from __future__ import annotations

import unittest

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleCharacterSummary,
    BattleHitReplayFactor,
    BattleHitReplayResult,
    BattleMaxHpReductionEvent,
    BattleRangeRoleSummary,
    BattleSkillSummary,
)
from src.services.battle_damage_composition_service import (
    BattleDamageCompositionService,
    classify_battle_hit_channel,
)


class BattleDamageCompositionServiceTests(unittest.TestCase):
    def test_daffodill_extra_unbalance_is_a_topple_channel(self) -> None:
        hit = BattleAnalysisHit(
            event_id="daffodill:extra",
            sequence=1,
            relative_time_us=1,
            character_id=1054,
            character_name="达芙蒂尔",
            skill_name="完美真相",
            damage_name="额外倾陷伤害",
            damage_component="skill",
            attack_type="Passive Damage",
            damage_attribute="true",
            target_id="target",
            target_name="目标",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
            gameplay_effect_id="GE_Player_Daffodill_ExtraUnbalance_Damage",
        )

        self.assertEqual(
            ("special_daffodill_extra_topple", "达芙蒂尔·额外倾陷伤害"),
            classify_battle_hit_channel(hit),
        )

    def test_fully_missing_source_identity_stays_in_unattributed(self) -> None:
        hit = BattleAnalysisHit(
            event_id="3:primary",
            sequence=3,
            relative_time_us=0,
            character_id=1004,
            character_name="安魂曲",
            skill_name="未归因伤害",
            damage_name="来源字段缺失",
            damage_component="",
            attack_type="",
            damage_attribute="unknown",
            target_id="",
            target_name="",
            damage=5_187.0,
            direction="outgoing",
            is_follow_up=False,
            classification="direct",
        )

        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=(BattleRangeRoleSummary(
                character_id=1004,
                character_name="安魂曲",
                hits=1,
                damage=5_187.0,
                dps=5_187.0,
                share_percent=100.0,
            ),),
            hits=(hit,),
            segment_total_damage=5_187.0,
        )

        self.assertEqual((), composition.roles)
        self.assertEqual(5_187.0, composition.other_total_damage)
        self.assertEqual("来源字段缺失", composition.other_entries[0].label)

    def test_lacrimosa_dissonance_damage_is_not_the_dissonance_reaction(self) -> None:
        hit = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1,
            character_id=1004,
            character_name="安魂曲",
            skill_name="失谐",
            damage_name="失谐",
            damage_component="skill",
            attack_type="失谐",
            damage_attribute="chaos",
            target_id="target",
            target_name="目标",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="reaction",
            gameplay_effect_id="GE_Player_Lacrimosa_AnHunZhouTwo_Damage",
        )

        self.assertEqual(
            ("special_lacrimosa_dissonance", "失谐强化伤害"),
            classify_battle_hit_channel(hit),
        )

    def test_qte_damage_is_direct_but_explicit_reaction_effect_stays_reaction(self) -> None:
        composition = BattleDamageCompositionService.calculate(
            characters=(
                BattleCharacterSummary(
                    character_id=1004,
                    name="安魂曲",
                    hits=2,
                    damage=150.0,
                    dps=150.0,
                    damage_share_percent=100.0,
                ),
            ),
            skills=(
                BattleSkillSummary(
                    character_id=1004,
                    character_name="安魂曲",
                    name="援护技",
                    category="环合·浊燃",
                    hits=1,
                    damage=100.0,
                    damage_share_percent=66.67,
                    ability_name="GA_Lacrimosa_QTE",
                    gameplay_effect_name="GE_Player_Lacrimosa_QTE1_Damage",
                ),
                BattleSkillSummary(
                    character_id=1004,
                    character_name="安魂曲",
                    name="浊燃",
                    category="环合·浊燃",
                    hits=1,
                    damage=50.0,
                    damage_share_percent=33.33,
                    ability_name="GA_Lacrimosa_QTE",
                    gameplay_effect_name="Buff_Reaction_5_new_1036",
                ),
            ),
            segment_total_damage=150.0,
        )

        entries = {entry.key: entry.damage for entry in composition.roles[0].entries}
        self.assertEqual(100.0, entries["direct"])
        self.assertEqual(50.0, entries["reaction_scorch"])

    def test_formal_direct_source_wins_polluted_scorch_display_labels(self) -> None:
        hit = BattleAnalysisHit(
            event_id="87:primary",
            sequence=87,
            relative_time_us=29_886_698,
            character_id=1036,
            character_name="残虹",
            skill_name="变轨技能：绯影闪",
            damage_name="浊燃",
            damage_component="绯影闪",
            attack_type="浊燃",
            damage_attribute="incantation",
            target_id="target",
            target_name="目标",
            damage=32_718.0,
            direction="outgoing",
            is_follow_up=False,
            classification="reaction",
            ability_id="GA_Zankou_Skill",
            gameplay_effect_id="GE_Player_Zankou_Skill1_Damage",
        )

        self.assertEqual(
            ("direct", "直伤"),
            classify_battle_hit_channel(hit),
        )

    def test_topple_classification_wins_polluted_scorch_display_labels(self) -> None:
        hit = BattleAnalysisHit(
            event_id="197:primary",
            sequence=197,
            relative_time_us=55_118_246,
            character_id=1036,
            character_name="残虹",
            skill_name="浊燃",
            damage_name="浊燃",
            damage_component="unknown",
            attack_type="浊燃",
            damage_attribute="true",
            target_id="target",
            target_name="目标",
            damage=38_171.0,
            direction="outgoing",
            is_follow_up=False,
            classification="topple",
            gameplay_effect_id="Buff_Tenacity_damage",
        )

        self.assertEqual(
            ("other_topple", "倾陷伤害"),
            classify_battle_hit_channel(hit),
        )

    def test_typed_reaction_follow_up_wins_inherited_qte_source_identity(self) -> None:
        hit = BattleAnalysisHit(
            event_id="75:follow_up",
            sequence=75,
            relative_time_us=28_164_539,
            character_id=1039,
            character_name="法帝娅",
            skill_name="援护技",
            damage_name="黯星",
            damage_component="follow_up",
            attack_type="黯星",
            damage_attribute="psychically",
            target_id="target",
            target_name="目标",
            damage=57_600.0,
            direction="outgoing",
            is_follow_up=True,
            classification="reaction",
            ability_id="GA_Fadia_QTE",
            gameplay_effect_id="GE_Player_Fadia_QTE1_Damage",
        )

        self.assertEqual(
            ("reaction_nova", "黯星"),
            classify_battle_hit_channel(hit),
        )

    def test_attributed_reflection_stays_with_its_packet_character(self) -> None:
        def hit(
            event_id: str,
            damage: float,
            *,
            damage_name: str,
            classification: str,
            character_id: int | None = 1004,
            character_name: str = "安魂曲",
        ) -> BattleAnalysisHit:
            return BattleAnalysisHit(
                event_id=event_id,
                sequence=int(event_id.split(":", 1)[0]),
                relative_time_us=1_000_000,
                character_id=character_id,
                character_name=character_name,
                skill_name=damage_name,
                damage_name=damage_name,
                damage_component="skill",
                attack_type=damage_name,
                damage_attribute="CHAOS",
                target_id="target",
                target_name="目标",
                damage=damage,
                direction="outgoing",
                is_follow_up=False,
                classification=classification,
            )

        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=(
                BattleRangeRoleSummary(
                    character_id=1004,
                    character_name="安魂曲",
                    hits=3,
                    damage=180.0,
                    dps=180.0,
                    share_percent=100.0,
                ),
            ),
            hits=(
                hit("1:primary", 100.0, damage_name="直伤", classification="direct"),
                hit("2:primary", 50.0, damage_name="黯星", classification="reaction"),
                hit(
                    "3:primary",
                    30.0,
                    damage_name="敌方飞弹反射伤害",
                    classification="mechanic",
                ),
            ),
            segment_total_damage=180.0,
        )

        entries = {entry.key: entry.damage for entry in composition.roles[0].entries}
        self.assertEqual(100.0, entries["direct"])
        self.assertEqual(50.0, entries["reaction_nova"])
        self.assertEqual(30.0, entries["other_reflected_projectile"])
        self.assertEqual(180.0, composition.roles[0].total_damage)
        self.assertEqual(0.0, composition.other_total_damage)
        self.assertEqual(0.0, composition.system_total_damage)
        self.assertEqual(
            "敌方飞弹反射伤害",
            next(
                entry.label
                for entry in composition.roles[0].entries
                if entry.key == "other_reflected_projectile"
            ),
        )

    def test_reflection_without_character_evidence_stays_in_system_damage(self) -> None:
        hit = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1_000_000,
            character_id=None,
            character_name="未知角色",
            skill_name="敌方飞弹反射伤害",
            damage_name="敌方飞弹反射伤害",
            damage_component="",
            attack_type="其他",
            damage_attribute="unknown",
            target_id="target",
            target_name="目标",
            damage=30.0,
            direction="outgoing",
            is_follow_up=False,
            classification="mechanic",
            gameplay_effect_id="GE_boss_05_HitBullet_Dmg_BP",
        )

        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=(),
            hits=(hit,),
            segment_total_damage=30.0,
        )

        self.assertEqual((), composition.roles)
        self.assertEqual(0.0, composition.other_total_damage)
        self.assertEqual(30.0, composition.system_total_damage)
        self.assertEqual(
            "敌方飞弹反射伤害",
            composition.system_entries[0].label,
        )

    def test_zero_damage_unknown_role_is_not_returned(self) -> None:
        composition = BattleDamageCompositionService.calculate(
            characters=(BattleCharacterSummary(
                character_id=0,
                name="未知角色",
                hits=0,
                damage=0.0,
                dps=0.0,
                damage_share_percent=0.0,
            ),),
            skills=(),
            segment_total_damage=0.0,
        )

        self.assertEqual((), composition.roles)
        self.assertEqual((), composition.other_entries)

    def test_selected_range_adds_attributed_max_hp_settlement_channel(self) -> None:
        event = BattleMaxHpReductionEvent(
            event_id="max-hp:target:2",
            target_id="target",
            target_name="目标",
            observed_at_us=2_000_000,
            old_max_hp=1_000.0,
            new_max_hp=900.0,
            max_hp_reduction=100.0,
            hp_before_settlement=800.0,
            hp_ratio_before=0.8,
            effective_hp_loss=80.0,
            source_character_id=1004,
            source_character_name="安魂曲",
            mechanic_kind="lacrimosa_nightmare_awaken_5",
            mechanic_name="安魂曲五觉·噩梦生命上限削减",
            source_skill_name="噩梦",
            evidence_event_ids=("1:primary", "2:primary"),
            attribution_confidence="中",
            calculation_confidence="中",
            inference_basis="test",
        )
        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=(
                BattleRangeRoleSummary(
                    character_id=1004,
                    character_name="安魂曲",
                    hits=1,
                    damage=180.0,
                    dps=180.0,
                    share_percent=100.0,
                    raw_damage=100.0,
                    max_hp_reduction_damage=80.0,
                    max_hp_reduction_events=1,
                ),
            ),
            hits=(
                BattleAnalysisHit(
                    event_id="1:primary",
                    sequence=1,
                    relative_time_us=1_000_000,
                    character_id=1004,
                    character_name="安魂曲",
                    skill_name="普通攻击",
                    damage_name="直伤",
                    damage_component="skill",
                    attack_type="normal",
                    damage_attribute="CHAOS",
                    target_id="target",
                    target_name="目标",
                    damage=100.0,
                    direction="outgoing",
                    is_follow_up=False,
                    classification="direct",
                ),
            ),
            max_hp_events=(event,),
            segment_total_damage=180.0,
        )

        entries = {entry.key: entry.damage for entry in composition.roles[0].entries}
        self.assertEqual(100.0, entries["direct"])
        self.assertEqual(80.0, entries["max_hp_reduction"])
        self.assertEqual(180.0, composition.roles[0].total_damage)

    def test_coarse_channels_separate_dot_attachment_and_named_reactions(self) -> None:
        def hit(
            sequence: int,
            damage: float,
            *,
            damage_name: str,
            gameplay_effect_id: str,
            classification: str = "direct",
        ) -> BattleAnalysisHit:
            return BattleAnalysisHit(
                event_id=f"{sequence}:primary",
                sequence=sequence,
                relative_time_us=sequence,
                character_id=1055,
                character_name="九原",
                skill_name=damage_name,
                damage_name=damage_name,
                damage_component="skill",
                attack_type="Special Damage",
                damage_attribute="NATURE",
                target_id="target",
                target_name="目标",
                damage=damage,
                direction="outgoing",
                is_follow_up=False,
                classification=classification,
                gameplay_effect_id=gameplay_effect_id,
            )

        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=(BattleRangeRoleSummary(
                character_id=1055,
                character_name="九原",
                hits=5,
                damage=150.0,
                dps=150.0,
                share_percent=100.0,
            ),),
            hits=(
                hit(1, 10.0, damage_name="普通攻击", gameplay_effect_id="GE_Direct"),
                hit(2, 20.0, damage_name="噩梦", gameplay_effect_id="GE_Player_Lacrimosa_Blood_Damage"),
                hit(3, 30.0, damage_name="致命玫约持续伤害", gameplay_effect_id="GE_Player_Kuhara_Seed_Damage"),
                hit(4, 40.0, damage_name="追加清算", gameplay_effect_id="GE_Player_Kuhara_SeedReaction_Damage", classification="reaction"),
                hit(5, 50.0, damage_name="浊燃", gameplay_effect_id="Buff_Reaction_5_new", classification="reaction"),
            ),
            segment_total_damage=150.0,
            grouping="coarse",
        )

        entries = {entry.key: entry.damage for entry in composition.roles[0].entries}
        self.assertEqual(50.0, entries["direct"])
        self.assertEqual(20.0, entries["dot"])
        self.assertEqual(30.0, entries["attachment"])
        self.assertEqual(50.0, entries["reaction_scorch"])
        self.assertEqual(0.0, composition.other_total_damage)

    def test_explicit_reaction_identity_precedes_reused_dot_effect(self) -> None:
        hit = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1,
            character_id=1036,
            character_name="残虹",
            skill_name="蚀心",
            damage_name="浊燃",
            damage_component="skill",
            attack_type="Special Damage",
            damage_attribute="LAKSHANA",
            target_id="target",
            target_name="目标",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="reaction",
            gameplay_effect_id="GE_Player_Zankou_DotDamage",
        )

        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=(BattleRangeRoleSummary(
                character_id=1036,
                character_name="残虹",
                hits=1,
                damage=100.0,
                dps=100.0,
                share_percent=100.0,
            ),),
            hits=(hit,),
            segment_total_damage=100.0,
            grouping="coarse",
        )

        entries = {
            entry.key: entry.damage
            for entry in composition.roles[0].entries
        }
        self.assertEqual({"reaction_scorch": 100.0}, entries)

    def test_fine_channels_keep_distinct_damage_identities(self) -> None:
        hits = tuple(
            BattleAnalysisHit(
                event_id=f"{sequence}:primary",
                sequence=sequence,
                relative_time_us=sequence,
                character_id=1004,
                character_name="安魂曲",
                skill_name="普通攻击",
                damage_name=damage_name,
                damage_component="skill",
                attack_type="普攻",
                damage_attribute="CHAOS",
                target_id="target",
                target_name="目标",
                damage=damage,
                direction="outgoing",
                is_follow_up=False,
                classification="direct",
                gameplay_effect_id=effect,
            )
            for sequence, damage, damage_name, effect in (
                (1, 10.0, "第一段", "GE_Melee1"),
                (2, 15.0, "第一段", "GE_Melee1"),
                (3, 20.0, "第二段", "GE_Melee2"),
            )
        )
        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=(BattleRangeRoleSummary(
                character_id=1004,
                character_name="安魂曲",
                hits=3,
                damage=45.0,
                dps=45.0,
                share_percent=100.0,
            ),),
            hits=hits,
            segment_total_damage=45.0,
            grouping="fine",
        )

        rows = {entry.label: entry.damage for entry in composition.roles[0].entries}
        self.assertEqual(25.0, rows["普通攻击 · 第一段"])
        self.assertEqual(20.0, rows["普通攻击 · 第二段"])

    def test_topple_observed_damage_is_split_by_replayed_role_contributions(self) -> None:
        topple = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1,
            character_id=1004,
            character_name="安魂曲",
            skill_name="倾陷伤害",
            damage_name="倾陷伤害",
            damage_component="",
            attack_type="倾陷伤害",
            damage_attribute="CHAOS",
            target_id="target",
            target_name="目标",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="topple",
            gameplay_effect_id="Buff_Tenacity_damage",
        )
        replay = BattleHitReplayResult(
            event_id=topple.event_id,
            observed_damage=100.0,
            non_critical_damage=80.0,
            critical_damage=None,
            selected_damage=80.0,
            selected_error_percent=20.0,
            critical_state="not_applicable",
            confidence="低",
            factors=(
                BattleHitReplayFactor("topple_target", "目标档位", 2.0, "test"),
                BattleHitReplayFactor("topple_character:1004", "安魂曲倾陷贡献", 20.0, "test"),
                BattleHitReplayFactor("topple_character:1036", "残虹倾陷贡献", 60.0, "test"),
            ),
        )
        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=(
                BattleRangeRoleSummary(1004, "安魂曲", 1, 100.0, 100.0, 100.0),
                BattleRangeRoleSummary(1036, "残虹", 0, 0.0, 0.0, 0.0),
            ),
            hits=(topple,),
            hit_replays=(replay,),
            segment_total_damage=100.0,
            grouping="coarse",
        )

        roles = {row.character_id: row for row in composition.roles}
        self.assertAlmostEqual(25.0, roles[1004].total_damage)
        self.assertAlmostEqual(75.0, roles[1036].total_damage)
        self.assertEqual(25.0, roles[1004].entries[0].damage)
        self.assertEqual(75.0, roles[1036].entries[0].damage)
        self.assertEqual(0.0, composition.other_total_damage)
        self.assertFalse(composition.pending_topple_attribution)

    def test_topple_without_role_replay_is_explicitly_unattributed(self) -> None:
        topple = BattleAnalysisHit(
            event_id="1:primary",
            sequence=1,
            relative_time_us=1,
            character_id=1004,
            character_name="安魂曲",
            skill_name="倾陷伤害",
            damage_name="倾陷伤害",
            damage_component="",
            attack_type="倾陷伤害",
            damage_attribute="CHAOS",
            target_id="target",
            target_name="目标",
            damage=100.0,
            direction="outgoing",
            is_follow_up=False,
            classification="topple",
            gameplay_effect_id="Buff_Tenacity_damage",
        )

        composition = BattleDamageCompositionService.calculate_from_hits(
            roles=(BattleRangeRoleSummary(1004, "安魂曲", 1, 100.0, 100.0, 100.0),),
            hits=(topple,),
            segment_total_damage=100.0,
        )

        self.assertTrue(composition.pending_topple_attribution)
        self.assertEqual(100.0, composition.other_total_damage)
        self.assertEqual("倾陷归属待计算", composition.other_entries[0].label)

        unresolved = BattleDamageCompositionService.calculate_from_hits(
            roles=(BattleRangeRoleSummary(1004, "安魂曲", 1, 100.0, 100.0, 100.0),),
            hits=(topple,),
            hit_replays=(BattleHitReplayResult(
                event_id=topple.event_id,
                observed_damage=100.0,
                non_critical_damage=None,
                critical_damage=None,
                selected_damage=None,
                selected_error_percent=None,
                critical_state="unreplayable",
                confidence="未解析",
                factors=(),
            ),),
            segment_total_damage=100.0,
        )

        self.assertFalse(unresolved.pending_topple_attribution)
        self.assertTrue(unresolved.unresolved_topple_attribution)
        self.assertEqual("倾陷归属证据不足", unresolved.other_entries[0].label)


if __name__ == "__main__":
    unittest.main()
