# 校验配装目录加载不阻塞调用线程：GUI 宿主必须走后台 worker。
"""账号切换与启动都在 GUI 线程重建配装目录，实测约 1.2 秒；这里锁定「不得同步」。

根因（2026-09-28 实测）：`_load_data` 在调用线程同步执行 `_read_allocation_catalog`
（中位 1174ms，一次重建会执行上千条 SQL），同一份逻辑在 `_refresh_execute` 里
却是包在 WorkerThread 中的，线程模型不一致。
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QWidget  # noqa: E402

from src.ui.main_window_data_mixin import MainWindowDataMixin  # noqa: E402
from tests.qt_application_fixture import ensure_qt_application  # noqa: E402


class _RecordingHost:
    """只提供目录加载所需的最小上下文，并记录读取与套用时机。"""

    def __init__(self) -> None:
        self.app_context = SimpleNamespace(
            account=SimpleNamespace(user_database_path=Path("user.sqlite3")),
            paths=SimpleNamespace(
                config_dir=Path("config"),
                equipment_allocation_database_path=Path("static.sqlite3"),
                equipment_allocation_asset_root=Path("assets"),
                workshop_weight_template_file=Path("workshop.json"),
            ),
            generation=1,
        )
        self._allocation_catalog_loaded_key = None
        self._allocation_catalog_pending_key = None
        self.reads: list[object] = []

    def _allocation_catalog_source_key(self):
        return (self.app_context.generation,)

    def _read_allocation_catalog(self, *_args, **_kwargs):
        self.reads.append("read")
        return ("loaded",)

    def _apply_allocation_catalog(self, _loaded, *, reload_priority, source_key):
        self.reads.append(("apply", reload_priority, source_key))


class _PlainHost(_RecordingHost, MainWindowDataMixin):
    """非 GUI 宿主：没有 Qt 事件循环，保持同步读取。"""


class _WidgetHost(_RecordingHost, MainWindowDataMixin, QWidget):
    """GUI 宿主：必须把读取放到后台 worker，而不是在调用线程同步重建。"""

    def __init__(self) -> None:
        QWidget.__init__(self)
        _RecordingHost.__init__(self)
        self.scanning_controller = SimpleNamespace(role_selector=None)


class _Signal:
    def connect(self, *_args, **_kwargs) -> None:
        return None


class _FakeWorker:
    """记录被派发的目标，但不真正启动线程。"""

    instances: list["_FakeWorker"] = []

    def __init__(self, target, parent=None):
        self.target = target
        self.started = False
        self.result_ready = _Signal()
        self.error = _Signal()
        self.finished = _Signal()
        _FakeWorker.instances.append(self)

    def start(self) -> None:
        self.started = True

    def deleteLater(self) -> None:
        return None


class MainWindowCatalogLoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ensure_qt_application()

    def test_plain_host_still_reads_synchronously(self) -> None:
        host = _PlainHost()

        host._load_data()

        self.assertEqual(["read", ("apply", True, (1,))], host.reads)

    def test_widget_host_never_reads_inline(self) -> None:
        host = _WidgetHost()
        _FakeWorker.instances.clear()

        with patch("src.ui.main_window_data_mixin.WorkerThread", _FakeWorker):
            host._load_data()

        self.assertNotIn(
            "read", host.reads,
            "GUI 宿主不得在调用线程同步重建配装目录（实测约 1.2 秒）",
        )


if __name__ == "__main__":
    unittest.main()
