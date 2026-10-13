# 比较完整 Buff 移除原生结果，覆盖整轴特殊伤害与冻结暴击分支。
from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.buffs.counterfactual import BattleBuffCounterfactualService
from src.services.battle_report.replay.service import BattleHitReplayService
from src.services.battle_report.replay.preparation import BattleHitProjectionPreparationService
from tests import test_native_battle_replay_application as replay_contract
from tests.battle_report.buffs.test_counterfactual import _snapshot, _interval, _modifier
from tests.test_native_direct_replay import frozen_fixture
from tests.test_native_mixed_hit_replay import _fixture
from tests.battle_report.marginal.test_continuous_direct import _nightmare, _vital, _baseline


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "explicit isolated executable required")
class NativeBattleBuffRemoveApplicationTests(unittest.TestCase):
    equivalent = replay_contract.NativeBattleReplayApplicationTests.equivalent

    @classmethod
    def setUpClass(cls):
        cls.native = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "buff-remove-application-test",
        )

    def compare(self, analysis, evidence, configs=None, *, prepared=False):
        projections = (BattleHitProjectionPreparationService.prepare(analysis, evidence)
                       if prepared else None)
        expected = BattleBuffCounterfactualService.calculate(
            analysis, evidence, topple_character_configs=configs,
            original_projection_by_event=None if projections is None else projections.beneficiary_by_event,
        )
        actual, = self.native.compute_batch("battle_buff_remove_v1", ({
            "analysis": replay_contract.wire(analysis), "skill_evidence": replay_contract.wire(evidence),
            "topple_configs": replay_contract.wire(configs or {}),
            "prepared_beneficiaries": prepared,
        },))
        self.equivalent(replay_contract.wire(expected), actual["results"])
        return expected

    def test_page_prepared_formula_attribute_controls_coverage(self):
        raw, evidence = frozen_fixture()
        analysis = _snapshot(hits=raw.hits, intervals=(
            _interval("element", modifiers=(_modifier(0.2, property_id="DamageUpNatureBase"),)),
        ), replays=(), baselines=raw.baselines)
        analysis = replace(analysis, target_condition=raw.target_condition)
        evidence = tuple(replace(row, damage_attribute="nature") for row in evidence)
        unprepared = self.compare(analysis, evidence)
        prepared = self.compare(analysis, evidence, prepared=True)
        self.assertEqual(0, unprepared[0].affected_hits)
        self.assertEqual(2, prepared[0].affected_hits)

    def test_direct_selected_branches_and_modifier_combinations(self):
        raw, evidence = frozen_fixture()
        for scope in ("self", "team", "team_others", "unknown", "target"):
            for modifiers in ((), (_modifier(0.15),), (_modifier(None),),
                              (_modifier(0.2, property_id="AtkUp"),),
                              (_modifier(-0.2, property_id="DamageResistChaos"),)):
                with self.subTest(scope=scope, modifiers=modifiers):
                    analysis = _snapshot(hits=raw.hits, intervals=(
                        _interval("buff", target_scope=scope, modifiers=modifiers),
                    ), replays=(), baselines=raw.baselines)
                    analysis = replace(analysis, target_condition=raw.target_condition)
                    self.compare(analysis, evidence)

    def test_stateful_mixed_axis_and_overlapping_group_intervals(self):
        raw, evidence, configs = _fixture()
        for cached in (False, True):
            analysis = _snapshot(hits=raw.hits, intervals=(
                _interval("buff", target_scope="team", modifiers=(_modifier(0.15),)),
                _interval("refresh", start_us=1, target_scope="team", modifiers=(_modifier(0.15),)),
                replace(_interval("strength", target_scope="team", modifiers=(
                    _modifier(6.0, property_id="UnbalIntensityBase"),
                )), source_effect_definition_id="test:strength"),
            ), replays=(), baselines=raw.baselines)
            analysis = replace(analysis, target_condition=raw.target_condition)
            if cached:
                analysis = replace(analysis, hit_replays=BattleHitReplayService.replay(
                    analysis, evidence, topple_character_configs=configs,
                ))
            with self.subTest(cached=cached):
                self.compare(analysis, evidence, configs)

    def test_public_unknown_and_partial_fixtures_match_native(self):
        original = BattleBuffCounterfactualService.calculate

        def compare(analysis, evidence, **kwargs):
            expected = original(analysis, evidence, **kwargs)
            actual, = self.native.compute_batch("battle_buff_remove_v1", ({
                "analysis": replay_contract.wire(analysis),
                "skill_evidence": replay_contract.wire(evidence), "topple_configs": {},
            },))
            self.equivalent(replay_contract.wire(expected), actual["results"])
            return expected

        names = (
            "test_unstructured_buff_remains_visible_without_a_numeric_gain",
            "test_unavailable_linked_settlement_keeps_known_beneficiary",
            "test_attack_buff_is_partial_when_buff_state_is_inferred",
            "test_target_resistance_buff_is_unavailable_without_target_profile",
            "test_mixed_attack_and_penetration_buff_is_partial_without_target",
            "test_no_covered_hit_is_not_applicable_without_zero_gain_display_semantics",
        )
        suite = unittest.defaultTestLoader.loadTestsFromNames([
            f"tests.battle_report.buffs.test_counterfactual.BattleBuffCounterfactualServiceTests.{name}"
            for name in names
        ])
        result = unittest.TestResult()
        with patch.object(BattleBuffCounterfactualService, "calculate", side_effect=compare):
            suite.run(result)
        self.assertFalse(result.errors + result.failures, "\n".join(
            f"{test}:\n{failure}" for test, failure in result.errors + result.failures
        ))

    def test_nightmare_vital_link_and_frozen_selected_policies(self):
        raw, evidence = frozen_fixture()
        hit = _nightmare()
        interval = replace(_interval("nightmare-buff", modifiers=(
            _modifier(0.2, property_id="AtkUp"),
        )), source_character_id=hit.character_id)
        nightmare_evidence = replace(evidence[0], event_id=hit.event_id,
                                    source_character_id=hit.character_id,
                                    damage_id=hit.gameplay_effect_id, critical_policy="fixed")
        analysis = _snapshot(hits=(hit,), intervals=(interval,), replays=(),
                             baselines=(_baseline(),), max_hp_events=(
                                 _vital(evidence_event_ids=(hit.event_id,)),
                             ))
        self.compare(replace(analysis, target_condition=raw.target_condition), (nightmare_evidence,))
        analysis = _snapshot(hits=raw.hits, intervals=(
            _interval("direct", modifiers=(_modifier(0.15),)),
        ), replays=(), baselines=raw.baselines)
        analysis = replace(analysis, target_condition=raw.target_condition)
        replays = BattleHitReplayService.replay(analysis, evidence)
        for policy in ("character", "fixed", "disabled", "unknown"):
            with self.subTest(policy=policy):
                self.compare(replace(analysis, hit_replays=tuple(
                    replace(row, critical_state="ambiguous", critical_policy=policy)
                    for row in replays
                )), evidence)


if __name__ == "__main__":
    unittest.main()
