# 验证遭遇残差只消费观测伤害后处理之前的原始公式预测。
from __future__ import annotations
NTE_TEST_TIER = "core"

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services.battle_report.replay.audit import BattleHitReplayAuditService
from src.services.battle_report.replay.local_critical import (
    BattleHitLocalCritInferenceService,
)
from src.services.battle_report.replay.service import BattleHitReplayService


class BattleHitReplayRawModeTests(unittest.TestCase):
    def test_raw_mode_skips_all_observed_damage_refinements(self) -> None:
        analysis = SimpleNamespace(hits=(), baselines=())

        with (
            patch.object(
                BattleHitLocalCritInferenceService,
                "apply",
                side_effect=AssertionError("local crit must not run"),
            ),
            patch.object(
                BattleHitReplayAuditService,
                "postprocess",
                side_effect=AssertionError("observed audit must not run"),
            ),
        ):
            result = BattleHitReplayService.replay(
                analysis,
                (),
                apply_observed_refinements=False,
            )

        self.assertEqual((), result)


