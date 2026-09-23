# 验证多角色结果替换期间的滚动输入在布局稳定后才生效。
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_batch_result_replacement_buffers_immediate_wheel_until_ready() -> None:
    from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QWidget

    from src.features.toolbox.cultivation_page import CultivationCalculatorPage

    class Service:
        def list_roles(self):
            return ()

    app = QApplication.instance() or QApplication([])
    page = CultivationCalculatorPage(Service())
    page.resize(700, 320)
    page.show()
    page._set_mode("batch")
    app.processEvents()

    batch = page.batch_calculator
    batch._set_result_message("第一次结果")
    batch._set_result_message("第二次结果")
    tall_result = QWidget(batch._result)
    tall_result.setFixedHeight(1400)
    batch._result_layout.insertWidget(0, tall_result)
    batch.layout_changed.emit()

    bar = page.scroll.verticalScrollBar()
    before = bar.value()
    event = QWheelEvent(
        QPointF(10, 10), QPointF(10, 10), QPoint(), QPoint(0, -120),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    QApplication.sendEvent(page.scroll.viewport(), event)
    assert bar.value() == before

    clock = QElapsedTimer()
    clock.start()
    while not bar.isEnabled() and clock.elapsed() < 1500:
        QTest.qWait(10)
    assert bar.isEnabled()
    assert bar.maximum() > 0
    assert bar.value() > before

    page.close()
    page.deleteLater()
    app.processEvents()
