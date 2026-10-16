# 组合根共享的套装编辑 owner：冻结账号、合并预览、提交后通知三个消费入口。
from __future__ import annotations

from copy import deepcopy
from functools import partial
from threading import Event

from PySide6.QtCore import QObject, Signal, Qt, QTimer
from PySide6.QtWidgets import QDialog

from src.app.context import CallbackAccountLifecycle
from src.app.page_tasks import PageTaskLane, PageRequest, PageCommitLane
from src.services.identification.matching_editor import (
    MatchingEditorRequest, load_matching_editor, preview_matching_role, save_matching_editor,
)
from src.services.identification.matching_settings import matching_settings_identity, normalize_matching_settings
from src.ui.role_suit_matching.dialog import MatchingEditorDialog


class RoleSuitMatchingController(QObject):
    changed = Signal(object)

    def __init__(self, context, catalog, parent):
        super().__init__(parent)
        self.context, self.catalog = context, catalog
        self._read = PageTaskLane(self)
        self._commit = PageCommitLane(self)
        self._read.idle.connect(self._retire)
        self._commit.settled.connect(self._retire)
        self._dialog = None
        self._retired = []
        self._cancel = Event()
        self._preview_cancel = Event()
        self._pending_preview = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(120)
        self._preview_timer.timeout.connect(self._dispatch_preview)
        self._sequence = 0
        self._closed = False
        self._request = None
        self._expected = None
        self._initial_draft = None
        self._embedded = False
        self._publishing = False
        self._management_commit = False
        self._panels = set()
        self.changed.connect(self._external_changed)
        self._unregister = context.register_account_lifecycle(CallbackAccountLifecycle(
            is_running=self.is_running, stop=self.stop, rebuild=lambda _account: None, start=lambda: None))

    def request(self):
        account, paths = self.context.account, self.context.paths
        return MatchingEditorRequest(account.active_account_id, self.context.generation,
            account.user_database_path, paths.config_dir, paths.equipment_allocation_database_path,
            paths.game_ui_asset_root)

    def is_running(self):
        return self._preview_timer.isActive() or self._read.is_running() or self._commit.is_running()

    def _current(self):
        return not self._closed and not self._cancel.is_set() and self._request == self.request()

    def open(self, parent=None):
        if self._closed:
            return
        if self._dialog is not None:
            self._dialog.raise_()
            self._dialog.activateWindow()
            return
        if self._commit.is_running():
            return
        self._request = self.request()
        self._cancel = Event()
        dialog = MatchingEditorDialog(self.catalog, parent or self.parent(), embedded=self._embedded)
        self._dialog = dialog
        if not self._embedded:
            dialog.setWindowModality(Qt.WindowModal)
        dialog.finished.connect(self._finished)
        dialog.preview_requested.connect(self._preview)
        dialog.preview_cancelled.connect(self._cancel_preview)
        dialog.save_requested.connect(self._save)
        dialog.management_save_requested.connect(self._save_management)
        dialog.reload_requested.connect(self._reload)
        dialog.show()
        self._reload()

    def create_panel(self, parent):
        """各窗口有独立草稿和任务 owner，共用同一账号服务及保存通知总线。"""
        owner = RoleSuitMatchingController(self.context, self.catalog, self)
        owner._embedded = True
        self._panels.add(owner)
        owner.destroyed.connect(lambda: self._panels.discard(owner))
        owner.changed.connect(self.changed.emit)
        self.changed.connect(owner._external_changed)
        parent.finished.connect(owner.close)
        parent.destroyed.connect(owner.close)
        owner.open(parent)
        owner._dialog.dismiss_requested.connect(parent.reject)
        # 账号生命周期撤销面板时也关闭持有旧账号草稿的管理窗口。
        owner._dialog.finished.connect(lambda _result: parent.reject() if parent.isVisible() else None)
        return owner._dialog

    def _reload(self):
        if not self._current() or self._dialog is None or self._commit.is_running():
            return
        self._cancel_preview()
        self._expected = self._initial_draft = None
        self._dialog.set_busy(True)
        self._dialog.status.setText(self.catalog.render("matching.loading"))
        self._sequence += 1
        self._read.submit(PageRequest(self._sequence,
            partial(load_matching_editor, self._request, self._cancel), self._loaded, self._failed))

    def _external_changed(self, request):
        if self._publishing or not self._current() or self._dialog is None or request != self._request:
            return
        if self._initial_draft is not None and (
            self._commit.is_running() or
            matching_settings_identity(self._dialog.draft) != matching_settings_identity(self._initial_draft)
        ):
            # 不以同步覆盖另一入口尚未保存的草稿；旧基线提交仍由 CAS 拒绝。
            self._dialog.status.setText(self.catalog.render("matching.external_changed"))
            self._dialog.reload_button.show()
        else:
            self._reload()

    def _loaded(self, data):
        if self._current() and self._dialog is not None:
            self._expected = deepcopy(data[2])
            self._initial_draft = deepcopy(data[3])
            self._dialog.load(data)

    def _preview(self, character_id, draft):
        if not self._current():
            return
        # 连续点选只保留最后一份草稿，避免每次点击都读取静态库并重新求图纸。
        self._cancel_preview()
        self._pending_preview = (character_id, deepcopy(draft))
        self._preview_timer.start()

    def _dispatch_preview(self):
        pending, self._pending_preview = self._pending_preview, None
        if not self._current() or pending is None:
            return
        character_id, draft = pending
        self._preview_cancel = Event()
        self._sequence += 1
        self._read.submit(PageRequest(self._sequence,
            partial(preview_matching_role, self._request, draft, character_id, self._preview_cancel),
            self._previewed, self._failed))

    def _previewed(self, states):
        if self._current() and self._dialog is not None:
            self._dialog.show_preview(states)

    def _cancel_preview(self):
        self._preview_timer.stop()
        self._pending_preview = None
        self._preview_cancel.set()
        self._read.cancel()

    def _failed(self, _error):
        if self._current() and self._dialog is not None:
            self._dialog.status.setText(self.catalog.render("matching.failed"))
            # 初次读取失败不开放空草稿保存；预览失败仍保留已有选择供修复。
            if self._expected is not None:
                self._dialog.set_busy(False)

    def _save_management(self, config):
        if self._dialog is not None:
            self._save(deepcopy(self._dialog.draft), post_action_config=config)

    def _save(self, draft, *, post_action_config=None):
        if not self._current() or self._expected is None or self._commit.is_running():
            return
        self._management_commit = post_action_config is not None
        # 旧变体只作读取投影；未改动有效草稿时保留原身份，避免一次空保存使结果失效。
        if matching_settings_identity(draft) == matching_settings_identity(self._initial_draft):
            draft = self._expected
        self._cancel_preview()
        self._dialog.set_busy(True, committing=True)
        self._dialog.status.setText(self.catalog.render("matching.saving"))
        self._pending_saved = normalize_matching_settings(draft)
        self._commit.submit(partial(save_matching_editor, self._request, deepcopy(draft),
                                    deepcopy(self._expected), self._cancel,
                                    post_action_config=deepcopy(post_action_config)),
                            self._saved, self._save_failed, self._save_failed)

    def _saved(self, changed):
        if self._current():
            if changed:
                self._publishing = True
                try:
                    self.changed.emit(self._request)
                finally:
                    self._publishing = False
            if self._dialog is not None:
                self._dialog.set_busy(False)
                if self._management_commit:
                    # 外层窗口接受后，由原仓库入口发起评估；不在套装编辑器另起业务。
                    self._dialog.management_save_finished.emit(True)
                elif self._embedded:
                    # 内层只保存套装并返回；更新编辑基线，外层保存才触发管理计算。
                    self._expected = deepcopy(self._pending_saved)
                    self._initial_draft = deepcopy(self._dialog.draft)
                    self._dialog.reload_button.hide()
                    self._dialog.show_overview()
                else:
                    self._dialog.accept()

    def _save_failed(self, _error):
        if self._current() and self._dialog is not None:
            self._dialog.set_busy(False)
            self._dialog.status.setText(self.catalog.render("matching.save_failed"))
            if self._management_commit:
                self._dialog.management_save_finished.emit(False)

    def _finished(self, _result):
        self._cancel.set()
        self._cancel_preview()
        if self._dialog is not None:
            self._dialog.set_busy(False)
            if self._embedded:
                # 父窗口可先销毁；退场面板由当前任务 owner 持有到 worker 物理结束。
                self._dialog.hide()
                self._dialog.setParent(None)
            self._retired.append(self._dialog)
        self._dialog = None
        self._expected = None
        self._initial_draft = None
        self._retire()

    def _retire(self, *_args):
        if not self.is_running():
            retired, self._retired = self._retired, []
            for dialog in retired:
                dialog.deleteLater()
            if self._closed and self._embedded:
                self.deleteLater()

    def stop(self):
        self._cancel.set()
        self._cancel_preview()
        if self._dialog is not None:
            # 账号生命周期撤销草稿及提交 token；不等待 UI 线程，不触碰其他窗口。
            for child in self._dialog.findChildren(QDialog):
                child.reject()
            QDialog.reject(self._dialog)

    def close(self):
        if self._closed:
            return
        self.stop()
        self._closed = True
        self._read.close()
        self._unregister()
        for owner in tuple(self._panels):
            owner.close()
        self._retire()
