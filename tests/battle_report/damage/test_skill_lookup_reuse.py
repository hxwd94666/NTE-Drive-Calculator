# 验证逐击技能证据的静态查询复用仅限当前冻结请求且保留身份与等级差异。
from __future__ import annotations

from collections import Counter
from dataclasses import replace
from types import SimpleNamespace
import unittest

from src.services.battle_report.damage.skill_evidence import BattleSkillDamageEvidenceService
from src.services.damage_calculation_service import reaction_tier_for_character_level
from tests.battle_report.damage.test_skill_evidence import _hit


class _CountingStaticDao:
    def __init__(self, *, scale=1.0, owners=(), candidates=("GA_A", "GA_B"), reaction=True, tags=None):
        self.calls = Counter()
        self.scale = scale
        self.owners = owners
        self.candidates = candidates
        self.reaction = reaction
        self.tags = tags or {}

    def gameplay_effect_has_tag(self, asset_path, tag):
        self.calls[("tag", asset_path, tag)] += 1
        return self.tags.get((asset_path, tag), False)

    def get_skill_damage(self, damage_id):
        self.calls[("damage", damage_id)] += 1
        if damage_id == "GE_Missing":
            return None
        return {
            "ability_id": "GA_A", "damage_type": "incantation",
            "atk_rate_base": (self.scale, self.scale * 2, self.scale * 3),
        }

    def list_skill_damage_owner_character_ids(self, damage_id):
        self.calls[("owners", damage_id)] += 1
        return self.owners

    def list_skill_level_ability_candidates(self, character_id, damage_id):
        self.calls[("candidates", character_id, damage_id)] += 1
        return self.candidates

    def get_reaction_damage_curve(self, damage_id):
        self.calls[("reaction", damage_id)] += 1
        return self.get_combat_level_curve("reaction:" + damage_id)

    def get_combat_level_curve(self, curve_id):
        self.calls[("curve", curve_id)] += 1
        if not self.reaction:
            return None
        return {"points": tuple({"value": float(index + 1)} for index in range(16))}


def _build():
    return {"characters": [
        {"character_id": character_id, "character_level": character_level,
         "skills": [{"skill_id": "GA_A", "skill_level": 1},
                    {"skill_id": "GA_B", "skill_level": skill_level}], "profile": {}}
        for character_id, character_level, skill_level in ((1003, 1, 2), (1004, 80, 3))
    ]}


def _analysis(hits):
    return SimpleNamespace(hits=tuple(hits), time_stop_intervals=())


class BattleSkillDamageLookupReuseTests(unittest.TestCase):
    def test_same_sql_inputs_reuse_but_observed_ability_and_level_stay_per_hit(self):
        hits = tuple(
            _hit(f"{index}:primary", damage_id=damage_id,
                 character_id=character_id, ability_id=ability_id)
            for index, (damage_id, character_id, ability_id) in enumerate((
                ("GE_Shared", 1003, "GA_A"), ("GE_Shared", 1003, "GA_B"),
                ("GE_Shared", 1004, "GA_B"), ("GE_Shared", 1004, "GA_B"),
                ("GE_Other", 1004, "GA_B"),
            ), 1)
        )
        dao = _CountingStaticDao()
        result = BattleSkillDamageEvidenceService.load(dao, _analysis(hits), _build())
        self.assertEqual(("GA_A", "GA_B", "GA_B", "GA_B", "GA_B"), tuple(row.ability_id for row in result))
        self.assertEqual((1, 2, 3, 3, 3), tuple(row.effective_skill_level for row in result))
        self.assertEqual((1., 2., 3., 3., 3.), tuple(row.scaling_multiplier for row in result))
        self.assertEqual(
            tuple(float(reaction_tier_for_character_level(level) + 1) for level in (1, 1, 80, 80, 80)),
            tuple(row.level_multiplier for row in result),
        )
        self.assertEqual(1, dao.calls[("candidates", 1003, "GE_Shared")])
        self.assertEqual(1, dao.calls[("candidates", 1004, "GE_Shared")])
        self.assertEqual(1, dao.calls[("candidates", 1004, "GE_Other")])
        for damage_id in ("GE_Shared", "GE_Other"):
            for query in ("damage", "owners", "reaction"):
                self.assertEqual(1, dao.calls[(query, damage_id)])
            self.assertEqual(1, dao.calls[("curve", "reaction:" + damage_id)])
        self.assertNotEqual(result[0].event_id, result[1].event_id)

    def test_empty_candidates_missing_damage_and_missing_curves_remain_unknown(self):
        dao = _CountingStaticDao(candidates=(), reaction=False)
        hits = tuple(_hit(f"{index}:primary", damage_id=damage_id) for index, damage_id in enumerate(
            ("GE_Shared", "GE_Shared", "GE_Missing", "GE_Missing"), 1
        ))
        result = BattleSkillDamageEvidenceService.load(dao, _analysis(hits), _build())
        self.assertEqual(2, len(result))
        self.assertTrue(all(row.formula_kind == "skill" and row.level_multiplier is None for row in result))
        self.assertEqual(1, dao.calls[("candidates", 1003, "GE_Shared")])
        self.assertEqual(1, dao.calls[("reaction", "GE_Shared")])
        self.assertEqual(1, dao.calls[("damage", "GE_Missing")])
        self.assertEqual(0, dao.calls[("owners", "GE_Missing")])

    def test_formal_owner_still_overrides_each_distinct_observed_owner(self):
        dao = _CountingStaticDao(owners=(1004,))
        first = _hit("1:primary", damage_id="GE_Shared", character_id=1003, ability_id="GA_B")
        second = replace(first, event_id="2:primary", character_id=1004)
        result = BattleSkillDamageEvidenceService.load(dao, _analysis((first, second)), _build())
        self.assertEqual((1004, 1004), tuple(row.definition_owner_character_id for row in result))
        self.assertEqual((3, 3), tuple(row.effective_skill_level for row in result))
        self.assertIn("Core 会话归属角色 1003", result[0].evidence_basis)
        self.assertNotIn("Core 会话归属角色", result[1].evidence_basis)
        self.assertEqual(1, dao.calls[("owners", "GE_Shared")])
        self.assertEqual(1, dao.calls[("candidates", 1004, "GE_Shared")])

    def test_each_load_resolves_its_own_dao_and_frozen_cultivation(self):
        hit = _hit("1:primary", damage_id="GE_Shared", ability_id="GA_B")
        analysis = _analysis((hit, replace(hit, event_id="2:primary")))
        first_dao, second_dao = _CountingStaticDao(), _CountingStaticDao(scale=10.)
        first = BattleSkillDamageEvidenceService.load(first_dao, analysis, _build())
        changed_build = _build()
        changed_build["characters"][0]["skills"][1]["skill_level"] = 3
        second = BattleSkillDamageEvidenceService.load(second_dao, analysis, changed_build)
        self.assertEqual(2., first[0].scaling_multiplier)
        self.assertEqual(30., second[0].scaling_multiplier)
        first_dao.scale = 4.
        refreshed = BattleSkillDamageEvidenceService.load(first_dao, analysis, _build())
        self.assertEqual(8., refreshed[0].scaling_multiplier)
        self.assertEqual(2, first_dao.calls[("candidates", 1003, "GE_Shared")])
        self.assertEqual(1, second_dao.calls[("candidates", 1003, "GE_Shared")])

    def test_static_lookup_error_propagates_without_reusing_other_query(self):
        class FailingDao(_CountingStaticDao):
            def list_skill_level_ability_candidates(self, character_id, damage_id):
                if damage_id == "GE_Failure":
                    raise RuntimeError("static query unavailable")
                return super().list_skill_level_ability_candidates(character_id, damage_id)

        hits = tuple(_hit(f"{index}:primary", damage_id=damage_id) for index, damage_id in enumerate(
            ("GE_Shared", "GE_Failure"), 1
        ))
        with self.assertRaisesRegex(RuntimeError, "static query unavailable"):
            BattleSkillDamageEvidenceService.load(FailingDao(), _analysis(hits), _build())

    def test_formal_tag_cache_keeps_full_asset_paths_negative_values_and_load_boundaries(self):
        tag = "Ability.Player.Nanally.XieTongDamage"
        paths = ("/Game/A/GE_Shared", "/Game/B/GE_Shared", "/game/a/GE_Shared")
        hits = tuple(_hit(f"{index}:primary", damage_id=path)
                     for index, path in enumerate((*paths, *paths), 1))
        dao = _CountingStaticDao(tags={(paths[0], tag): True})
        first = BattleSkillDamageEvidenceService.load(dao, _analysis(hits), _build())
        self.assertEqual((True, False, False) * 2, tuple(row.is_formal_follow_up for row in first))
        for path in paths:
            self.assertEqual(dao.calls[("tag", path, tag)], 1)
        dao.tags = {(paths[1], tag): True}
        refreshed = BattleSkillDamageEvidenceService.load(dao, _analysis(hits), _build())
        self.assertEqual((False, True, False) * 2, tuple(row.is_formal_follow_up for row in refreshed))
        for path in paths:
            self.assertEqual(dao.calls[("tag", path, tag)], 2)
        another = _CountingStaticDao(tags={(paths[2], tag): True})
        separate = BattleSkillDamageEvidenceService.load(another, _analysis(hits), _build())
        self.assertEqual((False, False, True) * 2, tuple(row.is_formal_follow_up for row in separate))

    def test_formal_tag_query_failure_remains_failure_and_next_load_can_retry(self):
        class FailingTagDao(_CountingStaticDao):
            def gameplay_effect_has_tag(self, asset_path, tag):
                if self.scale == 1.0:
                    raise RuntimeError("tag query failed")
                return super().gameplay_effect_has_tag(asset_path, tag)

        dao = FailingTagDao()
        analysis = _analysis((_hit("1:primary", damage_id="GE_Shared"),))
        with self.assertRaisesRegex(RuntimeError, "tag query failed"):
            BattleSkillDamageEvidenceService.load(dao, analysis, _build())
        dao.scale = 2.0
        result = BattleSkillDamageEvidenceService.load(dao, analysis, _build())
        self.assertFalse(result[0].is_formal_follow_up)
