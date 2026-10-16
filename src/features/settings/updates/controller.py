# 独占 Calc 更新的请求、取消、账号代次、物理退场与单次安装器派发。
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from src.app.constants import APP_VERSION, GITHUB_RELEASES_URL, MIRROR_UPDATE_API
from src.integrations.updates.mirror import (
    InstallerReceipt, UpdateDownloadCancelled, discard_installer, download_update_installer,
    fetch_update_info, is_newer_version, launch_installer,
)
from src.observability import OperationContext, log_event
from . import dialogs


@dataclass(eq=False)
class _Request:
    identity: tuple
    kind: str
    manual: bool = True
    cancelled: Event = field(default_factory=Event)
    dispatched: bool = False
    operation: OperationContext = field(init=False)

    def __post_init__(self):
        self.operation = OperationContext.create("update", account_id=self.identity[0],
                                                 context_generation=self.identity[1])


class _UpdateWorker(QThread):
    progress = Signal(object, object)

    def __init__(self, request, target, parent):
        super().__init__(parent)
        self.request, self.target = request, target
        self.result = self.error = None

    def run(self):
        try:
            self.result = self.target(self)
        except UpdateDownloadCancelled:
            self.request.cancelled.set()
        except Exception as error:
            # urllib 错误及 traceback 可能含短期地址：不交给通用 WorkerThread 原文日志。
            self.error = f"更新操作失败（{type(error).__name__}），请稍后重试。"


class CalcUpdateController(QObject):
    """UI 线程拥有状态；worker 只消费冻结参数，结果必须等物理 finished 后交付。"""

    def __init__(self, *, identity_provider, load_settings, save_settings, style_sheet,
                 show_group, show_netdisk, request_close, parent, update_channel="installer"):
        super().__init__(parent)
        if update_channel not in {"installer", "manual", "platform"}:
            raise ValueError("unsupported distribution update channel")
        # 发行身份由组合根冻结注入，不根据启动进程、目录或下载 URL 猜测。
        self._update_channel = update_channel
        self._identity = identity_provider
        self._load, self._save = load_settings, save_settings
        self._style, self._show_group, self._show_netdisk = style_sheet, show_group, show_netdisk
        self._request_close = request_close
        self._closed = False
        self._workers = set()
        self._check = self._download = None
        self._view = self._progress_dialog = None
        self._pending_launch = self._pending_prompt = None
        self._startup_identity = None
        self._startup_timer = self._timer(self._startup_ready, 1200)
        self._prompt_timer = self._timer(self._show_pending, 1200)
        self._launch_timer = self._timer(self._dispatch, 150)

    def _timer(self, callback, interval):
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(interval)
        timer.timeout.connect(callback)
        return timer

    def bind_view(self, *, status, check_button, download_button, cdk_editor):
        self._view = status, check_button, download_button, cdk_editor
        self.rebuild_account()

    def configure_distribution_links(self, *, homepage_button, netdisk_button):
        if self._update_channel == "installer":
            return
        netdisk_button.setEnabled(False)
        netdisk_button.setToolTip("此入口未声明便携 ZIP，不作为便携更新渠道。")
        if self._update_channel == "platform":
            homepage_button.setEnabled(False)
            homepage_button.setToolTip("请使用分发平台的更新或修复入口。")
        else:
            homepage_button.setText("发布页（选择便携 ZIP）")

    def _status(self, text):
        if self._view is not None:
            self._view[0].setText(text)

    def _buttons(self):
        if self._view is not None:
            self._view[1].setEnabled(not self._closed and self._check is None)
            self._view[1].setText("检查更新" if self._update_channel == "installer" else "更新说明")
            self._view[2].setEnabled(self._update_channel == "installer" and not self._closed and self._download is None)
            self._view[2].setToolTip("Mirror 当前仅提供安装器，便携版不使用此下载入口。" if self._update_channel != "installer" else "")
            self._view[3].setEnabled(self._update_channel == "installer" and not self._closed)

    def _portable_status(self):
        if self._update_channel == "platform":
            return (f"当前版本: {APP_VERSION}（平台托管便携版）\n"
                    "请在分发平台更新或修复，并先退出本程序；此处不检查或下载竞争更新。\n"
                    "Mirror 当前仅提供安装器，不适用于便携版。")
        return (f"当前版本: {APP_VERSION}（手动便携版）\n"
                "请从可信发布渠道获取对应便携 ZIP，退出程序后完整覆盖，保留 userdata。\n"
                "若渠道仅提供安装器，请等待便携包；Mirror 当前仅提供安装器，不作为便携更新。")

    def _current(self, request):
        return (not self._closed and not request.cancelled.is_set()
                and request.identity == self._identity()
                and (request is self._check or request is self._download))

    def _log(self, request, event, message, **fields):
        log_event("INFO", event, message, request.operation, **fields)

    def _spawn(self, request, phase, target):
        worker = _UpdateWorker(request, target, self)
        self._workers.add(worker)
        worker.progress.connect(lambda current, total: self._progress(request, current, total))
        worker.finished.connect(lambda: self._finished(worker, phase))
        worker.start()

    def is_running(self):
        # finished 槽尚未消费的终态也须保留：窗口不能提前析构仍被 Qt 回调引用的 owner。
        return bool(self._workers)

    def startup(self):
        if self._update_channel != "installer":
            self._status(self._portable_status())
            return
        if not self._closed and not self._load().get("never_remind"):
            self._startup_identity = self._identity()
            self._startup_timer.start()

    def _startup_ready(self):
        if self._closed or self._startup_identity != self._identity():
            return
        if QApplication.activeModalWidget() is not None:
            self._startup_timer.start()
        else:
            self.check(manual=False)

    def check(self, _checked=False, *, manual=True):
        if self._closed or self._check is not None:
            return
        if self._update_channel != "installer":
            self._status(self._portable_status())
            return
        request = self._check = _Request(self._identity(), "check", manual)
        self._buttons()
        self._status("正在通过 Mirror 酱检查更新…")
        self._log(request, "update.check_started", "开始检查更新", trigger="manual" if manual else "startup")
        self._spawn(request, "check", lambda _worker: fetch_update_info(
            MIRROR_UPDATE_API, APP_VERSION, cancel_check=request.cancelled.is_set))

    def save_cdk(self):
        if self._closed or self._view is None or self._update_channel != "installer":
            return ""
        settings = dict(self._load())
        cdk = self._view[3].text().strip()
        if cdk != str(settings.get("mirror_cdk") or ""):
            settings["mirror_cdk"] = cdk
            self._save(settings)
        return cdk

    def start_download(self, _checked=False):
        if self._closed or self._download is not None:
            return
        if self._update_channel != "installer":
            self._status(self._portable_status())
            return
        cdk = self.save_cdk() if self._view else str(self._load().get("mirror_cdk") or "").strip()
        if not cdk:
            dialogs._show_mirror_cdk_required_dialog(self.parent())
            return
        request = self._download = _Request(self._identity(), "download")
        self._buttons()
        self._status("正在向 Mirror 酱请求下载地址…")
        self._log(request, "update.download_request_started", "开始请求更新下载地址")
        self._spawn(request, "address", lambda _worker: fetch_update_info(
            MIRROR_UPDATE_API, APP_VERSION, cdk=cdk, cancel_check=request.cancelled.is_set))

    def _finished(self, worker, phase):
        self._workers.discard(worker)
        request, result, error = worker.request, worker.result, worker.error
        worker.deleteLater()
        if not self._current(request):
            if isinstance(result, InstallerReceipt):
                discard_installer(result)
            if request is self._download:
                self._download = None
                self._close_progress()
                if request.identity == self._identity() and not self._closed:
                    self._status("已取消 Mirror 下载。")
            if request is self._check:
                self._check = None
            self._buttons()
            return
        if error:
            self._fail(request, error)
        elif phase == "check":
            self._checked(request, result)
        elif phase == "address":
            self._address_ready(request, result)
        elif phase == "dispatch":
            self._status("安装程序已启动，正在关闭当前程序。")
            self._log(request, "update.installer_launched", "更新安装程序已启动")
            self.begin_close()
            self._request_close()
        else:
            self._close_progress()
            self._pending_launch = request, result
            self._status("安装程序下载完成，正在复核并启动…")
            self._launch_timer.start()

    def _checked(self, request, info):
        if not info or not info.get("has_release"):
            self._fail(request, "Mirror 酱更新服务请求失败，请稍后重试。")
            return
        self._log(request, "update.check_succeeded", "更新检查完成", newer=bool(info.get("newer")))
        if not info.get("newer"):
            self._status(f"当前已是最新版本: {APP_VERSION}")
            if request.manual:
                QMessageBox.information(self.parent(), "检查更新", f"当前已是最新版本。\n当前版本: {APP_VERSION}")
            self._end(request)
            return
        self._status(f"发现新版本: {info['latest']}（当前 {APP_VERSION}）")
        if request.manual or dialogs.should_show_startup_update(self._load(), info):
            info = dict(info, release_url=GITHUB_RELEASES_URL)
            self._pending_prompt = request, info
            self._show_pending()
        else:
            self._end(request)

    def _show_pending(self):
        pending = self._pending_prompt
        if pending is None:
            return
        request, info = pending
        if not self._current(request):
            self._pending_prompt = None
            return
        if not request.manual and QApplication.activeModalWidget() is not None:
            self._prompt_timer.start()
            return
        self._pending_prompt = None
        values = dict(self._load())
        result = dialogs.show_update_dialog(self.parent(), self._style(), info, APP_VERSION,
            show_group=self._show_group, show_netdisk=self._show_netdisk,
            start_download=lambda: self.start_download() if self._current(request) else None)
        # 弹窗运行嵌套事件循环；忽略版本/提醒偏好不得写进新账号，ABA 也已失效。
        if self._current(request) and result.get("changed"):
            for key in ("never_remind", "ignored_version"):
                if key in result:
                    values[key] = result[key]
            self._save(values)
        self._end(request)

    def _address_ready(self, request, info):
        if self._update_channel != "installer":
            self._status(self._portable_status())
            self._end(request)
            return
        latest, url = str((info or {}).get("latest") or ""), str((info or {}).get("url") or "")
        if not latest or not url:
            self._fail(request, "未获取到下载地址，请确认 CDK 有效后重试。")
            return
        if is_newer_version(APP_VERSION, latest):
            accepted = dialogs._confirm_mirror_older_version_download(self.parent(), APP_VERSION, latest)
            if not self._current(request):
                return
            if not accepted:
                self._status("已取消较旧版本下载。")
                self._end(request)
                return
        progress = QProgressDialog("正在下载更新安装程序…", "取消", 0, 0, self.parent())
        progress.setWindowTitle("Mirror 下载")
        progress.setWindowModality(Qt.WindowModal)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.setMinimumDuration(0)
        self._progress_dialog = progress
        progress.canceled.connect(self.cancel_download)
        progress.show()
        self._status("正在通过 Mirror 酱下载更新安装程序…")
        self._spawn(request, "download", lambda worker: download_update_installer(url,
            cancel_check=request.cancelled.is_set, progress_callback=worker.progress.emit))

    def _progress(self, request, downloaded, total):
        progress = self._progress_dialog
        if not self._current(request) or progress is None:
            return
        progress.setLabelText(f"正在下载更新安装程序… {downloaded / 1024 / 1024:.1f} MB")
        if total:
            progress.setRange(0, 1000)
            progress.setValue(min(1000, int(downloaded * 1000 / total)))

    def _close_progress(self):
        progress, self._progress_dialog = self._progress_dialog, None
        if progress is not None:
            progress.blockSignals(True)  # 收尾关闭不再被解释为新的用户取消。
            progress.close()
            progress.deleteLater()

    def _end(self, request):
        if request is self._check:
            self._check = None
        if request is self._download:
            self._download = None
        self._buttons()

    def _fail(self, request, message):
        # 检查与下载可以并行；只回收失败请求自己拥有的下载取消入口。
        if request is self._download:
            self._close_progress()
        self._status(message)
        self._log(request, "update.check_failed" if request.kind == "check" else "update.download_failed",
                  "更新操作失败")
        if request.kind == "download":
            dialogs._show_mirror_project_download_dialog(self.parent(), message)
        elif request.manual:
            QMessageBox.warning(self.parent(), "检查更新失败", message)
        self._end(request)

    def _dispatch(self):
        pending, self._pending_launch = self._pending_launch, None
        if pending is None:
            return
        request, receipt = pending
        if self._update_channel != "installer" or not self._current(request) or request.dispatched:
            discard_installer(receipt)
            return
        # 在 OS 边界前消耗权限。异常、超时、迟到槽均不复活同一次派发。
        request.dispatched = True
        def dispatch(_worker):
            try:
                # 最终逐块摘要与 OS 派发都在同一已拥有线程；UI 取消无需等待大文件读取。
                launch_installer(receipt, cancel_check=lambda: (
                    request.cancelled.is_set() or request.identity != self._identity()))
                return True
            except BaseException:
                discard_installer(receipt)
                raise
        self._spawn(request, "dispatch", dispatch)

    def cancel_download(self):
        request = self._download
        if request is None:
            return
        request.cancelled.set()
        self._launch_timer.stop()
        if self._pending_launch is not None:
            _, receipt = self._pending_launch
            self._pending_launch = None
            discard_installer(receipt)
        self._close_progress()
        self._status("已取消 Mirror 下载，正在等待任务退场。")
        if not any(worker.request is request for worker in self._workers):
            self._end(request)

    def stop_for_account(self):
        for timer in (self._startup_timer, self._prompt_timer, self._launch_timer):
            timer.stop()
        self._startup_identity = self._pending_prompt = None
        for worker in self._workers:
            worker.request.cancelled.set()
        if self._check is not None:
            self._check.cancelled.set()
        self.cancel_download()
        self._check = self._download = None
        self._buttons()

    def rebuild_account(self):
        # AppContext 已发布新 settings，再通知 lifecycle.rebuild；不读旧窗口缓存。
        if not self._closed and self._view is not None:
            self._view[3].setText(str(self._load().get("mirror_cdk") or ""))
            self._status(f"当前版本: {APP_VERSION}" if self._update_channel == "installer" else self._portable_status())
            self._buttons()

    def begin_close(self):
        self._closed = True
        self.stop_for_account()
