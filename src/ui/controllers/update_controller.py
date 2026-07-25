# 从 MainWindow 抽离的更新控制器方法。
"""Compatibility-installed MirrorChyan update controller."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QMessageBox, QProgressDialog, QVBoxLayout

from src.app.constants import (
    APP_VERSION,
    BILIBILI_HOME_URL,
    GITHUB_HOME_URL,
    GITHUB_RELEASES_URL,
    MIRROR_UPDATE_API,
)
from src.app.workers import WorkerThread
from src.features.settings.updates import (
    download_update_installer,
    fetch_update_info,
    is_newer_version,
    should_show_startup_update,
    show_update_dialog,
)
from src.ui.main_window_method_install import install_methods as _install_main_window_methods
from src.utils.logger import logger

_METHOD_NAMES = [
    "_maybe_check_updates_on_startup", "_check_updates", "_fetch_update_info",
    "_on_update_checked", "_on_update_error", "_should_show_startup_update",
    "_show_update_dialog", "_show_update_failure_netdisk_prompt", "_open_update_homepage",
    "_open_bilibili_homepage", "_show_netdisk_download_dialog", "_open_url",
    "_is_newer_version", "_mirror_cdk_value", "_save_mirror_cdk",
    "_start_mirror_download", "_on_mirror_download_ready", "_start_mirror_installer_download",
    "_on_mirror_download_progress", "_on_mirror_installer_downloaded", "_on_mirror_installer_download_error",
    "_finish_mirror_download_ui", "_launch_mirror_installer",
]


class _MirrorInstallerDownloadWorker(WorkerThread):
    progress = Signal(int, int)

    def __init__(self, url: str, parent=None):
        self._url = url
        self._cancel_event = threading.Event()
        super().__init__(target=self._download, parent=parent)

    def cancel(self) -> None:
        self._cancel_event.set()

    def _download(self):
        return download_update_installer(
            self._url,
            progress_callback=lambda current, total: self.progress.emit(current, total),
            cancel_check=self._cancel_event.is_set,
        )


def install_methods(app_module, window_cls) -> None:
    _install_main_window_methods(app_module, window_cls, _METHOD_NAMES, globals())


def _maybe_check_updates_on_startup(self):
    if self._update_config.get("never_remind"):
        return
    QTimer.singleShot(1200, lambda: self._check_updates(manual=False))


def _mirror_cdk_value(self):
    editor = getattr(self, "_mirror_cdk_edit", None)
    if editor is not None:
        return editor.text().strip()
    return str(self._update_config.get("mirror_cdk") or "").strip()


def _save_mirror_cdk(self):
    cdk = self._mirror_cdk_value()
    if self._update_config.get("mirror_cdk") != cdk:
        self._update_config["mirror_cdk"] = cdk
        self._save_update_config()
    return cdk


def _check_updates(self, manual=True):
    if hasattr(self, "_update_worker") and self._update_worker.isRunning():
        if manual:
            self._update_status.setText("正在检查更新…")
        return
    self._update_check_manual = manual
    if manual:
        self._check_update_btn.setEnabled(False)
        self._update_status.setText("正在通过 Mirror 酱检查更新…")
    self._update_worker = WorkerThread(
        target=self._fetch_update_info, parent=self,
    )
    self._update_worker.result_ready.connect(self._on_update_checked)
    self._update_worker.error.connect(self._on_update_error)
    self._update_worker.start()


def _fetch_update_info(self, cdk=""):
    info = fetch_update_info(MIRROR_UPDATE_API, APP_VERSION, cdk=cdk)
    if info.get("has_release"):
        info["release_url"] = GITHUB_RELEASES_URL
    return info


def _on_update_checked(self, info):
    manual = getattr(self, "_update_check_manual", True)
    if manual:
        self._check_update_btn.setEnabled(True)
    if not info.get("has_release"):
        message = info.get("message") or "Mirror 酱未返回可用更新信息。"
        self._update_status.setText(message)
        if manual:
            self._show_update_failure_netdisk_prompt(info.get("error", message))
        return
    latest = info.get("latest") or "未知"
    if info.get("newer"):
        self._update_status.setText(f"发现新版本: {latest}（当前 {APP_VERSION}）")
        if manual or self._should_show_startup_update(info):
            self._show_update_dialog(info, manual=manual)
    else:
        self._update_status.setText(f"当前已是最新版本: {APP_VERSION}")
        if manual:
            QMessageBox.information(
                self, "检查更新",
                f"当前已是最新版本。\n当前版本: {APP_VERSION}\n最新版本: {latest}",
            )


def _on_update_error(self, err):
    manual = getattr(self, "_update_check_manual", True)
    message = "Mirror 酱更新服务请求失败，请稍后重试。"
    if manual:
        self._check_update_btn.setEnabled(True)
        self._update_status.setText(message)
        self._show_update_failure_netdisk_prompt(err)
    else:
        if hasattr(self, "_update_status"):
            self._update_status.setText(message)
        logger.warning("启动自动检查更新失败: {}", err)


def _start_mirror_download(self):
    if hasattr(self, "_mirror_download_worker") and self._mirror_download_worker.isRunning():
        return
    cdk = self._save_mirror_cdk()
    if not cdk:
        _show_mirror_cdk_required_dialog(self)
        editor = getattr(self, "_mirror_cdk_edit", None)
        if editor is not None:
            editor.setFocus()
        return
    self._mirror_download_btn.setEnabled(False)
    self._update_status.setText("正在向 Mirror 酱请求下载地址…")
    self._mirror_download_worker = WorkerThread(
        target=lambda: self._fetch_update_info(cdk), parent=self,
    )
    self._mirror_download_worker.result_ready.connect(self._on_mirror_download_ready)
    self._mirror_download_worker.error.connect(
        lambda error: self._on_mirror_download_ready({"error": str(error)})
    )
    self._mirror_download_worker.start()


def _on_mirror_download_ready(self, info):
    url = str(info.get("url") or "").strip()
    if url:
        self._start_mirror_installer_download(url)
        return
    if hasattr(self, "_mirror_download_btn"):
        self._mirror_download_btn.setEnabled(True)
    self._update_status.setText("未获取到 Mirror 下载地址。")
    detail = info.get("message") or info.get("error") or "请确认 CDK 有效，且存在可下载的新版本。"
    QMessageBox.information(
        self, "Mirror 下载",
        "未获取到下载地址。请确认 CDK 有效，且存在可下载的新版本。\n\n" + str(detail),
    )


def _start_mirror_installer_download(self, url):
    """Download and launch the installer without sending the user to a browser."""
    self._update_status.setText("正在通过 Mirror 酱下载更新安装程序…")
    progress = QProgressDialog("正在下载更新安装程序…", "取消", 0, 0, self)
    progress.setWindowTitle("Mirror 下载")
    progress.setWindowModality(Qt.WindowModal)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.setMinimumDuration(0)
    progress.show()
    self._mirror_download_progress_dialog = progress
    worker = _MirrorInstallerDownloadWorker(str(url), parent=self)
    self._mirror_installer_download_worker = worker
    progress.canceled.connect(worker.cancel)
    worker.progress.connect(self._on_mirror_download_progress)
    worker.result_ready.connect(self._on_mirror_installer_downloaded)
    worker.error.connect(self._on_mirror_installer_download_error)
    worker.start()


def _on_mirror_download_progress(self, downloaded, total):
    progress = getattr(self, "_mirror_download_progress_dialog", None)
    if progress is None:
        return
    if total > 0:
        progress.setRange(0, total)
        progress.setValue(min(downloaded, total))
        progress.setLabelText(f"正在下载更新安装程序… {downloaded / 1024 / 1024:.1f} / {total / 1024 / 1024:.1f} MB")
    else:
        progress.setRange(0, 0)
        progress.setLabelText(f"正在下载更新安装程序… {downloaded / 1024 / 1024:.1f} MB")


def _finish_mirror_download_ui(self):
    progress = getattr(self, "_mirror_download_progress_dialog", None)
    if progress is not None:
        progress.close()
        progress.deleteLater()
    self._mirror_download_progress_dialog = None
    if hasattr(self, "_mirror_download_btn"):
        self._mirror_download_btn.setEnabled(True)


def _on_mirror_installer_downloaded(self, result):
    self._finish_mirror_download_ui()
    path = Path(str((result or {}).get("path") or ""))
    if not path.is_file():
        self._on_mirror_installer_download_error("安装程序下载完成后未找到文件。")
        return
    self._update_status.setText("安装程序下载完成，正在自动启动…")
    QTimer.singleShot(150, lambda: self._launch_mirror_installer(str(path)))


def _on_mirror_installer_download_error(self, error):
    self._finish_mirror_download_ui()
    message = str(error or "安装程序下载失败，请稍后重试。")
    if "已取消更新下载安装包" in message:
        self._update_status.setText("已取消 Mirror 下载。")
        return
    self._update_status.setText("Mirror 下载失败。")
    QMessageBox.warning(self, "Mirror 下载", "下载或启动安装程序失败，请稍后重试。\n\n" + message)


def _launch_mirror_installer(self, path):
    installer = Path(path)
    if not installer.is_file():
        self._on_mirror_installer_download_error("安装程序文件不存在。")
        return
    try:
        subprocess.Popen([str(installer)], cwd=str(installer.parent))
    except OSError as exc:
        self._on_mirror_installer_download_error(str(exc))
        return
    self._update_status.setText("安装程序已启动，当前程序即将退出。")
    logger.info("Mirror 更新安装程序已启动: {}", installer)
    application = QApplication.instance()
    if application is not None:
        QTimer.singleShot(250, application.quit)


def _show_mirror_cdk_required_dialog(self):
    dialog = QDialog(self)
    dialog.setWindowTitle("Mirror 下载")
    dialog.setMinimumWidth(460)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    message = QLabel(
        "请先填写 Mirror CDK 后再下载。<br><br>"
        "可前往 Mirror 酱获取 CDK："
        '<a href="https://mirrorchyan.com/zh/projects?rid=NTE-Drive-Calc&amp;channel=stable">'
        "https://mirrorchyan.com/zh/projects?rid=NTE-Drive-Calc&amp;channel=stable</a>"
    )
    message.setWordWrap(True)
    message.setTextFormat(Qt.RichText)
    message.setOpenExternalLinks(True)
    message.setTextInteractionFlags(Qt.TextBrowserInteraction)
    layout.addWidget(message)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()


def _should_show_startup_update(self, info):
    return should_show_startup_update(self._update_config, info)


def _show_update_dialog(self, info, manual=False):
    result = show_update_dialog(self, self._current_style_sheet(), info, APP_VERSION)
    if result.get("never_remind"):
        self._update_config["never_remind"] = True
    if result.get("ignored_version"):
        self._update_config["ignored_version"] = result["ignored_version"]
    if result.get("changed"):
        self._save_update_config()


def _show_update_failure_netdisk_prompt(self, detail=""):
    box = QMessageBox(self)
    box.setWindowTitle("检查更新失败")
    box.setText("Mirror 酱更新服务请求失败，请稍后重试。")
    if detail:
        box.setInformativeText(str(detail))
    box.addButton("确定", QMessageBox.AcceptRole)
    box.exec()


def _open_update_homepage(self):
    self._open_url(GITHUB_HOME_URL)


def _open_bilibili_homepage(self):
    self._open_url(BILIBILI_HOME_URL)


def _show_netdisk_download_dialog(self, links):
    links = tuple((str(name), str(url)) for name, url in links if name and url)
    if not links:
        return
    box = QMessageBox(self)
    box.setWindowTitle("网盘下载")
    box.setText("请选择下载网盘")
    box.setInformativeText("\n\n".join(f"{name}：\n{url}" for name, url in links))
    box.setMinimumSize(620, 300)
    box.setStyleSheet(box.styleSheet() + "\nQLabel{min-width:560px;}")
    buttons = [(box.addButton(f"打开{name}", QMessageBox.AcceptRole), url) for name, url in links]
    box.addButton("取消", QMessageBox.RejectRole)
    box.exec()
    for button, url in buttons:
        if box.clickedButton() is button:
            self._open_url(url)
            break


def _open_url(self, url):
    try:
        os.startfile(url)
    except Exception:
        import webbrowser
        webbrowser.open(url)


def _is_newer_version(self, remote, current):
    return is_newer_version(remote, current)
