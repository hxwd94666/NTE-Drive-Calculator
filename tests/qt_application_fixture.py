# 提供进程级唯一的 GUI 能力 Qt 应用实例，供测试复用，避免原生崩溃。
"""Process-wide Qt application fixture shared by tests."""

from __future__ import annotations

import os

from PySide6.QtWidgets import QApplication

_APPLICATION: QApplication | None = None


def ensure_qt_application() -> QApplication:
    """Return the single widget-capable application for this process.

    必须在任何依赖 Qt 的测试之前使用。这里持有模块级强引用，避免应用对象被垃圾
    回收后由其它模块重建，从而破坏 Qt 的原生屏幕资源；也避免创建非 GUI 的
    ``QCoreApplication`` 导致后续控件测试缺少 QGuiApplication 而致命退出。
    """

    global _APPLICATION
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    if _APPLICATION is None:
        existing = QApplication.instance()
        if isinstance(existing, QApplication):
            _APPLICATION = existing
        elif existing is not None:
            raise RuntimeError(
                "进程内已存在非 GUI 的 Qt 应用实例，无法升级为 QApplication；"
                "请改用 ensure_qt_application() 获取应用"
            )
        else:
            _APPLICATION = QApplication([])
    return _APPLICATION
