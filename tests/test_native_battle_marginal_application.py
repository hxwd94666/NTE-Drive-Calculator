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
        result = unittest.TestResult()
        with patch.object(BattleMarginalCalculationService, "calculate", side_effect=compare):
            suites.run(result)
        self.assertFalse(result.errors + result.failures, "\n".join(
            f"{test}:\n{failure}" for test, failure in result.errors + result.failures
        ))
        self.assertGreater(len(checked), 25)

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
