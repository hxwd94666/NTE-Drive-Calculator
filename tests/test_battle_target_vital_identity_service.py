# 验证目标身份不完整时不制造最大生命结算归因。
from __future__ import annotations

import unittest

from src.services.battle_target_vital_analysis_service import (
    BattleTargetVitalAnalysisService,
)
from tests.test_battle_target_vital_analysis_service import _row


class BattleTargetVitalIdentityServiceTests(unittest.TestCase):
    def test_mixed_identity_excludes_unknown_rows_from_vital_derivation(self) -> None:
        known = _row(
            1,
            1_000_000,
            character_id=1039,
            character_name="法帝娅",
            effect="Buff_Reaction_4_new",
            max_hp=1_000,
            hp_before=900,
            target_id="boss-1",
        )
        unknown = _row(
            2,
            1_100_000,
            character_id=1039,
            character_name="法帝娅",
            effect="Buff_Reaction_4_new",
            max_hp=800,
            hp_before=700,
            target_id="",
        )

        events = BattleTargetVitalAnalysisService.derive(
            rows=(known, unknown),
            build={"characters": [
                {"character_id": 1039, "breakthrough_stage": 2}
            ]},
        )

        self.assertEqual((), events)


if __name__ == "__main__":
    unittest.main()
