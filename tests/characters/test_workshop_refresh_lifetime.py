# 验证启动刷新单次有界、退出撤权和迟到网络结果不替换原模板。
from __future__ import annotations

from concurrent.futures import CancelledError
import json
from threading import Event, Thread
from time import monotonic, sleep
from unittest.mock import MagicMock, Mock

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication, QWidget
import pytest

from src.services.characters import workshop_weight_template_service as service
from src.integrations import workshop_weight_http as http
from src.ui.controllers.workshop_refresh_controller import WorkshopRefreshController

NTE_TEST_TIER = "core"


@pytest.fixture
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app
    app.sendPostedEvents(None, QEvent.DeferredDelete)


def _until(app, predicate, timeout=2):
    deadline = monotonic() + timeout
    while not predicate() and monotonic() < deadline:
        app.processEvents()
        sleep(0.001)
    assert predicate()


class Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return json.dumps({"code": 200, "data": [{
            "itemId": "1003", "name": "角色一",
            "weightConfig": {"weights": [{"name": "暴击率", "value": 1.25, "main_value": 0.75}]},
        }]}).encode("utf-8")


def test_close_before_delayed_start_never_starts_work(qt_app, tmp_path, monkeypatch):
    import src.ui.controllers.workshop_refresh_controller as controller

    starter = Mock()
    monkeypatch.setattr(controller, "start_workshop_weight_template_refresh", starter)
    parent = QWidget()
    owner = WorkshopRefreshController(tmp_path / "template.json", tmp_path / "static.sqlite3", parent=parent)
    owner.start(0)
    owner.close()
    owner.start(0)
    qt_app.processEvents()
    assert not owner.is_running()
    starter.assert_not_called()
    parent.deleteLater()


def test_repeated_start_has_one_worker_and_close_waits_without_publishing(qt_app, tmp_path, monkeypatch):
    entered, release = Event(), Event()
    calls = []
    template = tmp_path / "template.json"
    template.write_bytes(b"original-template")

    class SlowResponse(Response):
        def read(self):
            entered.set()
            assert release.wait(2)
            return super().read()

    def fetch(*_args, **_kwargs):
        calls.append(1)
        return SlowResponse().read()

    dao = MagicMock()
    dao.return_value.__enter__.return_value.list_role_template_characters.return_value = [{"character_id": 1003}]
    monkeypatch.setattr(service, "StaticGameDataDao", dao)
    monkeypatch.setattr(http, "fetch_workshop_payload", fetch)
    parent = QWidget()
    owner = WorkshopRefreshController(template, tmp_path / "static.sqlite3", parent=parent)
    try:
        owner.start(0)
        _until(qt_app, entered.is_set)
        for _ in range(30):
            owner.start(0)
        assert owner.is_running()
        began = monotonic()
        owner.close()
        assert monotonic() - began < 0.2
        assert owner.is_running()  # 取消不是物理终态；窗口仍需延后销毁。
        release.set()
        _until(qt_app, lambda: not owner.is_running())
        owner.start(0)
        qt_app.processEvents()
        assert calls == [1]
        assert template.read_bytes() == b"original-template"
        assert list(tmp_path.glob(".*.tmp")) == []
    finally:
        release.set()
        _until(qt_app, lambda: not owner.is_running())
        parent.deleteLater()


def test_cancel_at_atomic_publish_preserves_original_and_removes_temporary(tmp_path, monkeypatch):
    token = service.WorkshopRefreshToken()
    template = tmp_path / "template.json"
    original = b'{"schema_version":1,"characters":{},"payload_sha256":"old"}'
    template.write_bytes(original)
    monkeypatch.setattr(http, "fetch_workshop_payload", lambda *_a, **_k: Response().read())
    fsync = service.os.fsync

    def cancel_after_flush(fd):
        fsync(fd)
        token.set()

    monkeypatch.setattr(service.os, "fsync", cancel_after_flush)
    with pytest.raises(CancelledError):
        service.WorkshopWeightTemplateService(template).refresh(known_character_ids=(1003,), cancel_token=token)
    assert template.read_bytes() == original
    assert list(tmp_path.glob(".*.tmp")) == []


def test_cancelled_refresh_never_opens_network_or_creates_file(tmp_path, monkeypatch):
    token = service.WorkshopRefreshToken()
    token.set()
    network = Mock(side_effect=AssertionError("unexpected network"))
    monkeypatch.setattr(http, "fetch_workshop_payload", network)
    with pytest.raises(CancelledError):
        service.WorkshopWeightTemplateService(tmp_path / "template.json").refresh(
            known_character_ids=(1003,), cancel_token=token,
        )
    network.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_already_accepted_publish_finishes_before_physical_exit(tmp_path, monkeypatch):
    token = service.WorkshopRefreshToken()
    template = tmp_path / "template.json"
    template.write_bytes(b"original-template")
    entered, release = Event(), Event()
    outcome = []
    replace = service.os.replace
    monkeypatch.setattr(http, "fetch_workshop_payload", lambda *_a, **_k: Response().read())

    def blocked_replace(source, target):
        assert token.publish_accepted
        entered.set()
        assert release.wait(2)
        replace(source, target)

    monkeypatch.setattr(service.os, "replace", blocked_replace)
    worker = Thread(target=lambda: outcome.append(service.WorkshopWeightTemplateService(template).refresh(
        known_character_ids=(1003,), cancel_token=token,
    )))
    worker.start()
    try:
        assert entered.wait(1)
        token.set()  # 发布已先接纳，不把这次已接受提交追认成未提交。
        assert worker.is_alive() and token.publish_accepted
        assert template.read_bytes() == b"original-template"
    finally:
        release.set()
        worker.join(2)
    assert not worker.is_alive() and outcome[0].updated
    assert json.loads(template.read_text(encoding="utf-8"))["characters"]["1003"]
    assert list(tmp_path.glob(".*.tmp")) == []


def _dripping_network(monkeypatch):
    # 只有实际 QTimer/局部事件循环驱动分块，替身永不主动发 finished。
    from PySide6.QtCore import QObject, QTimer, Signal
    import PySide6.QtNetwork as network
    state = {"chunks": 0, "aborted": 0, "reply_deleted": 0, "manager_deleted": 0}

    class Reply(QObject):
        readyRead = Signal()
        finished = Signal()

        def __init__(self, parent):
            super().__init__(parent)
            self.done = False
            self.buffer = b""
            self.timer = QTimer(self)
            self.timer.setInterval(3)
            self.timer.timeout.connect(self.drip)
            self.timer.start()
            self.destroyed.connect(lambda: state.update(reply_deleted=state["reply_deleted"] + 1))

        def drip(self):
            state["chunks"] += 1
            self.buffer += b" "
            self.readyRead.emit()

        def setReadBufferSize(self, _size):
            pass

        def readAll(self):
            result, self.buffer = self.buffer, b""
            return result

        def isFinished(self):
            return self.done

        def abort(self):
            if not self.done:
                self.done = True
                state["aborted"] += 1
                self.timer.stop()
                self.finished.emit()

        def error(self):
            return network.QNetworkReply.NoError

    class Manager(QObject):
        def __init__(self):
            super().__init__()
            self.destroyed.connect(lambda: state.update(manager_deleted=state["manager_deleted"] + 1))

        def get(self, _request):
            return Reply(self)

    monkeypatch.setattr(network, "QNetworkAccessManager", Manager)
    return state


def test_continuous_chunks_have_total_deadline_and_release_qt_objects(qt_app, monkeypatch):
    state = _dripping_network(monkeypatch)
    began = monotonic()
    with pytest.raises(TimeoutError, match="总时限"):
        http.fetch_workshop_payload("https://fixture.invalid/", timeout_seconds=0.08,
                                    cancel_check=lambda: False, headers={})
    assert 0.05 <= monotonic() - began < 0.5
    assert state["chunks"] > 2
    assert state["aborted"] == state["reply_deleted"] == state["manager_deleted"] == 1


def test_close_during_continuous_response_releases_actual_worker(qt_app, tmp_path, monkeypatch):
    state = _dripping_network(monkeypatch)
    dao = MagicMock()
    dao.return_value.__enter__.return_value.list_role_template_characters.return_value = [{"character_id": 1003}]
    monkeypatch.setattr(service, "StaticGameDataDao", dao)
    template = tmp_path / "template.json"
    template.write_bytes(b"original-template")
    parent = QWidget()
    owner = WorkshopRefreshController(template, tmp_path / "static.sqlite3", parent=parent)
    try:
        owner.start(0)
        _until(qt_app, lambda: state["chunks"] >= 3)
        assert owner.is_running()
        began = monotonic()
        owner.close()
        assert monotonic() - began < 0.2
        _until(qt_app, lambda: not owner.is_running(), timeout=0.5)
        assert template.read_bytes() == b"original-template"
        assert state["aborted"] == state["reply_deleted"] == state["manager_deleted"] == 1
    finally:
        owner.close()
        _until(qt_app, lambda: not owner.is_running())
        parent.deleteLater()
