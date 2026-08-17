# 测试鼠标扫描输入动作。
"""Public timing and coordinate contracts for mouse inventory scan input."""

import random
import unittest

from src.integrations.vision.mouse_scan_input import (
    MouseInputRandomization,
    inventory_top_reset_swipe_points,
)


class MouseInputRandomizationTests(unittest.TestCase):
    def test_click_jitter_is_seeded_for_tests_and_stays_inside_scaled_bounds(self) -> None:
        profile = MouseInputRandomization()
        first_rng = random.Random(2468)
        second_rng = random.Random(2468)

        first = [profile.jitter_position((1000, 700), 2160, first_rng) for _ in range(20)]
        second = [profile.jitter_position((1000, 700), 2160, second_rng) for _ in range(20)]

        self.assertEqual(first, second)
        self.assertGreater(len(set(first)), 1)
        self.assertTrue(all(abs(x - 1000) <= 8 and abs(y - 700) <= 8 for x, y in first))

    def test_click_timing_uses_ranges_instead_of_a_fixed_period(self) -> None:
        profile = MouseInputRandomization()
        rng = random.Random(7)

        move_values = [profile.move_seconds(rng) for _ in range(12)]
        hold_values = [profile.hold_seconds(rng) for _ in range(12)]

        self.assertTrue(all(0.025 <= value <= 0.060 for value in move_values))
        self.assertTrue(all(0.018 <= value <= 0.035 for value in hold_values))
        self.assertGreater(len(set(move_values)), 1)
        self.assertGreater(len(set(hold_values)), 1)

    def test_fast_trial_profile_halves_input_budget_but_keeps_frame_safe_delays(self) -> None:
        standard = MouseInputRandomization()
        fast = MouseInputRandomization.fast_trial()

        standard_mean = sum(
            (low + high) / 2
            for low, high in (
                standard.move_seconds_range,
                standard.hold_seconds_range,
                standard.after_click_seconds_range,
                standard.between_items_seconds_range,
            )
        )
        fast_mean = sum(
            (low + high) / 2
            for low, high in (
                fast.move_seconds_range,
                fast.hold_seconds_range,
                fast.after_click_seconds_range,
                fast.between_items_seconds_range,
            )
        )

        self.assertLess(fast_mean, standard_mean * 0.55)
        self.assertGreaterEqual(fast.after_click_seconds_range[0], 1 / 30)
        self.assertGreaterEqual(fast.after_scroll_seconds_range[0], 1 / 30)

    def test_one_point_five_trial_slows_fast_input_without_restoring_low_load_pause(self) -> None:
        fast = MouseInputRandomization.fast_trial()
        balanced = MouseInputRandomization.one_point_five_trial()

        def input_budget(profile):
            return sum(
                (low + high) / 2
                for low, high in (
                    profile.move_seconds_range,
                    profile.hold_seconds_range,
                    profile.after_click_seconds_range,
                    profile.between_items_seconds_range,
                )
            )

        self.assertAlmostEqual(1.51, input_budget(balanced) / input_budget(fast), delta=0.08)
        self.assertGreaterEqual(balanced.after_click_seconds_range[0], 2 / 30)
        self.assertGreaterEqual(balanced.after_scroll_seconds_range[0], 2 / 30)

    def test_inventory_reset_points_reverse_the_incremental_swipe(self) -> None:
        start, end = inventory_top_reset_swipe_points(
            left=40,
            top=60,
            width=2560,
            height=1440,
        )

        self.assertEqual((1320, 360), start)
        self.assertEqual((1320, 1360), end)
        self.assertLess(start[1], end[1])
