# 工坊公开模板的单请求网络读取：总时长、取消和响应体积有界，资源归本后台线程。
from __future__ import annotations

from collections.abc import Callable, Mapping
from concurrent.futures import CancelledError
import math


def fetch_workshop_payload(endpoint: str, *, timeout_seconds: float,
                           cancel_check: Callable[[], bool], headers: Mapping[str, str]) -> bytes:
    """调用者必须已有 Qt 应用；局部事件循环只运行于该次刷新后台线程。"""
    if cancel_check():
        raise CancelledError("工坊模板读取已取消")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("工坊模板总时限须为有限正数")
    # 没有请求时不引入 QtNetwork 或其 TLS 后端的初始化成本。
    from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer, Qt, QUrl
    from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

    if QCoreApplication.instance() is None:
        raise RuntimeError("工坊后台读取需要已初始化 Qt 应用")
    manager = QNetworkAccessManager()
    loop = QEventLoop()
    deadline, cancellation = QTimer(), QTimer()
    deadline.setSingleShot(True)
    deadline.setTimerType(Qt.PreciseTimer)
    cancellation.setInterval(50)
    cancellation.setTimerType(Qt.PreciseTimer)
    reply = None
    failure = None
    payload = bytearray()

    def stop(error):
        nonlocal failure
        if failure is None:
            failure = error
        if reply is not None:
            reply.abort()
        loop.quit()

    def collect():
        payload.extend(bytes(reply.readAll()))
        if len(payload) > 4 * 1024 * 1024:
            stop(RuntimeError("工坊模板响应超过 4 MiB 上限"))

    try:
        request = QNetworkRequest(QUrl(endpoint))
        for key, value in headers.items():
            request.setRawHeader(key.encode("ascii"), value.encode("utf-8"))
        deadline.timeout.connect(lambda: stop(TimeoutError("工坊模板请求超过总时限")))
        cancellation.timeout.connect(lambda: stop(CancelledError("工坊模板读取已取消")) if cancel_check() else None)
        deadline.start(max(1, math.ceil(timeout_seconds * 1000)))
        cancellation.start()
        reply = manager.get(request)
        reply.setReadBufferSize(64 * 1024)
        reply.readyRead.connect(collect)
        reply.finished.connect(loop.quit)
        if not reply.isFinished():
            loop.exec()
        if failure is not None:
            raise failure
        if cancel_check():
            raise CancelledError("工坊模板读取已取消")
        if reply.error() != QNetworkReply.NoError:
            raise RuntimeError("工坊模板网络读取失败：" + reply.errorString())
        collect()
        if failure is not None:
            raise failure
        return bytes(payload)
    finally:
        deadline.stop()
        cancellation.stop()
        if reply is not None:
            if not reply.isFinished():
                reply.abort()
            reply.deleteLater()
        manager.deleteLater()
        # DeferredDelete 在创建网络对象的线程完成，不把 owner 转交给 GUI 或全局列表。
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
