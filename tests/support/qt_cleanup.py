# 回收单个用例新建的顶层窗口，防止整页控件跨用例存活拖慢后续全局样式刷新；不关闭用例开始前已存在的窗口。

import pytest

THREAD_EXIT_TIMEOUT_SECONDS = 10


def _wait_child_threads(app, windows):
    """删除窗口前等其后台线程物理退场；销毁运行中的 QThread 会让 Qt 中止整个测试进程。"""
    from time import monotonic
    from PySide6.QtCore import QThread

    deadline = monotonic() + THREAD_EXIT_TIMEOUT_SECONDS
    for window in windows:
        for thread in window.findChildren(QThread):
            # 线程收尾可能依赖主线程处理排队信号，等待期间继续推进事件。
            while thread.isRunning() and monotonic() < deadline:
                app.processEvents()
                thread.wait(10)
            if thread.isRunning():
                raise AssertionError(
                    f"{type(window).__name__} 的后台线程未在 {THREAD_EXIT_TIMEOUT_SECONDS} 秒内退场；"
                    "用例须先停止或等待自己的任务，夹具不会销毁仍在运行的线程。")


@pytest.fixture(autouse=True)
def dispose_windows_created_by_test():
    """只回收本例新建的顶层窗口并当场完成删除。

    无事件循环时 ``deleteLater`` 不会自行执行，页面又常被信号闭包引用而无法被垃圾回收；
    残留控件会让每次 ``apply_app_theme`` 重新套用样式的成本随用例数线性增长。
    用例开始前已存在的窗口（类级夹具、共享应用对象）按 C++ 地址保留，不做全局关窗。
    """
    import shiboken6
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    existing = {shiboken6.getCppPointer(widget)[0] for widget in app.topLevelWidgets()}
    yield
    created = [widget for widget in app.topLevelWidgets()
               if shiboken6.getCppPointer(widget)[0] not in existing]
    # 用例自己调用过 deleteLater 的窗口也会在下面的立即删除中析构，因此先等全部新窗口的线程退场。
    _wait_child_threads(app, created)
    # 有父对象的独立窗口（下拉框弹出容器、子对话框）归父控件所有；补全弹窗等虽无父对象，
    # 也归 C++ 侧 owner（如 QCompleter）。单独删除会让 owner 留下悬空指针，
    # 因此只回收无父对象且仍由 Python 持有所有权的根窗口，其余随 owner 析构。
    for widget in created:
        if widget.parent() is None and shiboken6.ownedByPython(widget):
            widget.close()
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
