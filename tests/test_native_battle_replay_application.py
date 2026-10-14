# 对完整原生逐击应用层验证公式、中文证据、目标路由和整场审计与 Python 一致。
from __future__ import annotations

import math
import os
import unittest
from dataclasses import asdict, dataclass, is_dataclass, replace
from pathlib import Path
from types import SimpleNamespace

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.replay.service import BattleHitReplayService
from src.services.battle_report.replay.audit import BattleHitReplayAuditService
from tests.test_native_direct_replay import frozen_fixture
from tests.test_native_mixed_hit_replay import _fixture
from tests.battle_report.replay.test_audit_repairs import _hit, _replay
from tests.battle_report.buffs.test_attribute_projection import _interval
from src.domain.battle_report.build.inputs import BattleCharacterStat, BattleCharacterSourceStat


@dataclass
class RoutedAnalysis:
    hits: tuple
    baselines: tuple
    buff_intervals: tuple
    target_condition: object
    target_instance_resolutions: tuple
    target_instance_mapping_required: bool = True


def wire(value):
    if is_dataclass(value):
        return wire(asdict(value))
    if isinstance(value, SimpleNamespace):
        return wire(vars(value))
    if isinstance(value, dict):
        return {str(key): wire(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [wire(item) for item in value]
    return value


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "explicit isolated replay executable required")
class NativeBattleReplayApplicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.native = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "full-replay-test",
        )

    def equivalent(self, expected, actual, path="result"):
        if isinstance(expected, dict):
            self.assertEqual(expected.keys(), actual.keys(), path)
            for key in expected:
                self.equivalent(expected[key], actual[key], f"{path}.{key}")
        elif isinstance(expected, list):
            self.assertEqual(len(expected), len(actual), path)
            for index, (left, right) in enumerate(zip(expected, actual, strict=True)):
                self.equivalent(left, right, f"{path}[{index}]")
        elif isinstance(expected, float):
            self.assertIsNotNone(actual, path)
            self.assertTrue(math.isclose(expected, actual, rel_tol=1e-11, abs_tol=1e-9),
                            f"{path}: {expected!r} != {actual!r}")
        else:
            self.assertEqual(expected, actual, path)

    def replay_equivalent(self, expected, actual, evidence_terms, path="result"):
        # 仅本文件重放契约允许已裁决的证据措辞变化；每个其他字段独立断言，避免文本先挡住数值。
        if path in evidence_terms:
            with self.subTest(field=path):
                self.assertIsInstance(actual, str)
                for term in evidence_terms[path]:
                    self.assertIn(term, expected)
                    self.assertIn(term, actual)
        elif isinstance(expected, dict):
            with self.subTest(field=path):
                self.assertEqual(expected.keys(), actual.keys())
            for key in expected:
                if key in actual:
                    self.replay_equivalent(expected[key], actual[key], evidence_terms, f"{path}.{key}")
        elif isinstance(expected, list):
            with self.subTest(field=path):
                self.assertEqual(len(expected), len(actual))
            for index, (left, right) in enumerate(zip(expected, actual)):
                self.replay_equivalent(left, right, evidence_terms, f"{path}[{index}]")
        else:
            with self.subTest(field=path):
                self.equivalent(expected, actual, path)

    def compare(self, analysis, evidence, configs=None, *, refined=True, evidence_terms=None):
        expected = BattleHitReplayService.replay(
            analysis, evidence, topple_character_configs=configs,
            apply_observed_refinements=refined,
        )
        actual, = self.native.compute_batch("battle_replay_v1", ({
            "analysis": wire(analysis), "skill_evidence": wire(evidence),
            "topple_configs": wire(configs or {}), "apply_observed_refinements": refined,
        },))
        if evidence_terms is None:
            self.equivalent(wire(expected), actual["results"])
        else:
            self.replay_equivalent(wire(expected), actual["results"], evidence_terms)
        return actual["results"]

    def test_final_zero_stat_does_not_restore_earlier_source_term(self):
        analysis, evidence = frozen_fixture()
        baseline = analysis.baselines[0]
        analysis.baselines = (replace(baseline, source_stats=(), stats=(
            *baseline.stats,
            BattleCharacterStat("AtkUp", "攻击力提升", 0.25, True),
            BattleCharacterStat("AtkUp", "攻击力提升", 0.0, True),
        )), *analysis.baselines[1:])
        self.compare(analysis, evidence)

    def test_complete_mixed_axis_before_and_after_observed_refinement(self):
        analysis, evidence, configs = _fixture()
        for refined in (False, True):
            with self.subTest(refined=refined):
                self.compare(analysis, evidence, configs, refined=refined, evidence_terms={
                    "result[1].factors[0].evidence_basis": ("UnbalMax=50",),
                    "result[1].factors[0].terms[0].evidence_basis": ("目标",),
                    "result[1].factors[1].terms[1].evidence_basis": ("该角色等级", "穿防"),
                    "result[1].factors[1].terms[2].evidence_basis": ("固有伤害属性", "属性穿透", "目标"),
                })

    def test_dynamic_projection_source_terms_and_inferred_target_routing(self):
        analysis, evidence = frozen_fixture()
        analysis.baselines = (replace(analysis.baselines[0], stats=(
            BattleCharacterStat("AtkBase", "基础攻击", 1000.0, False),
            BattleCharacterStat("AtkUp", "攻击提升", 0.2, True),
        ), source_stats=(BattleCharacterSourceStat("character", "角色", "AtkBase", "基础攻击", 1000.0, False),)),)
        analysis.buff_intervals = tuple(replace(_interval(
            str(i), start_us=0, property_id=prop, value=value, target_scope=scope,
        ), source_character_id=1, buff_asset_path=f"/Game/Test/{i}") for i, (prop, value, scope) in enumerate((
            ("AtkUp", 0.3, "self"), ("DamageResistChaosAdd", -0.1, "target"),
            ("DamageUpFinal", 0.2, "self"), ("Unresolved", None, "self"),
        )))
        condition = replace(analysis.target_condition, source_kind="inferred_encounter_hp_injective_default")
        analysis.target_condition = None
        analysis.target_instance_mapping_required = True
        analysis.target_instance_resolutions = (SimpleNamespace(
            scope_half="upper", captured_target_id="target", target_condition=condition,
            resolved_monster_id="enemy",
        ),)
        analysis = RoutedAnalysis(**vars(analysis))
        rows = self.compare(analysis, evidence, evidence_terms={})
        for row in rows:
            self.assertIsNotNone(row["selected_damage"])
        analysis.target_instance_resolutions = ()
        terms = {f"result[{index}].missing_evidence[0]": ("目标", "防御") for index in (0, 1)}
        rows = self.compare(analysis, evidence, evidence_terms=terms)
        for row in rows:
            self.assertEqual("unreplayable", row["critical_state"])
            self.assertIsNone(row["selected_damage"])

    def test_critical_policies_state_guards_and_missing_sources(self):
        for policy in ("character", "fixed", "disabled", "unknown"):
            for attribute in ("chaos", "true"):
                with self.subTest(policy=policy, attribute=attribute):
                    analysis, evidence = frozen_fixture()
                    evidence = tuple(replace(e, critical_policy=policy, damage_attribute=attribute)
                                     for e in evidence)
                    self.compare(analysis, evidence)
        analysis, evidence = frozen_fixture()
        analysis.hits = (*analysis.hits, replace(analysis.hits[0], event_id="missing"))
        self.compare(analysis, evidence)

    def test_weave_records_exact_source_and_retains_missing_source(self):
        # 正式覆纹扩展了来源/比例因子，不再把旧离线解释布局当协议；数值仍独立逐字段比较。
        for strength, passive, damage, settled in ((0., False, 100., 20.),
                                                  (0., True, 100., 32.),
                                                  (180., True, 1000., 463.)):
            with self.subTest(strength=strength, passive=passive):
                analysis, evidence = frozen_fixture()
                first = replace(analysis.hits[0], damage=damage)
                baseline = replace(analysis.baselines[0], stats=(
                    *analysis.baselines[0].stats,
                    BattleCharacterStat("MagBase", "环合强度", strength, False),
                ), enabled_team_passive_ids=("PASSIVE-1072-GA_Radio072_Passive_1",) if passive else ())
                # 跨角色追加仍取实际原伤来源，不能借用覆纹记录角色的更高强度。
                owner = 2 if strength else first.character_id
                analysis.baselines = (baseline, replace(baseline, character_id=2, stats=(
                    BattleCharacterStat("MagBase", "环合强度", 900., False),
                )))
                weave = replace(first, event_id="weave", character_id=owner, classification="weave",
                                is_follow_up=True, damage_name="覆纹", damage=10.0)
                analysis.hits = (first, weave, replace(weave, event_id="unpaired", sequence=500))
                expected = wire(BattleHitReplayService.replay(analysis, evidence))
                result, = self.native.compute_batch("battle_replay_v1", ({
                    "analysis": wire(analysis), "skill_evidence": wire(evidence),
                    "topple_configs": {}, "apply_observed_refinements": True,
                },))
                rows = result["results"]
                self.replay_equivalent(expected[0], rows[0], {}, "direct")
                self.replay_equivalent(expected[2], rows[2], {}, "unpaired")
                before, after = dict(expected[1]), dict(rows[1])
                old_factors = {f["factor_id"]: f for f in before.pop("factors")}
                factors = {f["factor_id"]: f for f in after.pop("factors")}
                self.assertEqual(len(rows[1]["factors"]), len(factors))
                before["formula_panel_character_id"] = first.character_id
                before["formula_damage_attribute"] = first.damage_attribute
                self.replay_equivalent(before, after, {}, "weave")
                self.assertEqual(set(old_factors) | {
                    "weave_copy_ratio", "weave_strength_addition", "weave_final",
                }, set(factors))
                for key, factor in old_factors.items():
                    self.equivalent(factor["value"], factors[key]["value"], key)
                self.assertEqual(settled, rows[1]["selected_damage"])
                self.assertEqual(damage, factors["recorded_direct_damage"]["value"])
                self.assertIn(first.event_id, factors["recorded_direct_damage"]["evidence_basis"])
                self.assertEqual(1., factors["weave_final"]["value"])
                self.assertEqual(factors["weave_strength"]["value"] - 1.,
                                 factors["weave_strength_addition"]["value"])
                copy = factors["weave_copy_ratio"]
                self.assertAlmostEqual(.33 if passive else .20, copy["value"], places=7)
                terms = {term["term_id"]: term for term in copy["terms"]}
                self.assertEqual({"weave:base", "weave:extension"}, set(terms))
                self.assertEqual(.20, terms["weave:base"]["value"])
                self.assertEqual(.13 if passive else 0., terms["weave:extension"]["value"])
                self.assertEqual("DT_ReactionEffectFigure.LingZhouCopyCoef",
                                 terms["weave:base"]["source_name"])
                self.assertIn("LingZhouReactionExpendOne.ExtraDamageRatio",
                              terms["weave:extension"]["source_name"])
                self.assertIsNone(rows[2]["selected_damage"])
                self.assertEqual("unreplayable", rows[2]["critical_state"])

    def test_overkill_and_formula_context_keep_raw_identity(self):
        analysis, evidence = frozen_fixture()
        analysis.hits = tuple(replace(h, raw_damage=1000.0, damage_correction_kind="nte_core_overkill_v3",
                                      damage_correction_basis="原始证据") for h in analysis.hits)
        evidence = tuple(replace(e, formula_context_kind="test", formula_context_basis="正式来源",
                                 panel_character_id=1, action_character_id=1075,
                                 is_formal_follow_up=True) for e in evidence)
        self.compare(analysis, evidence)

    def test_local_critical_pair_full_audit(self):
        analysis, evidence = frozen_fixture()
        analysis.hits = tuple(replace(analysis.hits[0], event_id=str(i), sequence=i,
                                     damage=100.0 if i % 2 else 150.0) for i in range(8))
        evidence = tuple(replace(evidence[0], event_id=h.event_id) for h in analysis.hits)
        self.compare(analysis, evidence)

    def compare_audit(self, hits, results):
        analysis = SimpleNamespace(hits=hits, baselines=())
        expected = BattleHitReplayAuditService.postprocess(analysis, results)
        actual, = self.native.compute_batch("battle_replay_audit_v1", ({
            "analysis": wire(analysis), "results": wire(results),
        },))
        self.equivalent(wire(expected), actual["results"])

    def test_erosion_modes_and_conflicting_hp_evidence(self):
        hit = _hit("erosion", 1000, "GE_Player_Zankou_DotDamage", character_id=1036)
        for observed in (100.0, 250.0, 500.0, 1250.0, 193.0):
            replay = replace(_replay(hit.event_id, observed, 5.0, 500.0), formula_type="直伤（蚀心）")
            self.compare_audit((replace(hit, damage=observed),), (replay,))
        first = replace(hit, damage=100.0, target_hp_before=1000.0, target_hp_after=900.0)
        second = replace(first, event_id="other", sequence=1001, gameplay_effect_id="Other")
        self.compare_audit((first, second), (_replay(first.event_id, 100.0, 5.0, 500.0),
                                            _replay(second.event_id, 100.0, 1.0, 100.0)))

    def test_nightmare_missing_application_and_dark_star_remainder(self):
        first = _hit("first", 0, "GE_Player_Lacrimosa_Blood_Damage", damage=100.0)
        application = _hit("application", 900_000, "GE_Player_Lacrimosa_Melee")
        later = _hit("later", 1_000_000, "GE_Player_Lacrimosa_Blood_Damage", damage=200.0)
        self.compare_audit((first, application, later),
                           (_replay("first", 100.0, 1.0, 100.0),
                            _replay("application", 100.0, 1.0, 100.0),
                            _replay("later", 200.0, 3.0, 300.0)))
        star = replace(_hit("star", 1_000_000, "Buff_Reaction_4_new", damage=1249.0),
                       target_hp_before=3_871_103.75, target_hp_after=3_812_254.75)
        replay = replace(_replay("star", 1249.0, 1.0, 57600.0), formula_type="黯星",
                         selected_damage=57600.0, critical_damage=None, expected_damage=57600.0)
        self.compare_audit((star,), (replay,))
