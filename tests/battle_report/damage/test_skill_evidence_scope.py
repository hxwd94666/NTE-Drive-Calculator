# 验证技能证据只消费同半场同方向的冻结事件身份。
NTE_TEST_TIER = "core"

from dataclasses import replace
from types import SimpleNamespace
import unittest

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_report.damage.skill_evidence import BattleSkillDamageEvidenceService


class _Static:
    def get_skill_damage(self, damage_id):
        return {'ability_id': 'GA_Test', 'damage_type': 'incantation', 'atk_rate_base': (1.0,)}

    def get_reaction_damage_curve(self, damage_id):
        return None


def _hit(event_id, *, effect='GE_Test', half='upper', direction='outgoing'):
    return BattleAnalysisHit(
        event_id=event_id, sequence=1, relative_time_us=1_000_000, character_id=1003,
        character_name='角色', skill_name='技能', damage_name='伤害', damage_component='skill',
        attack_type='E', damage_attribute='incantation', target_id='reused-target', target_name='目标',
        damage=100.0, direction=direction, is_follow_up=False, classification='direct',
        ability_id='GA_Test', gameplay_effect_id=effect, scope_half=half,
    )


def _load(hits):
    return BattleSkillDamageEvidenceService.load(
        _Static(), SimpleNamespace(hits=tuple(hits), time_stop_intervals=()),
        {'characters': [{'character_id': 1003, 'character_level': 80,
                         'skills': [{'skill_id': 'GA_Test', 'skill_level': 1}], 'profile': {}}]},
    )


class SkillEvidenceScopeTests(unittest.TestCase):
    def test_weave_requires_the_same_packet_half_and_direction(self):
        source = _hit('1:primary')
        weave = replace(source, event_id='1:follow_up', classification='weave', is_follow_up=True)
        for changed in ({'scope_half': 'lower'}, {'direction': 'incoming'}, {'target_id': 'other'}, {'sequence': 2}):
            with self.subTest(changed=changed):
                result = _load((source, replace(weave, **changed)))
                self.assertFalse(result[0].target_has_weave)
        self.assertTrue(_load((source, weave))[0].target_has_weave)

    def test_same_time_damage_hint_stays_inside_its_half(self):
        missing = _hit('1:primary', effect='')
        other = _hit('2:primary', half='lower')
        self.assertNotIn(missing.event_id, {row.event_id for row in _load((missing, other))})
        same = replace(other, scope_half='upper')
        inferred = next(row for row in _load((missing, same)) if row.event_id == missing.event_id)
        self.assertEqual('GE_Test', inferred.damage_id)
        self.assertIn('弱证据', inferred.evidence_basis)
        conflicting = replace(same, event_id='3:primary', gameplay_effect_id='GE_Conflict')
        self.assertNotIn(missing.event_id, {row.event_id for row in _load((missing, same, conflicting))})


if __name__ == '__main__':
    unittest.main()
