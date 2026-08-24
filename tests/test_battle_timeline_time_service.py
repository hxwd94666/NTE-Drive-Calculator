# 验证完整轴两套时间语义及拖拽范围的反向投影。
from __future__ import annotations

import unittest

from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    ELAPSED_TIME_MODE,
    project_timeline_time_us,
    projected_range_duration_us,
    unproject_timeline_time_us,
)


class BattleTimelineTimeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.intervals = (
            (2_000_000, 4_000_000),
            (7_000_000, 8_000_000),
        )

    def test_elapsed_and_active_clocks_project_the_same_hit_differently(self):
        self.assertEqual(
            9_000_000,
            project_timeline_time_us(
                9_000_000,
                battle_start_us=0,
                intervals=self.intervals,
                mode=ELAPSED_TIME_MODE,
            ),
        )
        self.assertEqual(
            6_000_000,
            project_timeline_time_us(
                9_000_000,
                battle_start_us=0,
                intervals=self.intervals,
                mode=ACTIVE_TIME_MODE,
            ),
        )

    def test_active_range_duration_only_subtracts_intersection(self):
        self.assertEqual(
            3_000_000,
            projected_range_duration_us(
                3_000_000,
                7_000_000,
                intervals=self.intervals,
                mode=ACTIVE_TIME_MODE,
            ),
        )

    def test_plateau_inverse_uses_front_for_start_and_back_for_end(self):
        start = unproject_timeline_time_us(
            2_000_000,
            battle_start_us=0,
            battle_end_us=10_000_000,
            intervals=self.intervals,
            mode=ACTIVE_TIME_MODE,
        )
        end = unproject_timeline_time_us(
            2_000_000,
            battle_start_us=0,
            battle_end_us=10_000_000,
            intervals=self.intervals,
            mode=ACTIVE_TIME_MODE,
            prefer_interval_end=True,
        )

        self.assertEqual(2_000_000, start)
        self.assertEqual(4_000_000, end)


if __name__ == "__main__":
    unittest.main()
