# 将已有边际公共行为样本逐个交给完整 Rust 面板，比较所有结果字段。
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.marginal.calculation import BattleMarginalCalculationService
from tests import test_native_battle_replay_application as replay_contract
from tests.test_native_battle_replay_application import wire
from tests.battle_report.marginal.test_calculation import _baseline, _hit, _critical_replay
from tests.battle_report.marginal import test_regressions as marginal_regressions


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "explicit isolated executable required")
class NativeBattleMarginalApplicationTests(unittest.TestCase):
    # Reuse the original fixtures and assertions, while checking the complete native
    # output at the public calculation boundary. No native result feeds the oracle.
    def test_existing_public_marginal_fixtures_match_complete_native_panel(self):
        native = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "marginal-application-test",
        )
        original = BattleMarginalCalculationService.calculate
        checked = []

        def compare(*args, **kwargs):
            expected = original(*args, **kwargs)
            inputs = {key: wire(kwargs.get(key)) for key in (
                "analysis", "character_id", "edited_values", "units",
            )}
            actual, = native.compute_batch("battle_marginal_v1", (inputs,))
            self.equivalent(wire(expected), actual["results"])
            checked.append(len(expected))
            return expected

        suites = unittest.defaultTestLoader.loadTestsFromNames([
            "tests.battle_report.marginal.test_calculation",
            "tests.battle_report.marginal.test_continuous_direct",
            "tests.battle_report.marginal.test_regressions",
            "tests.battle_report.marginal.test_topple_units",
            "tests.battle_report.marginal.test_character_panel",
            "tests.test_battle_linko_marginal_regressions",
            "tests.battle_report.damage.test_stain_boundary",
        ])
        # 这一旧夹具的Python对照冻结了历史HP比例；同一输入在下方转为当前顺序HP原生contract。
        linked_case = "test_five_awaken_can_calculate_multiple_default_units"
        def retained_cases(suite):
            for case in suite:
                if isinstance(case, unittest.TestSuite):
                    yield from retained_cases(case)
                elif case.id() != (
                    "tests.battle_report.marginal.test_regressions.BattleMarginalRegressionTests." + linked_case
                ):
                    yield case

        suites = unittest.TestSuite(retained_cases(suites))
        result = unittest.TestResult()
        with patch.object(BattleMarginalCalculationService, "calculate", side_effect=compare):
            suites.run(result)
        self.assertFalse(result.errors + result.failures, "\n".join(
            f"{test}:\n{failure}" for test, failure in result.errors + result.failures
        ))
        self.assertGreater(len(checked), 25)
        self.assert_linked_default_units_contract(native, original)

    def assert_linked_default_units_contract(self, native, legacy):
        # 复用原失败输入，不用原生输出构造期望；只替换旧oracle的同比结算及其派生收益。
        fixture = marginal_regressions
        hit = fixture._hit("nightmare:1", classification="dot",
                           effect="GE_Player_Lacrimosa_Blood_Damage", attribute="chaos")
        replay = fixture._replay(hit, policy="fixed")
        analysis = fixture._analysis(hit, replay, vital=fixture._vital(hit))
        units = BattleMarginalCalculationService.default_units(
            analysis.baselines[0], hits=analysis.hits, replays={replay.event_id: replay})
        self.assertEqual({"AtkUp", "AtkAdd", "DamageUpGeneralBase", "DamageUpChaosBase",
                          "CritBase", "CritDamageBase", "DamagePenetrateChaos"}, set(units))
        inputs = {"analysis": analysis, "character_id": fixture.CHARACTER_ID,
                  "edited_values": {}, "units": units}
        expected = wire(legacy(**inputs))
        for row in expected:
            prop, unit = row["property_id"], row["unit"]
            if prop in {"CritBase", "DamagePenetrateChaos"}:
                # 固定50%不消费面板暴击率；缺目标抗性仍不可用。二者继续完整差分，未知不填零。
                continue
            if prop == "AtkAdd":
                ratio = (1000 + unit) / 1000
            elif prop == "CritDamageBase":
                ratio = (1 + 0.5 * (1 + unit)) / (1 + 0.5)
            else:
                ratio = 1 + unit
            # M=10000/H=8000/R=1000；先由候选噩梦扣当前HP，再执行最大生命同比扣减。
            candidate_hit = 1000 * ratio
            candidate_hp = 8000 - (candidate_hit - 1000)
            candidate = candidate_hit + candidate_hp * (1000 * ratio) / 10000
            gain = candidate - 1800
            row["known_projection_damage"] = candidate
            for key in ("quantified_role_gain_percent", "quantified_team_gain_percent",
                        "full_role_gain_percent", "full_team_gain_percent"):
                row[key] = gain / 1800 * 100
            row["quantification"]["quantified_increment"] = gain
        actual, = native.compute_batch("battle_marginal_v1", (wire(inputs),))
        self.equivalent(expected, actual["results"])

    def test_default_unit_catalog_matches_formula_roles_and_replay(self):
        native = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "marginal-default-test",
        )
        baseline = _baseline()
        hit = _hit()
        replay = _critical_replay(hit, "character", 0.5)
        for hits, replays in (((), ()), ((hit,), (replay,))):
            expected = BattleMarginalCalculationService.default_units(
                baseline, hits=hits, replays={r.event_id: r for r in replays},
            )
            actual, = native.compute_batch("battle_marginal_defaults_v1", ({
                "baseline": wire(baseline), "hits": wire(hits), "replays": wire(replays),
            },))
            self.assertEqual(expected, actual)

    equivalent = replay_contract.NativeBattleReplayApplicationTests.equivalent


if __name__ == "__main__":
    unittest.main()
