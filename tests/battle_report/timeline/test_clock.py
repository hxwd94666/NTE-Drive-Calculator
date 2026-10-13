# 验证完整轴两套时间语义及拖拽范围的反向投影。
from __future__ import annotations

import unittest
import random

from src.services.battle_report.timeline.clock import (
    ACTIVE_TIME_MODE,
    ELAPSED_TIME_MODE,
    project_timeline_time_us,
    projected_range_duration_us,
    unproject_timeline_time_us,
)


class BattleTimelineTimeServiceTests(unittest.TestCase):
    def test_inverse_matches_exhaustive_integer_clock_with_overlaps_and_clipping(self):
        randomizer = random.Random(20260906)
        for _ in range(80):
            start = randomizer.randrange(-20, 10)
            end = start + randomizer.randrange(0, 45)
            intervals = tuple((randomizer.randrange(start - 8, end + 9),
                               randomizer.randrange(start - 8, end + 9))
                              for _ in range(randomizer.randrange(9)))
            intervals += ((None, end), (start, None))
            clock = tuple(project_timeline_time_us(raw, battle_start_us=start,
                                                   intervals=intervals, mode=ACTIVE_TIME_MODE)
                          for raw in range(start, end + 1))
            for target in range(-1, clock[-1] + 2):
                clamped = min(clock[-1], max(0, target))
                candidates = [start + index for index, value in enumerate(clock) if value == clamped]
                for prefer_end in (False, True):
                    # Supply a one-shot iterator: each inversion must freeze its own input once.
                    actual = unproject_timeline_time_us(target, battle_start_us=start, battle_end_us=end,
                        intervals=iter(intervals), mode=ACTIVE_TIME_MODE, prefer_interval_end=prefer_end)
                    self.assertEqual(candidates[-1] if prefer_end else candidates[0], actual,
                                     (start, end, target, prefer_end, intervals))

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
