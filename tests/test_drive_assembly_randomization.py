# 验证驱动装配的随机化上下文和辅助函数。
"""Tests for drive assembly randomization helpers and backend integration."""

import unittest

import numpy as np

from src.features.drive_assembly.randomization import (
    RandomizationContext,
    jitter_position,
    jitter_scroll_endpoint,
    jitter_timing,
    jitter_duration_ms,
    path_noise_offset,
)


class RandomizationContextTests(unittest.TestCase):
    def test_context_is_disabled_by_default(self):
        ctx = RandomizationContext()
        self.assertFalse(ctx.enabled)

    def test_seed_makes_rng_reproducible(self):
        a = RandomizationContext()
        b = RandomizationContext()
        a.seed(42)
        b.seed(42)
        for _ in range(20):
            self.assertEqual(a.rng.randint(0, 1000), b.rng.randint(0, 1000))


class JitterPositionTests(unittest.TestCase):
    def test_returns_identity_when_disabled(self):
        ctx = RandomizationContext()
        result = jitter_position(ctx, (100, 200), 5)
        self.assertEqual((100, 200), result)

    def test_returns_identity_when_max_offset_is_zero(self):
        ctx = RandomizationContext(enabled=True)
        result = jitter_position(ctx, (100, 200), 0)
        self.assertEqual((100, 200), result)

    def test_jitter_stays_within_range(self):
        ctx = RandomizationContext(enabled=True)
        ctx.seed(123)
        for _ in range(50):
            result = jitter_position(ctx, (500, 500), 3)
            self.assertGreaterEqual(result[0], 497)
            self.assertLessEqual(result[0], 503)
            self.assertGreaterEqual(result[1], 497)
            self.assertLessEqual(result[1], 503)

    def test_jitter_is_reproducible_with_same_seed(self):
        ctx1 = RandomizationContext(enabled=True)
        ctx1.seed(77)
        results1 = [jitter_position(ctx1, (100, 100), 5) for _ in range(20)]

        ctx2 = RandomizationContext(enabled=True)
        ctx2.seed(77)
        results2 = [jitter_position(ctx2, (100, 100), 5) for _ in range(20)]

        self.assertEqual(results1, results2)

    def test_independent_contexts_produce_different_sequences(self):
        ctx1 = RandomizationContext(enabled=True)
        ctx2 = RandomizationContext(enabled=True)
        ctx1.seed(1)
        ctx2.seed(9999)
        results1 = [jitter_position(ctx1, (0, 0), 10) for _ in range(10)]
        results2 = [jitter_position(ctx2, (0, 0), 10) for _ in range(10)]
        self.assertNotEqual(results1, results2)

    def test_does_not_affect_global_random_state(self):
        import random
        before = random.getstate()
        ctx = RandomizationContext(enabled=True)
        for _ in range(100):
            jitter_position(ctx, (0, 0), 10)
        after = random.getstate()
        self.assertEqual(before, after)


class JitterScrollEndpointTests(unittest.TestCase):
    def test_preserves_scroll_direction_downward(self):
        ctx = RandomizationContext(enabled=True)
        ctx.seed(42)
        for _ in range(30):
            result = jitter_scroll_endpoint(ctx, (200, 900), (200, 200), 3)
            # Must remain above end.y (200) — end should not cross below start
            self.assertLess(result[1], 900, "end y must stay above start y (downward scroll)")

    def test_preserves_scroll_direction_upward(self):
        ctx = RandomizationContext(enabled=True)
        ctx.seed(42)
        for _ in range(30):
            result = jitter_scroll_endpoint(ctx, (200, 200), (200, 900), 3)
            self.assertGreater(result[1], 200, "end y must stay below start y (upward scroll)")

    def test_preserves_horizontal_direction_rightward(self):
        ctx = RandomizationContext(enabled=True)
        ctx.seed(42)
        for _ in range(30):
            result = jitter_scroll_endpoint(ctx, (100, 500), (800, 500), 3)
            self.assertGreater(result[0], 100, "end x must stay right of start x")

    def test_returns_identity_when_disabled(self):
        ctx = RandomizationContext()
        result = jitter_scroll_endpoint(ctx, (200, 900), (200, 200), 3)
        self.assertEqual((200, 200), result)


class JitterTimingTests(unittest.TestCase):
    def test_returns_identity_when_disabled(self):
        ctx = RandomizationContext()
        self.assertEqual(0.45, jitter_timing(ctx, 0.45))

    def test_returns_identity_when_jitter_fraction_is_zero(self):
        ctx = RandomizationContext(enabled=True, timing_jitter_fraction=0.0)
        self.assertEqual(0.45, jitter_timing(ctx, 0.45))

    def test_jitter_stays_within_fraction_range(self):
        ctx = RandomizationContext(enabled=True, timing_jitter_fraction=0.15)
        ctx.seed(42)
        base = 1.0
        for _ in range(50):
            result = jitter_timing(ctx, base)
            self.assertGreaterEqual(result, 0.85)
            self.assertLessEqual(result, 1.15)

    def test_jitter_never_returns_negative(self):
        ctx = RandomizationContext(enabled=True, timing_jitter_fraction=2.0)
        ctx.seed(42)
        for _ in range(50):
            result = jitter_timing(ctx, 0.0)
            self.assertGreaterEqual(result, 0.0)

    def test_base_zero_always_returns_zero(self):
        ctx = RandomizationContext(enabled=True)
        self.assertEqual(0.0, jitter_timing(ctx, 0.0))


class JitterDurationMsTests(unittest.TestCase):
    def test_returns_identity_when_disabled(self):
        ctx = RandomizationContext()
        self.assertEqual(700, jitter_duration_ms(ctx, 700))

    def test_returns_at_least_one_ms(self):
        ctx = RandomizationContext(enabled=True, timing_jitter_fraction=2.0)
        ctx.seed(42)
        for _ in range(20):
            result = jitter_duration_ms(ctx, 10)
            self.assertGreaterEqual(result, 1)

    def test_is_reproducible(self):
        ctx1 = RandomizationContext(enabled=True)
        ctx1.seed(55)
        results1 = [jitter_duration_ms(ctx1, 700) for _ in range(10)]

        ctx2 = RandomizationContext(enabled=True)
        ctx2.seed(55)
        results2 = [jitter_duration_ms(ctx2, 700) for _ in range(10)]

        self.assertEqual(results1, results2)


class PathNoiseOffsetTests(unittest.TestCase):
    def test_returns_zero_when_disabled(self):
        ctx = RandomizationContext()
        self.assertEqual((0, 0), path_noise_offset(ctx))

    def test_returns_zero_when_noise_pixels_is_zero(self):
        ctx = RandomizationContext(enabled=True, path_noise_pixels=0)
        self.assertEqual((0, 0), path_noise_offset(ctx))

    def test_stays_within_range(self):
        ctx = RandomizationContext(enabled=True, path_noise_pixels=5)
        ctx.seed(42)
        for _ in range(50):
            dx, dy = path_noise_offset(ctx)
            self.assertGreaterEqual(dx, -5)
            self.assertLessEqual(dx, 5)
            self.assertGreaterEqual(dy, -5)
            self.assertLessEqual(dy, 5)


class DisabledDefaultIntegrationTests(unittest.TestCase):
    """Verify that existing backend behaviour is unchanged when randomization is disabled."""

    def test_pyautogui_click_passes_position_unchanged(self):
        from src.features.drive_assembly.executor import PyAutoGuiMouseBackend

        calls = []

        class FakePA:
            def mouseUp(self, button=None):
                pass

            def moveTo(self, x, y, **kwargs):
                calls.append(("move", x, y))

            def mouseDown(self, button=None):
                calls.append(("down",))

        class FakeSendInput:
            available = False

        backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
        backend._randomization = RandomizationContext()  # disabled
        backend._send_input = FakeSendInput()
        backend._pyautogui = FakePA()

        backend.click((320, 240))

        self.assertIn(("move", 320, 240), calls)

    def test_pyautogui_drag_uses_diagonal_path_when_disabled(self):
        from src.features.drive_assembly.executor import PyAutoGuiMouseBackend

        calls = []

        class FakePA:
            def mouseUp(self, **kwargs):
                calls.append(("up",))

            def moveTo(self, *pos, **kwargs):
                calls.append(("move", pos, kwargs.get("duration")))

            def mouseDown(self, **kwargs):
                calls.append(("down",))

        class FakeSendInput:
            available = False

        backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
        backend._randomization = RandomizationContext()  # disabled
        backend._send_input = FakeSendInput()
        backend._pyautogui = FakePA()
        backend._sleeper = lambda s: calls.append(("sleep", round(s, 3)))

        backend.drag((120, 840), (720, 260), 700)

        # Two moves: initial moveTo(start), then one diagonal move to end.
        moves = [c for c in calls if c[0] == "move"]
        self.assertEqual(2, len(moves), "Should produce 2 moves: start placement + diagonal drag")
        # Initial position.
        self.assertEqual((120, 840), moves[0][1])
        # Diagonal move reaches the full endpoint.
        self.assertEqual((720, 260), moves[1][1])
        # Full requested movement duration is used for that diagonal move.
        self.assertEqual(0.7, moves[1][2])

    def test_enable_randomization_turns_on_context(self):
        from src.features.drive_assembly.executor import PyAutoGuiMouseBackend

        backend = PyAutoGuiMouseBackend()
        self.assertFalse(backend._randomization.enabled)

        backend.enable_randomization(seed=42)
        self.assertTrue(backend._randomization.enabled)
        self.assertTrue(backend._send_input._randomization.enabled)


    def test_enabled_randomization_produces_variable_drag_positions(self):
        from src.features.drive_assembly.executor import PyAutoGuiMouseBackend

        calls = []

        class FakePA:
            def mouseUp(self, **kwargs):
                calls.append(("up",))
            def moveTo(self, *pos, **kwargs):
                calls.append(("move", pos, kwargs.get("duration")))
            def mouseDown(self, **kwargs):
                calls.append(("down",))

        class FakeSendInput:
            available = False

        backend = PyAutoGuiMouseBackend.__new__(PyAutoGuiMouseBackend)
        backend._randomization = RandomizationContext(enabled=True, drag_start_offset_range=5)
        backend._randomization.seed(42)
        backend._send_input = FakeSendInput()
        backend._pyautogui = FakePA()
        backend._sleeper = lambda s: None

        # Run multiple drags — first move positions should vary.
        start_positions = []
        for _ in range(5):
            calls.clear()
            backend.drag((120, 840), (720, 260), 700)
            first_move = next(c for c in calls if c[0] == "move")
            start_positions.append(first_move[1][:2])

        # All 5 drags should not be identical
        self.assertEqual(len(start_positions), len(set(start_positions)),
                         "Randomization enabled: drag start positions should vary across calls")


    def test_enabled_randomization_is_backend_isolated(self):
        from src.features.drive_assembly.executor import PyAutoGuiMouseBackend

        b1 = PyAutoGuiMouseBackend(randomization=RandomizationContext(enabled=True))
        b2 = PyAutoGuiMouseBackend()
        self.assertTrue(b1._randomization.enabled)
        self.assertFalse(b2._randomization.enabled)


if __name__ == "__main__":
    unittest.main()
