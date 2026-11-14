# 验证养成工具首次加载、重试及过期回调隔离，不连接游戏。
from threading import Event
from time import monotonic

import pytest
from PySide6.QtCore import QThread, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from tests.support.auto_sync_ui_fixture import dispose
from tests.support.theme import themed_app as themed_app
from tests.cultivation.cultivation_planner_fixture import PlanningFixture
from src.features.toolbox.cultivation.page import CultivationCalculatorPage
from src.services.progression.models import CultivationRole, CultivationSeed

def wait(predicate):
    deadline = monotonic() + 6
    while not predicate() and monotonic() < deadline:
        QApplication.processEvents()
        QTest.qWait(5)
    assert predicate()


class SlowStartup(PlanningFixture):
    def __init__(self):
        self.entered, self.release = Event(), Event()
        self.threads, self.calls = [], 0
        self.fail = False

    def list_roles(self):
        self.calls += 1
        self.threads.append(QThread.currentThread() == QApplication.instance().thread())
        self.entered.set()
        assert self.release.wait(5)
        if self.fail:
            raise ValueError('fixture loading error')
        return (CultivationRole(1004, '测试角色'),)

    def load_seed(self, identifier):
        self.threads.append(QThread.currentThread() == QApplication.instance().thread())
        return CultivationSeed(identifier, '测试角色', 1, 0, (), None)


def close_page(page, service):
    service.release.set()
    page.shutdown()
    wait(lambda: not page._startup_controller._lane.is_running())
    wait(lambda: not page.calculator._controller.is_running())
    dispose(page)


@pytest.mark.parametrize('late_change', ['none', 'account', 'close'])
def test_first_open_reads_once_off_gui_and_ignores_stale_callbacks(themed_app, late_change):
    service = SlowStartup();identity = ['a']
    page = CultivationCalculatorPage(service, defer_initial_load=True, context_identity=lambda: identity[0])
    ticks = []
    try:
        page.show();wait(service.entered.is_set)
        assert page._initial_loading and page.calculator._seed is None
        QTimer.singleShot(0, page, lambda: ticks.append(1));wait(lambda: bool(ticks))
        if late_change == 'account':
            identity[0] = 'b'
        elif late_change == 'close':
            page.shutdown()
        service.release.set()
        wait(lambda: not page._startup_controller._lane.is_running())
        if late_change == 'none':
            assert page.calculator._seed.character_id == 1004 and not page._initial_loading
            assert page.batch_calculator._roles == page.calculator._roles
        else:
            assert page.calculator._seed is None
        assert service.calls == 1 and service.threads == [False, False]
    finally:
        close_page(page, service)


def test_loading_error_can_retry_and_preparation_never_inserts_busy_hint(themed_app):
    service = SlowStartup();service.fail = True;service.release.set()
    page = CultivationCalculatorPage(service, defer_initial_load=True)
    try:
        page.show();wait(lambda: service.calls == 1 and not page._initial_loading)
        service.fail = False;page._reset();wait(lambda: not page._initial_loading)
        assert service.calls == 2 and page.calculator._seed is not None
        single, batch = page.calculator, page.batch_calculator
        for content in (single, batch):
            content._prepare_timer.stop()
            assert content._preparation_hint.isHidden()
            for _ in range(3):
                content._controller.preparing_changed.emit(True)
                content._controller.preparing_changed.emit(False)
            assert content._preparation_hint.isHidden()
            content._preparation_failed('fixture')
            assert not content._preparation_hint.isHidden()
    finally:
        close_page(page, service)

