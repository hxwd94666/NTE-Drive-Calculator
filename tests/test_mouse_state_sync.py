from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from src.integrations.vision.equipment_state_detection import (
    lock_to_discard_confirmation_visible,
    right_panel_button_state_from_image,
)
from src.integrations.vision.mouse_inventory_scan import MouseInventoryLayout
from src.integrations.vision.mouse_state_sync import (
    CONFIRM_BUTTON_CENTER,
    LOCK_BUTTON_CENTER,
    TRASH_BUTTON_CENTER,
    MouseEquipmentStateSync,
)
from src.scanner.window_capture import WindowRect


class EquipmentStateGoldenTests(unittest.TestCase):
    @staticmethod
    def _state_image(*, trash=False, lock=False):
        image = np.full((1440, 2560, 3), 24, dtype=np.uint8)
        for active, center in ((trash, TRASH_BUTTON_CENTER), (lock, LOCK_BUTTON_CENTER)):
            cx = round(2560 * center[0])
            cy = round(1440 * center[1])
            image[cy - 16 : cy + 16, cx - 16 : cx + 16] = 210 if active else 55
        return image

    def test_state_images_classify_normal_discarded_and_locked(self) -> None:
        expected = (
            (self._state_image(), "normal"),
            (self._state_image(trash=True), "discarded"),
            (self._state_image(lock=True), "locked"),
        )
        for image, state in expected:
            with self.subTest(state=state):
                self.assertEqual(state, right_panel_button_state_from_image(image))

    def test_ambiguous_dual_active_buttons_are_not_treated_as_a_state(self) -> None:
        self.assertEqual(
            "unknown",
            right_panel_button_state_from_image(self._state_image(trash=True, lock=True)),
        )

    def test_lock_to_discard_popup_is_detected(self) -> None:
        image = np.full((1440, 2560, 3), 20, dtype=np.uint8)
        image[round(1440 * 0.35) : round(1440 * 0.48), round(2560 * 0.25) : round(2560 * 0.75)] = 220
        self.assertTrue(lock_to_discard_confirmation_visible(image))


class MouseStateSyncTests(unittest.TestCase):
    class Input:
        def __init__(self):
            self.clicks = []
            self.scrolls = []

        def click(self, position, *, content_height):
            self.clicks.append((position, content_height))
            return position

        def scroll(self, position, amount):
            self.scrolls.append((position, amount))

    class Scanner:
        CLICK_SAFE_OFFSET_Y_2K = -20

        def __init__(self, states, output_dir="."):
            self.output_dir = output_dir
            if output_dir != ".":
                Path(output_dir).mkdir(parents=True, exist_ok=True)
                for index in (1, 2):
                    cv2.imwrite(
                        str(Path(output_dir) / f"raw_drive_{index:04d}.png"),
                        np.zeros((8, 8, 3), dtype=np.uint8),
                    )
            self.layout = MouseInventoryLayout()
            self._input = MouseStateSyncTests.Input()
            self._stopped = False
            self.states = iter(states)
            self.frame = SimpleNamespace(
                image=SimpleNamespace(),
                rect=WindowRect(40, 60, 2600, 1500),
            )

        def _capture_frame(self, *, freeze=False):
            return self.frame

        def _content_height(self, frame):
            return 1440

        def _wait_for_selected_panel(self, slot, row_offset_px):
            return self.frame

        def _scroll_amounts_for_flip(self, flip_number):
            return (-280, -120)

        def _panel_signature(self, image):
            return np.zeros((8, 8), dtype=np.uint8)

    @staticmethod
    def _sync(scanner, **kwargs):
        return MouseEquipmentStateSync(
            scanner,
            identity_verifier=lambda _index, _image: True,
            **kwargs,
        )

    def test_changes_are_deduplicated_and_sorted_descending(self) -> None:
        changes = MouseEquipmentStateSync._validated_changes(
            50,
            [
                {"index": 2, "current_state": "normal", "target_state": "locked"},
                {"index": 49, "current_state": "discarded", "target_state": "normal"},
                {"index": 2, "current_state": "normal", "target_state": "discarded"},
            ],
        )
        self.assertEqual([49, 2], [row["index"] for row in changes])

    def test_reverse_page_navigation_uses_positive_inverse_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scanner = self.Scanner(["normal", "locked"], tmp)
            sync = MouseEquipmentStateSync(
                scanner,
                state_detector=lambda _image: next(scanner.states),
            sleep_fn=lambda _seconds: None,
            )
            sync._click_ratio = lambda frame, _ratio: frame

            applied = sync.sync(
                50,
                [{"index": 2, "current_state": "normal", "target_state": "locked"}],
            )
            report = Path(tmp, "mouse_state_sync_last_report.json").read_text(encoding="utf-8")

        self.assertEqual(1, applied)
        self.assertIn('"status": "complete"', report)
        self.assertEqual(
            [120, 280, 120, 280, 280, 280, 280, 280],
            [amount for _position, amount in scanner._input.scrolls],
        )

    def test_stale_current_state_stops_before_clicking_an_action(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            scanner = self.Scanner(["discarded"], tmp)
            sync = MouseEquipmentStateSync(
                scanner,
                state_detector=lambda _image: next(scanner.states),
                sleep_fn=lambda _seconds: None,
            )
            with self.assertRaisesRegex(RuntimeError, "画面为 discarded.*计划为 normal"):
                sync.sync(
                    7,
                    [{"index": 1, "current_state": "normal", "target_state": "locked"}],
                )
            report = Path(tmp, "mouse_state_sync_last_report.json").read_text(encoding="utf-8")
        self.assertEqual(1, len(scanner._input.clicks))
        self.assertIn('"status": "error"', report)

    def test_lock_to_discard_waits_then_confirms_and_verifies(self) -> None:
        scanner = self.Scanner(["discarded"])
        sleeps = []
        clicks = []
        sync = MouseEquipmentStateSync(
            scanner,
            state_detector=lambda _image: next(scanner.states),
            sleep_fn=lambda seconds: sleeps.append(seconds),
        )
        sync._click_ratio = lambda frame, ratio: clicks.append(ratio) or frame

        popup_states = iter((True, False))
        sync.popup_detector = lambda _image: next(popup_states)
        frame = sync._apply_transition(scanner.frame, "locked", "discarded")

        self.assertIs(frame, scanner.frame)
        self.assertEqual([TRASH_BUTTON_CENTER, CONFIRM_BUTTON_CENTER], clicks)
        self.assertEqual([1.0, 0.5], sleeps)

    def test_all_direct_transitions_click_expected_icon(self) -> None:
        cases = (
            ("normal", "discarded", TRASH_BUTTON_CENTER),
            ("normal", "locked", LOCK_BUTTON_CENTER),
            ("discarded", "normal", TRASH_BUTTON_CENTER),
            ("locked", "normal", LOCK_BUTTON_CENTER),
            ("discarded", "locked", LOCK_BUTTON_CENTER),
        )
        for current, target, expected in cases:
            with self.subTest(current=current, target=target):
                scanner = self.Scanner([])
                sleeps = []
                clicks = []
                sync = MouseEquipmentStateSync(
                    scanner,
                    state_detector=lambda _image: "normal",
                    sleep_fn=lambda seconds: sleeps.append(seconds),
                )
                sync._click_ratio = lambda frame, ratio: clicks.append(ratio) or frame
                sync._apply_transition(scanner.frame, current, target)
                self.assertEqual([expected], clicks)
                self.assertEqual([0.5], sleeps)


if __name__ == "__main__":
    unittest.main()
