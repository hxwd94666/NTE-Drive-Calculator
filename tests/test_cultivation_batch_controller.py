# 测试多角色养成工作器的末次请求胜出和上下文过期丢弃。
from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _request(dataset: str):
    from src.services.cultivation_batch_planner_service import CultivationBatchRequest

    return CultivationBatchRequest("account", 1, dataset, 60, 7, ())


def _wait_until(predicate, *, timeout: float = 3.0) -> None:
    from PySide6.QtWidgets import QApplication

    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.01)


def _dispose_owner(owner) -> None:
    from PySide6.QtCore import QEvent
    from PySide6.QtWidgets import QApplication

    owner.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def test_batch_controller_serializes_and_only_publishes_latest_request() -> None:
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from src.features.toolbox.cultivation_batch_controller import (
        CultivationBatchController,
    )

    class Service:
        calls = []

        def calculate(self, request):
            self.calls.append(request.dataset_identity)
            if request.dataset_identity == "first":
                time.sleep(0.08)
            return request.dataset_identity

    QApplication.instance() or QApplication([])
    owner = QObject()
    controller = CultivationBatchController(
        Service(),
        context_identity=lambda: ("account", 1, "dataset"),
        parent=owner,
    )
    results = []
    controller.result_ready.connect(results.append)

    identity = ("account", 1, "dataset")
    controller.submit(_request("first"), identity)
    controller.submit(_request("latest"), identity)
    _wait_until(lambda: results == ["latest"])

    assert results == ["latest"]
    assert controller._service.calls == ["first", "latest"]
    controller.close()
    _dispose_owner(owner)


def test_batch_controller_discards_result_after_context_change() -> None:
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from src.features.toolbox.cultivation_batch_controller import (
        CultivationBatchController,
    )

    class Service:
        def calculate(self, request):
            time.sleep(0.05)
            return request.dataset_identity

    QApplication.instance() or QApplication([])
    identity = {"value": ("account", 1, "dataset-a")}
    owner = QObject()
    controller = CultivationBatchController(
        Service(),
        context_identity=lambda: identity["value"],
        parent=owner,
    )
    results = []
    controller.result_ready.connect(results.append)
    controller.submit(_request("dataset-a"), identity["value"])
    identity["value"] = ("account", 2, "dataset-b")
    _wait_until(lambda: controller._worker is None)

    assert results == []
    controller.close()
    _dispose_owner(owner)


def test_batch_controller_close_waits_for_active_worker_before_owner_disposal() -> None:
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from src.features.toolbox.cultivation_batch_controller import (
        CultivationBatchController,
    )

    class Service:
        def calculate(self, request):
            time.sleep(0.05)
            return request.dataset_identity

    QApplication.instance() or QApplication([])
    owner = QObject()
    controller = CultivationBatchController(
        Service(), context_identity=None, parent=owner,
    )
    results = []
    controller.result_ready.connect(results.append)
    controller.submit(_request("dataset"), None)
    worker = controller._worker
    controller.close()

    assert worker is not None and not worker.isRunning()
    assert results == []
    _dispose_owner(owner)


def test_batch_controller_keeps_result_when_only_database_access_time_changes(
    tmp_path,
) -> None:
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from src.features.toolbox.cultivation_batch_controller import (
        CultivationBatchController,
    )
    from src.features.toolbox.toolbox_navigation import (
        cultivation_context_identity,
    )

    database = tmp_path / "game_static.sqlite3"
    database.write_bytes(b"static")
    identity = lambda: cultivation_context_identity("account", 1, database)

    class Service:
        def calculate(self, request):
            stat = database.stat()
            os.utime(
                database,
                ns=(stat.st_atime_ns + 1_000_000_000, stat.st_mtime_ns),
            )
            return request.dataset_identity

    QApplication.instance() or QApplication([])
    owner = QObject()
    controller = CultivationBatchController(
        Service(),
        context_identity=identity,
        parent=owner,
    )
    results = []
    controller.result_ready.connect(results.append)
    controller.submit(_request("dataset"), identity())
    _wait_until(lambda: controller._worker is None)

    assert results == ["dataset"]
    controller.close()
    _dispose_owner(owner)


def test_batch_controller_invalidates_running_result_on_draft_edit() -> None:
    from PySide6.QtCore import QObject
    from PySide6.QtWidgets import QApplication

    from src.features.toolbox.cultivation_batch_controller import (
        CultivationBatchController,
    )

    class Service:
        def calculate(self, request):
            time.sleep(0.05)
            return request.dataset_identity

    QApplication.instance() or QApplication([])
    owner = QObject()
    controller = CultivationBatchController(
        Service(),
        context_identity=lambda: ("account", 1, "dataset"),
        parent=owner,
    )
    results = []
    controller.result_ready.connect(results.append)
    controller.submit(_request("dataset"), ("account", 1, "dataset"))
    controller.invalidate()
    _wait_until(lambda: controller._worker is None)

    assert results == []
    controller.close()
    _dispose_owner(owner)


def test_batch_controller_delivers_worker_result_on_gui_thread() -> None:
    from PySide6.QtCore import QObject, QThread
    from PySide6.QtWidgets import QApplication

    from src.features.toolbox.cultivation_batch_controller import (
        CultivationBatchController,
    )

    class Service:
        def calculate(self, request):
            return request.dataset_identity

    QApplication.instance() or QApplication([])
    gui_thread = QThread.currentThread()
    owner = QObject()
    controller = CultivationBatchController(
        Service(), context_identity=None, parent=owner,
    )
    result_threads = []
    controller.result_ready.connect(
        lambda _result: result_threads.append(QThread.currentThread() == gui_thread)
    )

    controller.submit(_request("dataset"), None)
    _wait_until(lambda: controller._worker is None)

    assert result_threads == [True]
    controller.close()
    _dispose_owner(owner)
