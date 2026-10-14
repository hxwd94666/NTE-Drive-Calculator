# 验证伤害构成的正式来源、未知归属与角色名称身份。
NTE_TEST_TIER = "core"
import unittest
import pytest
from dataclasses import replace

from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.analysis.composition import BattleRangeRoleSummary
from src.services.battle_report.damage.composition import BattleDamageCompositionService, classify_battle_hit_channel
from tests.battle_report.buffs.test_hit_explanation import _hit


class CompositionIdentityTests(unittest.TestCase):
    def test_names_survive_numeric_and_empty_baseline_placeholders(self):
        hit = replace(_hit(), character_id=1004, character_name="安魂曲")
        for baseline_name in ("1004", "", " "):
            for roles in ((), (BattleRangeRoleSummary(
                character_id=1004, character_name="安魂曲", hits=1,
                damage=hit.damage, dps=0, share_percent=100,
            ),)):
                with self.subTest(baseline=baseline_name, has_roles=bool(roles)):
                    result = BattleDamageCompositionService.calculate_from_hits(
                        roles=roles, hits=(hit,), segment_total_damage=hit.damage,
                        role_identities=((1004, baseline_name),),
                    )
                    self.assertEqual("安魂曲", result.roles[0].character_name)
                    self.assertEqual(hit.damage, result.roles[0].total_damage)

    def test_unknown_name_keeps_identity_and_damage(self):
        hit = replace(_hit(), character_id=9999, character_name="")
        result = BattleDamageCompositionService.calculate_from_hits(
            roles=(), hits=(hit,), segment_total_damage=hit.damage,
            role_identities=((9999, "9999"),),
        )
        self.assertEqual("9999", result.roles[0].character_name)
        self.assertEqual(hit.damage, result.roles[0].total_damage)


def nova_hit(effect: str, damage: float = 100.0) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id="nova:primary", sequence=1, relative_time_us=0,
        character_id=1042, character_name="黑羽", skill_name="环合",
        damage_name="环合", damage_component="", attack_type="环合",
        damage_attribute="psychically", target_id="target", target_name="目标",
        damage=damage, direction="outgoing", is_follow_up=False,
        classification="reaction", gameplay_effect_id=effect,
    )


@pytest.mark.parametrize("effect", [
    "GE_Reaction_4_new_1042_Damage",
    "GE_Reaction_4_new_1042_Damage_C",
    "/Game/Effects/GE_Reaction_4_new_1042_Damage_C",
    "\\Game\\Effects\\ge_reaction_4_new_1042_damage_c",
])
@pytest.mark.parametrize("grouping", ["coarse", "fine"])
def test_extra_nova_stays_separate_from_base_nova_and_unknown(effect, grouping):
    extra = nova_hit(effect, 370_021.0)
    base = replace(nova_hit("Buff_Reaction_4_new", 226_800.0),
                   event_id="base", damage_name="黯星", attack_type="黯星")
    unknown = replace(nova_hit("GE_Reaction_Unresolved_Damage", 100.0),
                      event_id="unknown")
    result = BattleDamageCompositionService.calculate_from_hits(
        roles=(), hits=(extra, base, unknown),
        segment_total_damage=596_921.0, grouping=grouping,
    )
    role, = result.roles
    entries = {entry.label: entry.damage for entry in role.entries}
    assert entries == {
        "鸫歌·追加黯星": 370_021.0,
        "环合·黯星": 226_800.0,
        "环合（未细分）": 100.0,
    }
    assert role.total_damage == 596_921.0
    assert extra.damage_name == "环合"
    assert extra.damage == 370_021.0


@pytest.mark.parametrize("effect", [
    "GE_Reaction_4_new_9999_Damage", "Buff_Reaction_4_new",
    "GE_Reaction_4_new_1042_Damage_Extra", "",
])
def test_other_or_missing_sources_are_not_promoted(effect):
    assert classify_battle_hit_channel(nova_hit(effect))[0] == "reaction_unknown"


def test_incoming_extra_nova_is_not_outgoing_damage():
    hit = replace(nova_hit("GE_Reaction_4_new_1042_Damage"), direction="incoming")
    assert classify_battle_hit_channel(hit) == ("incoming", "承伤")
