# 在生命周期专项中保留唯一 QApplication，避免反复销毁原生屏幕资源。
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent


_application = None


def application():
    global _application
    if _application is None:
        _application = QApplication.instance() or QApplication([])
    return _application


def dispose(widget):
    widget.close()
    widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    application().processEvents()
