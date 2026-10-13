# 验证角色页异步加载的草稿保护、账号隔离、缓存失效与快速切换恢复。
"""Official-role behavior under delayed reads and discarded requests."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from src.app.page_tasks import close_page_tasks
from src.features.official_role.controller import OfficialRoleController
from src.features.official_role import role_shell
from src.domain.official_role.models import WorldBonusSettings


def detail(character_id):
    return {"character": {"character_id": character_id}, "profile": {"character_level": 80},
            "property_weights": {}, "main_property_weights": {},
            "equipment_contexts": {"saved": {"available": False}}}


class OfficialRoleAsyncLoadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for name in ("user.sqlite3", "static.sqlite3", "shared.sqlite3"):
            (self.root / name).write_bytes(b"fixture identity only")
        self.window = QWidget()
        self.window.app_context = SimpleNamespace(
            account=SimpleNamespace(active_account_id="test", user_database_path=self.root / "user.sqlite3"),
            generation=1,
            paths=SimpleNamespace(static_database_path=self.root / "static.sqlite3",
                                  shared_database_path=self.root / "shared.sqlite3", role_catalog=None),
        )
        self.window._go = lambda _key: None
        self.layout = QVBoxLayout(self.window)
        self.patches = [
            patch.object(OfficialRoleController, "load_index", return_value=[
                {"character_id": value, "name_zh": str(value)} for value in (1001, 1002)
            ]),
            patch.object(OfficialRoleController, "load_world_bonus", return_value=WorldBonusSettings()),
            patch.object(OfficialRoleController, "load_detail", side_effect=lambda value, **_kw: detail(value)),
        ]
        for name in ("base", "awakening", "skill", "margin", "fork", "drive_summary", "damage_formula", "weight"):
            self.patches.append(patch.object(role_shell, f"_build_{name}_group", side_effect=lambda *_args: QWidget()))
        for active in self.patches:
            active.start()

    def tearDown(self) -> None:
        close_page_tasks(self.window)
        self.window.app_context.generation += 1
        controller = getattr(self.window, "_official_role_controller", None)
        if controller is not None:
            self.wait(lambda: not controller.is_loading())
        for active in reversed(self.patches):
            active.stop()
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def wait(self, predicate) -> None:
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate())

    def build(self) -> None:
        self.layout.addWidget(role_shell._page_my_role(self.window))

    def ready(self) -> bool:
        tabs = getattr(self.window, "official_role_tabs", None)
        return bool(tabs is not None and tabs.currentWidget() is not None and tabs.currentWidget().property("loaded"))


    def test_refresh_preserves_unsaved_draft_even_after_database_changes(self) -> None:
        self.build()
        self.wait(self.ready)
        self.window._official_role_dirty_ids.add(1001)
        self.window._my_role_dirty = True
        editor = self.window._official_role_editors[1001]
        editor["detail"]["profile"]["character_level"] = 70
        (self.root / "user.sqlite3").write_bytes(b"new file identity")
        role_shell._refresh_my_role(self.window)
        self.assertIs(editor, self.window._official_role_editors[1001])
        self.assertEqual(70, editor["detail"]["profile"]["character_level"])

    def test_old_account_index_result_is_discarded(self) -> None:
        release = threading.Event()
        with patch.object(OfficialRoleController, "load_index", side_effect=lambda: (release.wait(2), [
            {"character_id": 1001, "name_zh": "fixture"}
        ])[1]):
            self.build()
            self.window.app_context.generation = 2
            release.set()
            self.wait(lambda: not self.window._official_role_controller.is_loading())
        self.assertIsNone(getattr(self.window, "official_role_tabs", None))
        self.assertEqual({}, self.window._official_role_editors)

    def test_cancelled_tab_read_can_be_selected_again(self) -> None:
        self.build()
        self.wait(self.ready)
        self.wait(lambda: not self.window._official_role_controller.is_loading())
        tabs = self.window.official_role_tabs
        release = threading.Event()
        with patch.object(OfficialRoleController, "load_detail", side_effect=lambda value, **_kw: (
            release.wait(2) if value == 1002 else None, detail(value)
        )[1]):
            tabs.setCurrentIndex(1)
            tabs.setCurrentIndex(0)
            # Start another request to supersede the unfinished second tab.
            controller = self.window._official_role_controller
            controller.request_detail(1001, lambda _result: None, lambda _error: None)
            self.assertFalse(tabs.widget(1)._detail_requested)
            release.set()
            self.wait(lambda: not controller.is_loading())
            tabs.setCurrentIndex(1)
            self.wait(self.ready)
        self.assertIn(1002, self.window._official_role_editors)



if __name__ == "__main__":
    unittest.main()
