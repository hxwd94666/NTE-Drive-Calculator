# 初始化 Qt、异常处理和应用主窗口。
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
import sys
import threading
from PySide6.QtCore import Qt, QEventLoop, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication
from src.app.theme import apply_app_theme, install_dialog_defaults
from src.utils.logger import enable_crash_dump, logger

# ── Entry
def _global_exception_handler(exc_type, exc_value, exc_tb, *, text_catalog=None):
    """全局异常处理，防止未捕获异常导致闪退"""
    import traceback as tb

    error_msg = "".join(tb.format_exception(exc_type, exc_value, exc_tb))
    logger.error(f"未捕获异常:\n{error_msg}")
    try:
        from PySide6.QtWidgets import QMessageBox

        QMessageBox.critical(None, resolve_text_catalog(text_catalog).render('ui.startup.global_exception_handler.qmessagebox'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.startup.global_exception_handler.qmessagebox_2', value=f'{error_msg[:1000]}')))
    except Exception as exc:
        logger.error(f"显示全局异常弹窗失败: {exc}")


def prepare_process_dpi_awareness():
    """显式 GUI 启动保留旧鼠标库的系统 DPI 初始化时点，不覆盖既有感知模式。"""
    if sys.platform == "win32":
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except AttributeError:
            # 与旧依赖一致，仅兼容没有该 API 的系统；返回失败不强制切换模式。
            pass


def run_gui(app_context, global_theme_settings, window_factory, *, on_ready=None):
    """消费已经准备好的运行期依赖；Qt 初始化不再隐式创建默认账号。"""
    # 崩溃转储随 set_log_dir 迁移到新账号，切换后旧账号目录不再被本进程占用。
    enable_crash_dump(app_context.account.log_dir)

    sys.excepthook = _global_exception_handler
    threading.excepthook = lambda args: logger.error(
        f"线程异常 [{args.thread}]: {args.exc_type.__name__}: {args.exc_value}"
    )
    if hasattr(Qt, "AA_DontUseNativeDialogs"):
        QApplication.setAttribute(Qt.AA_DontUseNativeDialogs, True)
    # 启动参数保留给 UAC/主题重启，Qt 只接收自己的平台参数。
    qt_args = [arg for arg in sys.argv if arg not in {"--gui", "--ime-compat", "--admin-restarted"}]
    prepare_process_dpi_awareness()
    app = QApplication.instance() or QApplication(qt_args)
    app.setStyle("Fusion")
    account_settings = app_context.account_settings
    legacy_theme = account_settings.legacy_theme_preference()
    account_settings.migrate_legacy_settings()
    account_settings.remove_legacy_theme_preference()
    apply_app_theme(
        app,
        global_theme_settings.load(legacy_theme=legacy_theme),
    )
    install_dialog_defaults(app)
    if app_context.paths.app_icon_path.exists():
        app.setWindowIcon(QIcon(str(app_context.paths.app_icon_path)))
    w = window_factory()
    from functools import partial
    sys.excepthook = partial(_global_exception_handler, text_catalog=getattr(w, "text_catalog", None))
    w.show()
    if on_ready is not None:
        try:
            on_ready()
        except BaseException:
            retire_failed_startup_window(w)
            raise
    sys.exit(app.exec())



def retire_failed_startup_window(window):
    """持有外层数据租约，让本窗口既有异步 close 协议完成后才传播启动失败。"""
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(25)
    def finish_when_closed():
        if not window.isVisible():
            loop.quit()
    timer.timeout.connect(finish_when_closed)
    window.close()
    if window.isVisible():
        timer.start()
        try:
            # 不全局关窗或终止线程；窗口的 closeEvent 负责 owner 的物理退场。
            while window.isVisible():
                loop.exec()
        finally:
            timer.stop()
