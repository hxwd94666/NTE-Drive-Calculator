# 从 MainWindow 抽离的更新控制器方法。
"""Compatibility-installed MirrorChyan update controller."""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QMessageBox, QProgressDialog, QVBoxLayout

from src.i18n import tr
from src.app.constants import (
    APP_VERSION,
    BILIBILI_HOME_URL,
    GROUP_CHAT_NOTICE,
    GITHUB_HOME_URL,
    GITHUB_LATEST_RELEASE_URL,
    GITHUB_RELEASES_URL,
    MIRROR_PROJECT_URL,
    MIRROR_UPDATE_API,
    SUPPORT_US_URL,
)
from src.app.workers import WorkerThread
from src.observability.context import OperationContext
from src.observability.operation import log_event
from src.features.settings.updates import (
    download_update_installer,
    fetch_update_info,
    is_newer_version,
    should_show_startup_update,
    show_update_dialog,
)
from src.utils.logger import logger


def _new_update_operation(
    self: Any,
    *,
    feature: str = "update",
) -> OperationContext:
    app_context = getattr(self, "app_context", None)
    return OperationContext.create(
        feature,
        account_id=(
            app_context.account.active_account_id
            if app_context is not None
            else None
        ),
        context_generation=(
            app_context.generation if app_context is not None else None
        ),
    )


class _MirrorInstallerDownloadWorker(WorkerThread):
    progress = Signal(int, int)

    def __init__(self, url: str, parent: QObject | None = None) -> None:
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
    update_worker = getattr(self, "_update_worker", None)
    if update_worker is not None and update_worker.isRunning():
        if manual:
            self._update_status.setText(tr("正在检查更新…"))
        return
    self._update_check_manual = manual
    self._update_operation_context = _new_update_operation(self)
    log_event(
        "INFO",
        "update.check_started",
        "开始检查更新",
        self._update_operation_context,
        trigger="manual" if manual else "startup",
        current_version=APP_VERSION,
    )
    if manual:
        self._check_update_btn.setEnabled(False)
        self._update_status.setText(tr("正在通过 Mirror 酱检查更新…"))
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
    operation = getattr(
        self, "_update_operation_context", _new_update_operation(self)
    )
    log_event(
        "INFO",
        "update.check_succeeded",
        "更新检查完成",
        operation,
        trigger="manual" if manual else "startup",
        has_release=bool(info.get("has_release")),
        newer=bool(info.get("newer")),
        latest_version=info.get("latest"),
    )
    if manual:
        self._check_update_btn.setEnabled(True)
    if not info.get("has_release"):
        message = info.get("message") or tr("Mirror 酱未返回可用更新信息。")
        self._update_status.setText(message)
        if manual:
            self._show_update_failure_netdisk_prompt(info.get("error", message))
        return
    latest = info.get("latest") or tr("未知")
    if info.get("newer"):
        self._update_status.setText(
            tr("发现新版本: {latest}（当前 {current}）", latest=latest, current=APP_VERSION)
        )
        if manual or self._should_show_startup_update(info):
            self._show_update_dialog(info, manual=manual)
    else:
        self._update_status.setText(tr("当前已是最新版本: {version}", version=APP_VERSION))
        if manual:
            QMessageBox.information(
                self, tr("检查更新"),
                tr("当前已是最新版本。\n当前版本: {current}\n最新版本: {latest}",
                   current=APP_VERSION, latest=latest),
            )


def _on_update_error(self, err):
    manual = getattr(self, "_update_check_manual", True)
    operation = getattr(
        self, "_update_operation_context", _new_update_operation(self)
    )
    log_event(
        "ERROR",
        "update.check_failed",
        "更新检查失败",
        operation,
        trigger="manual" if manual else "startup",
        error=err,
    )
    message = tr("Mirror 酱更新服务请求失败，请稍后重试。")
    if manual:
        self._check_update_btn.setEnabled(True)
        self._update_status.setText(message)
        self._show_update_failure_netdisk_prompt(err)
    else:
        if hasattr(self, "_update_status"):
            self._update_status.setText(message)
        logger.warning("启动自动检查更新失败: {}", err)


def _start_mirror_download(self):
    download_worker = getattr(self, "_mirror_download_worker", None)
    if download_worker is not None and download_worker.isRunning():
        return
    cdk = self._save_mirror_cdk()
    if not cdk:
        _show_mirror_cdk_required_dialog(self)
        editor = getattr(self, "_mirror_cdk_edit", None)
        if editor is not None:
            editor.setFocus()
        return
    self._mirror_download_operation_context = _new_update_operation(
        self, feature="update_download"
    )
    log_event(
        "INFO",
        "update.download_request_started",
        "开始请求 Mirror 下载",
        self._mirror_download_operation_context,
        cdk_present=True,
    )
    self._mirror_download_btn.setEnabled(False)
    self._update_status.setText(tr("正在向 Mirror 酱请求下载地址…"))
    self._mirror_download_worker = WorkerThread(
        target=lambda: self._fetch_update_info(cdk), parent=self,
    )
    self._mirror_download_worker.result_ready.connect(self._on_mirror_download_ready)
    self._mirror_download_worker.error.connect(
        lambda error: self._on_mirror_download_ready({"error": str(error)})
    )
    self._mirror_download_worker.start()


def _on_mirror_download_ready(self, info):
    operation = getattr(
        self,
        "_mirror_download_operation_context",
        _new_update_operation(self, feature="update_download"),
    )
    url = str(info.get("url") or "").strip()
    if url:
        latest = str(info.get("latest") or "").strip()
        if not _mirror_download_version_is_available(latest, APP_VERSION):
            log_event(
                "WARNING",
                "update.download_historical_version_blocked",
                "Mirror 返回的版本低于当前版本",
                operation,
                current_version=APP_VERSION,
                latest_version=latest,
            )
            if hasattr(self, "_mirror_download_btn"):
                self._mirror_download_btn.setEnabled(True)
            self._update_status.setText(tr("当前已是最新版本，无法下载历史旧版本。"))
            QMessageBox.information(
                self,
                tr("Mirror 下载"),
                tr("当前版本高于 Mirror 可下载版本，已是最新版本，无法下载历史旧版本。"),
            )
            return
        log_event(
            "INFO",
            "update.download_url_received",
            "已获取 Mirror 下载地址",
            operation,
            has_download_url=True,
        )
        self._start_mirror_installer_download(url)
        return
    log_event(
        "ERROR",
        "update.download_request_failed",
        "未获取到 Mirror 下载地址",
        operation,
        error=info.get("message") or info.get("error"),
    )
    if hasattr(self, "_mirror_download_btn"):
        self._mirror_download_btn.setEnabled(True)
    self._update_status.setText(tr("未获取到 Mirror 下载地址，可前往项目页面尝试下载。"))
    _show_mirror_project_download_dialog(
        self,
        "未获取到 Mirror 下载地址。请确认 CDK 有效，且存在可下载的新版本；"
        "若仍无法下载，可前往下方项目页面尝试下载。",
    )


def _start_mirror_installer_download(self, url):
    """Download and launch the installer without sending the user to a browser."""
    self._update_status.setText(tr("正在通过 Mirror 酱下载更新安装程序…"))
    progress = QProgressDialog("正在下载更新安装程序…", "取消", 0, 0, self)
    progress.setWindowTitle(tr("Mirror 下载"))
    progress.setWindowModality(Qt.WindowModality.WindowModal)
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
        progress.setLabelText(
            tr("正在下载更新安装程序… {done:.1f} / {total:.1f} MB",
               done=downloaded / 1024 / 1024, total=total / 1024 / 1024)
        )
    else:
        progress.setRange(0, 0)
        progress.setLabelText(
            tr("正在下载更新安装程序… {done:.1f} MB", done=downloaded / 1024 / 1024)
        )


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
    operation = getattr(
        self,
        "_mirror_download_operation_context",
        _new_update_operation(self, feature="update_download"),
    )
    path = Path(str((result or {}).get("path") or ""))
    if not path.is_file():
        self._on_mirror_installer_download_error("安装程序下载完成后未找到文件。")
        return
    log_event(
        "INFO",
        "update.download_succeeded",
        "Mirror 安装程序下载完成",
        operation,
        installer_name=path.name,
    )
    self._update_status.setText(tr("安装程序下载完成，正在自动启动…"))
    QTimer.singleShot(150, lambda: self._launch_mirror_installer(str(path)))


def _on_mirror_installer_download_error(self, error):
    self._finish_mirror_download_ui()
    message = str(error or "安装程序下载失败，请稍后重试。")
    operation = getattr(
        self,
        "_mirror_download_operation_context",
        _new_update_operation(self, feature="update_download"),
    )
    if "已取消更新下载安装包" in message:
        log_event(
            "WARNING",
            "update.download_cancelled",
            "用户取消 Mirror 下载",
            operation,
        )
        self._update_status.setText(tr("已取消 Mirror 下载。"))
        return
    log_event(
        "ERROR",
        "update.download_failed",
        "Mirror 安装程序下载失败",
        operation,
        error=message,
    )
    self._update_status.setText(tr("Mirror 下载失败，可前往项目页面尝试下载。"))
    _show_mirror_project_download_dialog(
        self,
        "下载或启动安装程序失败，请稍后重试；若仍失败，可前往下方项目页面尝试下载。",
    )


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
    self._update_status.setText(tr("安装程序已启动，当前程序即将退出。"))
    operation = getattr(
        self,
        "_mirror_download_operation_context",
        _new_update_operation(self, feature="update_download"),
    )
    log_event(
        "INFO",
        "update.installer_launched",
        "Mirror 更新安装程序已启动",
        operation,
        installer_name=installer.name,
    )
    application = QApplication.instance()
    if application is not None:
        QTimer.singleShot(250, application.quit)


def _show_mirror_cdk_required_dialog(self):
    dialog = QDialog(self)
    dialog.setWindowTitle(tr("Mirror 下载"))
    dialog.setMinimumWidth(460)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    message = QLabel(
        tr("请先填写 Mirror CDK 后再下载。<br><br>") + _mirror_project_link_text(tr("获取 CDK"))
    )
    message.setWordWrap(True)
    message.setTextFormat(Qt.TextFormat.RichText)
    message.setOpenExternalLinks(True)
    message.setTextInteractionFlags(
        Qt.TextInteractionFlag.TextBrowserInteraction
    )
    layout.addWidget(message)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()


def _mirror_project_link_text(action: str) -> str:
    """Return the visible, clickable Mirror project link used by download dialogs."""
    return (
        f"可前往 Mirror 项目页面{action}："
        f'<a href="{MIRROR_PROJECT_URL}">{MIRROR_PROJECT_URL}</a>'
    )


def _mirror_download_version_is_available(latest: str, current: str) -> bool:
    """Allow the current release or a newer release, never a historical one."""
    return bool(latest) and not is_newer_version(current, latest)


def _show_mirror_project_download_dialog(self: Any, summary: str) -> None:
    """Show a download failure with a direct, browser-openable Mirror link."""
    dialog = QDialog(self)
    dialog.setWindowTitle(tr("Mirror 下载"))
    dialog.setMinimumWidth(460)
    if hasattr(self, "_current_style_sheet"):
        dialog.setStyleSheet(self._current_style_sheet())
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    message = QLabel(summary)
    message.setWordWrap(True)
    layout.addWidget(message)
    link = QLabel(_mirror_project_link_text(tr("尝试下载")))
    link.setWordWrap(True)
    link.setTextFormat(Qt.TextFormat.RichText)
    link.setOpenExternalLinks(True)
    link.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    layout.addWidget(link)
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
    box.setWindowTitle(tr("检查更新失败"))
    box.setText(tr("Mirror 酱更新服务请求失败，请稍后重试。"))
    if detail:
        box.setInformativeText(str(detail))
    box.addButton(tr("确定"), QMessageBox.AcceptRole)
    box.exec()


def _open_update_homepage(self):
    self._open_url(GITHUB_LATEST_RELEASE_URL)


def _open_bilibili_homepage(self):
    self._open_url(BILIBILI_HOME_URL)


def _open_project_homepage(self):
    self._open_url(GITHUB_HOME_URL)


def _open_support_homepage(self):
    self._open_url(SUPPORT_US_URL)


def _show_group_chat_notice(self):
    QMessageBox.information(self, tr("加入群聊"), GROUP_CHAT_NOTICE)


def _show_netdisk_download_dialog(self, links):
    links = tuple((str(name), str(url)) for name, url in links if name and url)
    if not links:
        return
    box = QMessageBox(self)
    box.setWindowTitle(tr("网盘下载"))
    box.setText(tr("请选择下载网盘"))
    box.setInformativeText("\n\n".join(f"{name}：\n{url}" for name, url in links))
    box.setMinimumSize(620, 300)
    box.setStyleSheet(box.styleSheet() + "\nQLabel{min-width:560px;}")
    buttons = [(box.addButton(f"打开{name}", QMessageBox.AcceptRole), url) for name, url in links]
    box.addButton(tr("取消"), QMessageBox.RejectRole)
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


class UpdateControllerMixin:
    _maybe_check_updates_on_startup = _maybe_check_updates_on_startup
    _check_updates = _check_updates
    _fetch_update_info = _fetch_update_info
    _on_update_checked = _on_update_checked
    _on_update_error = _on_update_error
    _should_show_startup_update = _should_show_startup_update
    _show_update_dialog = _show_update_dialog
    _show_update_failure_netdisk_prompt = _show_update_failure_netdisk_prompt
    _open_update_homepage = _open_update_homepage
    _open_bilibili_homepage = _open_bilibili_homepage
    _open_project_homepage = _open_project_homepage
    _open_support_homepage = _open_support_homepage
    _show_group_chat_notice = _show_group_chat_notice
    _show_netdisk_download_dialog = _show_netdisk_download_dialog
    _open_url = _open_url
    _is_newer_version = _is_newer_version
    _mirror_cdk_value = _mirror_cdk_value
    _save_mirror_cdk = _save_mirror_cdk
    _start_mirror_download = _start_mirror_download
    _on_mirror_download_ready = _on_mirror_download_ready
    _start_mirror_installer_download = _start_mirror_installer_download
    _on_mirror_download_progress = _on_mirror_download_progress
    _on_mirror_installer_downloaded = _on_mirror_installer_downloaded
    _on_mirror_installer_download_error = _on_mirror_installer_download_error
    _finish_mirror_download_ui = _finish_mirror_download_ui
    _launch_mirror_installer = _launch_mirror_installer
