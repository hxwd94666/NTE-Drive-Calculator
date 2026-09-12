# 初始化 Qt、异常处理和应用主窗口。
import sys
import threading
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from src.app.theme import apply_app_theme, install_dialog_defaults
from src.utils.logger import logger

# ── Entry
def _global_exception_handler(exc_type, exc_value, exc_tb):
    """全局异常处理，防止未捕获异常导致闪退"""
    import traceback as tb

    error_msg = "".join(tb.format_exception(exc_type, exc_value, exc_tb))
    logger.error(f"未捕获异常:\n{error_msg}")
    try:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(None, "程序异常", f"发生未捕获的异常:\n\n{error_msg[:1000]}")
    except Exception as exc:
        logger.error(f"显示全局异常弹窗失败: {exc}")


def run_gui(APP_CONTEXT, GLOBAL_THEME_SETTINGS, MainWindow, _ensure_admin):
    import faulthandler

    _ensure_admin()
    APP_CONTEXT.account.log_dir.mkdir(parents=True, exist_ok=True)
    _fault_log = open(
        str(APP_CONTEXT.account.log_dir / "crash_dump.log"),
        "w",
        encoding="utf-8",
    )
    faulthandler.enable(file=_fault_log)

    sys.excepthook = _global_exception_handler
    threading.excepthook = lambda args: logger.error(
        f"线程异常 [{args.thread}]: {args.exc_type.__name__}: {args.exc_value}"
    )
    if hasattr(Qt, "AA_DontUseNativeDialogs"):
        QApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    account_settings = APP_CONTEXT.account_settings
    legacy_theme = account_settings.legacy_theme_preference()
    account_settings.migrate_legacy_settings()
    account_settings.remove_legacy_theme_preference()
    apply_app_theme(
        app,
        GLOBAL_THEME_SETTINGS.load(legacy_theme=legacy_theme),
    )
    install_dialog_defaults(app)
    if APP_CONTEXT.paths.app_icon_path.exists():
        app.setWindowIcon(QIcon(str(APP_CONTEXT.paths.app_icon_path)))
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


