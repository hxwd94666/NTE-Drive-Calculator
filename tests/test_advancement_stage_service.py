# 人物和弧盘的上限等级必须同时保留突破前后两个合法状态。
"""Regression tests for Qt-free advancement-stage resolution."""

from __future__ import annotations

import unittest

from src.services.advancement_stage_service import (
    character_growth_choices,
    fork_active_panel_stats,
    fork_breakthrough_choices,
    fork_permanent_stats,
    fork_panel_stats,
    legacy_fork_breakthrough_stage,
    select_character_growth,
    select_fork_breakthrough,
)


class AdvancementStageServiceTests(unittest.TestCase):
    def test_legacy_fork_level_defaults_to_pre_breakthrough_at_caps(self) -> None:
        self.assertEqual(
            [0, 0, 1, 1, 5, 6],
            [
                legacy_fork_breakthrough_stage(level)
                for level in (1, 20, 21, 30, 70, 80)
            ],
        )

    def test_character_cap_level_preserves_before_and_after(self) -> None:
        rows = [
            {"level": 19, "breakthrough_stage": 0, "state": "normal"},
            {
                "level": 20,
                "breakthrough_stage": 0,
                "state": "breakthrough_before",
            },
            {
                "level": 20,
                "breakthrough_stage": 1,
                "state": "breakthrough_after",
            },
            {"level": 21, "breakthrough_stage": 1, "state": "normal"},
        ]

        choices = character_growth_choices(rows, 20)
        self.assertEqual([0, 1], [row["breakthrough_stage"] for row in choices])
        self.assertEqual(
            "breakthrough_before",
            select_character_growth(rows, 20, preferred_stage=0)["state"],
        )
        self.assertEqual(
            "breakthrough_after",
            select_character_growth(rows, 20, preferred_stage=1)["state"],
        )
        self.assertEqual(
            1,
            select_character_growth(rows, 20)["breakthrough_stage"],
        )
        self.assertEqual(
            [1],
            [
                row["breakthrough_stage"]
                for row in character_growth_choices(rows, 21)
            ],
        )

    def test_fork_stage_uses_first_cap_that_contains_level(self) -> None:
        rows = [
            {"stage": 0, "max_fork_level": 20},
            {"stage": 1, "max_fork_level": 30},
            {"stage": 2, "max_fork_level": 40},
        ]

        self.assertEqual(
            [0],
            [row["stage"] for row in fork_breakthrough_choices(rows, 19)],
        )
        self.assertEqual(
            [0, 1],
            [row["stage"] for row in fork_breakthrough_choices(rows, 20)],
        )
        self.assertEqual(
            [1],
            [row["stage"] for row in fork_breakthrough_choices(rows, 21)],
        )
        self.assertEqual(
            0,
            select_fork_breakthrough(rows, 20)["stage"],
        )
        self.assertEqual(
            1,
            select_fork_breakthrough(rows, 20, preferred_stage=1)["stage"],
        )

    def test_fork_panel_stats_share_explicit_stage_resolution(self) -> None:
        template = {
            "upgrade_levels": [
                {
                    "level": level,
                    "modifiers": [{"property_id": "AtkBase", "value": attack}],
                }
                for level, attack in ((19, 90), (20, 96), (21, 100))
            ],
            "breakthroughs": [
                {
                    "stage": 0,
                    "max_fork_level": 20,
                    "modifiers": [{"property_id": "HPMaxUp", "value": 0.165}],
                },
                {
                    "stage": 1,
                    "max_fork_level": 30,
                    "modifiers": [
                        {"property_id": "AtkBase", "value": 18},
                        {"property_id": "HPMaxUp", "value": 0.2062},
                    ],
                },
            ],
        }

        self.assertEqual(
            {"AtkBase": 90.0, "HPMaxUp": 0.165},
            fork_panel_stats(template, 19),
        )
        self.assertEqual(
            {"AtkBase": 96.0, "HPMaxUp": 0.165},
            fork_panel_stats(template, 20, breakthrough_stage=0),
        )
        self.assertEqual(
            {"AtkBase": 114.0, "HPMaxUp": 0.2062},
            fork_panel_stats(template, 20, breakthrough_stage=1),
        )
        self.assertEqual(
            {"AtkBase": 118.0, "HPMaxUp": 0.2062},
            fork_panel_stats(template, 21),
        )

    def test_fork_active_panel_stats_adds_exact_refinement_property(self) -> None:
        template = {
            "upgrade_levels": [{
                "level": 20,
                "modifiers": [{"property_id": "AtkBase", "value": 100}],
            }],
            "breakthroughs": [{
                "stage": 1,
                "max_fork_level": 20,
                "modifiers": [{"property_id": "AtkBase", "value": 25}],
            }],
            "permanent_properties": [
                {"refinement_level": 1, "property_id": "CritBase", "property_value": 0.16},
                {"refinement_level": 5, "property_id": "CritBase", "property_value": 0.32},
            ],
        }

        self.assertEqual({"CritBase": 0.16}, fork_permanent_stats(template, 1))
        self.assertEqual({"CritBase": 0.32}, fork_permanent_stats(template, 5))
        self.assertEqual(
            {"AtkBase": 125.0, "CritBase": 0.32},
            fork_active_panel_stats(
                template, 20, breakthrough_stage=1, refinement_level=5,
            ),
        )


if __name__ == "__main__":
    unittest.main()
