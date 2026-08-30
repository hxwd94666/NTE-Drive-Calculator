# 覆盖 Core v4 零值下法帝娅黯星的定向 HPMax 样本回退。
from __future__ import annotations

import unittest

from src.services.battle_target_vital_analysis_service import (
    BattleTargetVitalAnalysisService,
)


def _row(
    sequence: int,
    time_us: int,
    *,
    character_id: int,
    effect: str,
    max_hp: float,
    hp_before: float,
    hp_after: float,
    max_hp_reduction: float = 0.0,
    target_id: str = "boss-1",
    abyss_half: str = "upper",
) -> dict:
    return {
        "sequence_order": sequence,
        "relative_time_us": time_us,
        "character_id": character_id,
        "character_name": "法帝娅" if character_id == 1039 else "残虹",
        "direction": "outgoing",
        "damage": max(0.0, hp_before - hp_after),
        "gameplay_effect_name": effect,
        "target_id": target_id,
        "target_name": "测试目标",
        "abyss_half": abyss_half,
        "target_max_hp": max_hp,
        "target_hp_before": hp_before,
        "target_hp_after": hp_after,
        "max_hp_reduction": max_hp_reduction,
    }


def _fadia_build(stage: int = 2) -> dict:
    return {"characters": [{"character_id": 1039, "breakthrough_stage": stage}]}


class BattleTargetVitalV4FadiaFallbackTests(unittest.TestCase):
    def test_zero_core_value_allows_fadia_observed_delta_fallback(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1039,
                effect="Buff_Reaction_4_new",
                max_hp=2_000,
                hp_before=1_500,
                hp_after=1_500,
            ),
            _row(
                2,
                1_400_000,
                character_id=1036,
                effect="GE_Player_Zankou_Melee1_Damage",
                max_hp=1_800,
                hp_before=1_500,
                hp_after=1_499,
            ),
        )

        events = BattleTargetVitalAnalysisService.derive(
            rows=rows,
            build=_fadia_build(),
            structured_max_hp_reduction=True,
        )

        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual("fadia_dark_star_max_hp_transfer", event.mechanic_kind)
        self.assertEqual(2_000.0, event.old_max_hp)
        self.assertEqual(1_800.0, event.new_max_hp)
        self.assertEqual(200.0, event.max_hp_reduction)
        self.assertEqual(1_500.0, event.hp_before_settlement)
        self.assertEqual(150.0, event.effective_hp_loss)
        self.assertIn("法帝娅定向样本回退", event.inference_basis)

    def test_zero_core_value_keeps_unrelated_sample_drop_suppressed(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1036,
                effect="hit-a",
                max_hp=1_000,
                hp_before=900,
                hp_after=850,
            ),
            _row(
                2,
                1_100_000,
                character_id=1036,
                effect="hit-b",
                max_hp=900,
                hp_before=800,
                hp_after=799,
            ),
        )

        self.assertEqual(
            (),
            BattleTargetVitalAnalysisService.derive(
                rows=rows,
                build=_fadia_build(),
                structured_max_hp_reduction=True,
            ),
        )

    def test_zero_core_value_requires_unlocked_fadia_passive(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1039,
                effect="Buff_Reaction_4_new",
                max_hp=2_000,
                hp_before=1_500,
                hp_after=1_500,
            ),
            _row(
                2,
                1_400_000,
                character_id=1036,
                effect="hit-b",
                max_hp=1_800,
                hp_before=1_500,
                hp_after=1_499,
            ),
        )

        self.assertEqual(
            (),
            BattleTargetVitalAnalysisService.derive(
                rows=rows,
                build=_fadia_build(stage=1),
                structured_max_hp_reduction=True,
            ),
        )

    def test_positive_core_value_prevents_second_fadia_sample_event(self) -> None:
        rows = (
            _row(
                1,
                1_000_000,
                character_id=1039,
                effect="Buff_Reaction_4_new",
                max_hp=1_800,
                hp_before=1_500,
                hp_after=1_500,
                max_hp_reduction=200,
            ),
            _row(
                2,
                1_400_000,
                character_id=1036,
                effect="hit-b",
                max_hp=1_800,
                hp_before=1_500,
                hp_after=1_499,
            ),
        )

        events = BattleTargetVitalAnalysisService.derive(
            rows=rows,
            build=_fadia_build(),
            structured_max_hp_reduction=True,
        )

        self.assertEqual(1, len(events))
        self.assertEqual(200.0, events[0].max_hp_reduction)
        self.assertIn("nte-core v4", events[0].inference_basis)
        self.assertNotIn("法帝娅定向样本回退", events[0].inference_basis)

    def test_prior_suppressed_drop_is_not_charged_to_later_fadia(self) -> None:
        rows = (
            _row(
                1, 1_000_000, character_id=1036, effect="hit-a",
                max_hp=2_000, hp_before=1_600, hp_after=1_550,
            ),
            _row(
                2, 1_100_000, character_id=1036, effect="hit-b",
                max_hp=1_900, hp_before=1_500, hp_after=1_499,
            ),
            _row(
                3, 2_000_000, character_id=1039,
                effect="Buff_Reaction_4_new",
                max_hp=1_900, hp_before=1_500, hp_after=1_500,
            ),
            _row(
                4, 2_400_000, character_id=1036, effect="hit-c",
                max_hp=1_700, hp_before=1_500, hp_after=1_499,
            ),
        )

        events = BattleTargetVitalAnalysisService.derive(
            rows=rows,
            build=_fadia_build(),
            structured_max_hp_reduction=True,
        )

        self.assertEqual(1, len(events))
        self.assertEqual(1_900.0, events[0].old_max_hp)
        self.assertEqual(1_700.0, events[0].new_max_hp)
        self.assertEqual(200.0, events[0].max_hp_reduction)


if __name__ == "__main__":
    unittest.main()
