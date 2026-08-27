# 验证遭遇残差只消费观测伤害后处理之前的原始公式预测。
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.services.battle_hit_replay_audit_service import BattleHitReplayAuditService
from src.services.battle_hit_replay_service import BattleHitReplayService


class BattleHitReplayRawModeTests(unittest.TestCase):
    def test_raw_mode_skips_all_observed_damage_refinements(self) -> None:
        analysis = SimpleNamespace(hits=(), baselines=())

        with (
            patch.object(
                BattleHitReplayService,
                "_apply_local_crit_evidence",
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


if __name__ == "__main__":
    unittest.main()
