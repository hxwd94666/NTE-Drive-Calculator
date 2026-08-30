# 验证扫描和鉴定共用的全局热键管理器拥有独立且互斥的会话。
from __future__ import annotations

import unittest

from src.integrations.global_hotkeys import GlobalHotkeyManager


NTE_TEST_TIER = "core"


class FakeThread:
    def __init__(self, *, target, args, name, daemon):
        self.target = target
        self.args = args
        self.name = name
        self.daemon = daemon
        self.started = False
        self.joined = False

    def start(self):
        self.started = True

    def is_alive(self):
        return self.started and not self.joined

    def join(self, timeout=None):
        del timeout
        self.joined = True


class GlobalHotkeyManagerTests(unittest.TestCase):
    def make_manager(self):
        threads = []

        def thread_factory(**kwargs):
            thread = FakeThread(**kwargs)
            threads.append(thread)
            return thread

        manager = GlobalHotkeyManager(
            capture_hotkey="F9",
            finish_hotkey="F10",
            stop_hotkey="F12",
            battle_rerecord_hotkey="F11",
            thread_factory=thread_factory,
        )
        return manager, threads

    def test_session_routes_only_declared_actions(self):
        manager, threads = self.make_manager()
        actions = []
        manager.start(
            owner="scanning",
            on_stop=lambda: actions.append("stop"),
            on_capture=lambda: actions.append("capture"),
        )
        generation = manager._generation

        manager._dispatch(generation, "capture")
        manager._dispatch(generation, "finish")
        manager._dispatch(generation, "battle_rerecord")
        manager._dispatch(generation, "stop")

        self.assertEqual(["capture", "stop"], actions)
        self.assertEqual("scanning", manager.active_owner)
        self.assertTrue(threads[0].started)

    def test_replacing_owner_invalidates_old_callbacks(self):
        manager, threads = self.make_manager()
        actions = []
        manager.start(
            owner="scanning",
            on_stop=lambda: actions.append("scan-stop"),
        )
        scan_generation = manager._generation
        manager.start(
            owner="identification",
            on_stop=lambda: actions.append("identify-stop"),
            on_finish=lambda: actions.append("identify-finish"),
        )
        identify_generation = manager._generation

        manager._dispatch(scan_generation, "stop")
        manager._dispatch(identify_generation, "finish")

        self.assertEqual(["identify-finish"], actions)
        self.assertTrue(threads[0].joined)
        self.assertEqual("identification", manager.active_owner)

    def test_owner_scoped_stop_cannot_cancel_another_feature(self):
        manager, threads = self.make_manager()
        manager.start(owner="identification", on_stop=lambda: None)

        manager.stop(owner="scanning")
        self.assertEqual("identification", manager.active_owner)
        self.assertFalse(threads[0].joined)

        manager.stop(owner="identification")
        self.assertIsNone(manager.active_owner)
        self.assertTrue(threads[0].joined)

    def test_configuration_is_frozen_for_active_session(self):
        manager, _threads = self.make_manager()
        manager.start(owner="scanning", on_stop=lambda: None)
        generation = manager._generation

        manager.update_configuration(
            capture_hotkey="F6",
            finish_hotkey="F7",
            stop_hotkey="F8",
            battle_rerecord_hotkey="F5",
        )

        active_configuration, _callbacks = manager._session_snapshot(
            generation
        )
        self.assertEqual("F9", active_configuration.capture)
        self.assertEqual("F8", manager.configuration.stop)
        self.assertEqual("F5", manager.configuration.battle_rerecord)

    def test_battle_session_routes_only_rerecord_binding(self):
        manager, _threads = self.make_manager()
        actions = []
        manager.start(
            owner="battle_report",
            on_battle_rerecord=lambda: actions.append("rerecord"),
        )
        generation = manager._generation

        manager._dispatch(generation, "stop")
        manager._dispatch(generation, "battle_rerecord")

        self.assertEqual(["rerecord"], actions)

    def test_supported_virtual_keys_and_invalid_configuration(self):
        self.assertEqual(0x70, GlobalHotkeyManager.hotkey_to_vk("F1"))
        self.assertEqual(0x87, GlobalHotkeyManager.hotkey_to_vk("f24"))
        self.assertEqual(ord("A"), GlobalHotkeyManager.hotkey_to_vk("a"))
        self.assertIsNone(GlobalHotkeyManager.hotkey_to_vk("Ctrl+F9"))

        manager, _threads = self.make_manager()
        with self.assertRaises(ValueError):
            manager.update_configuration(
                capture_hotkey="",
                finish_hotkey="F10",
                stop_hotkey="F12",
                battle_rerecord_hotkey="F11",
            )


if __name__ == "__main__":
    unittest.main()
