# 验证批量倾陷边际只替换指定贡献，保持未知、零基准与单位顺序。
from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.integrations.nte_analysis_core import NteAnalysisCoreClient
from src.services.battle_report.marginal.topple import topple_ratio, topple_ratio_batch


def _factor(character, value, **terms):
    return SimpleNamespace(
        factor_id=f"topple_character:{character}", value=value,
        terms=tuple(SimpleNamespace(property_id=key, value=value) for key, value in terms.items()),
    )


def _replays():
    selected = _factor(
        1054, 100.0, UnbalIntensityBase=120.0, UnbalIntensityUp=0.2,
        UnbalIntensityAdd=10.0, UnbalDamageUp=0.3, ToppleDamageUp=0.1,
    )
    teammate = _factor(1004, 200.0, UnbalIntensityBase=80.0)
    return (
        None,
        SimpleNamespace(critical_state="unreplayable", factors=(selected, teammate)),
        SimpleNamespace(critical_state="not_applicable", factors=()),
        SimpleNamespace(critical_state="not_applicable", factors=(teammate,)),
        SimpleNamespace(critical_state="not_applicable", factors=(selected, teammate)),
        SimpleNamespace(critical_state="not_applicable", factors=(teammate, selected)),
        SimpleNamespace(critical_state="not_applicable", factors=(
            _factor(1054, -20.0, UnbalIntensityBase=100.0), teammate,
        )),
        SimpleNamespace(critical_state="not_applicable", factors=(
            _factor(1054, 100.0, UnbalIntensityBase=0.0, UnbalDamageUp=-1.0),
        )),
        SimpleNamespace(critical_state="not_applicable", factors=(
            selected, _factor(1054, 40.0, UnbalIntensityBase=300.0), teammate,
        )),
    )


class NativeToppleMarginalAdapterTests(unittest.TestCase):
    def test_without_native_keeps_unit_major_order(self):
        replays, units = _replays(), (0.0, 1.0, 300.0, -500.0)
        expected = tuple(
            tuple(topple_ratio(row, character_id=1054, unit=unit) for row in replays)
            for unit in units
        )
        self.assertEqual(
            expected, topple_ratio_batch(replays, character_id=1054, units=units),
        )

    def test_native_receives_one_batch_and_cancellation_checkpoint(self):
        checkpoint = Mock()

        class Cancelled:
            supports_battle_compute = True

            def compute_batch(inner, operation, inputs, **kwargs):
                self.assertEqual("topple_marginal_v1", operation)
                self.assertEqual(2 * len(_replays()), len(inputs))
                self.assertIs(checkpoint, kwargs["checkpoint"])
                raise RuntimeError("cancelled")

        with patch("src.services.battle_report.marginal.topple.topple_ratio") as oracle:
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                topple_ratio_batch(
                    _replays(), character_id=1054, units=(1.0, 2.0),
                    backend=Cancelled(), checkpoint=checkpoint,
                )
            oracle.assert_not_called()


@unittest.skipUnless(os.environ.get("NTE_ANALYSIS_CORE_TEST_EXE"), "explicit isolated executable not configured")
class NativeToppleMarginalExecutableTests(unittest.TestCase):
    def test_all_contribution_selection_and_unknown_results_match_python(self):
        client = NteAnalysisCoreClient.from_executable(
            Path(os.environ["NTE_ANALYSIS_CORE_TEST_EXE"]), "public-topple-marginal-test",
        )
        self.assertTrue(client.supports_battle_compute)
        replays, units = _replays(), (0.0, 1.0, 300.0, -500.0)
        for character in (1054, 1004, 9999):
            expected = topple_ratio_batch(replays, character_id=character, units=units)
            with patch("src.services.battle_report.marginal.topple.topple_ratio", side_effect=AssertionError("fallback")):
                actual = topple_ratio_batch(
                    replays, character_id=character, units=units, backend=client,
                )
            self.assertEqual(len(expected), len(actual))
            for left, right in zip(expected, actual, strict=True):
                self.assertEqual(len(left), len(right))
                for expected_ratio, actual_ratio in zip(left, right, strict=True):
                    if expected_ratio is None:
                        self.assertIsNone(actual_ratio)
                    else:
                        self.assertAlmostEqual(expected_ratio, actual_ratio, places=12)
