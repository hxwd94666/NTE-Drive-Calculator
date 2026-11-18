# 仓库独立任务 owner 冻结账号与草稿，统一提交设置后生成预览，等待本 owner 线程物理结束才关窗。
from src.ui.common.enum_text import ui_enum_text
from src.observability.stage_timings import StageTimings, timed_call
from concurrent.futures import CancelledError
from copy import deepcopy
from functools import partial
from threading import Event

from PySide6.QtCore import QObject, Signal, QTimer
from PySide6.QtWidgets import QDialog, QMessageBox

from src.observability.context import OperationContext
from src.app.context import CallbackAccountLifecycle
from src.app.page_tasks import PageTaskLane, PageCommitLane, PageRequest
from src.domain.equipment.eligibility.management_settings import MatchingScope
from src.features.inventory.warehouse.management.management_dialog import WarehouseManagementDialog
from src.features.inventory.warehouse.management.conflict_dialog import WarehouseConflictDialog
from src.services.identification.matching_editor import MatchingEditorRequest, preview_matching_role
from src.services.identification.matching_settings import normalize_matching_settings
from src.services.inventory.management.management_settings import ManagementSettingsService
from src.services.inventory.management.warehouse_management_editor import load_warehouse_editor, generate_editor_rules
from src.services.inventory.recommendation.warehouse_recommendation_preview import build_preview


class WarehouseManagementController(QObject):
    progress = Signal(object, str)

    def __init__(self, context, catalog, parent=None, *, capture, project, settings_changed):
        super().__init__(parent)
        self.context, self.catalog = context, catalog
        self.capture, self.project, self.settings_changed = capture, project, settings_changed
        self.dialog = None
        self.conflict_dialog = None
        self._focus_rules = ()
        self._request = None
        self._cancel, self._match_cancel = Event(), Event()
        self._read, self._matching, self._commit = PageTaskLane(self), PageTaskLane(self), PageCommitLane(self)
        self._closing = None
        self._sequence = 0
        self._key = None
        self._expected = None
        self._timings = None
        self._read.idle.connect(self._retire)
        self._matching.idle.connect(self._retire)
        self._commit.settled.connect(self._retire)
        self.progress.connect(self._progress)
        self._unregister = context.register_account_lifecycle(
            CallbackAccountLifecycle(
                is_running=self.is_running, stop=self.stop, rebuild=lambda _: None, start=lambda: None
            )
        )

    def request(self):
        account, paths = self.context.account, self.context.paths
        return MatchingEditorRequest(
            account.active_account_id,
            self.context.generation,
            account.user_database_path,
            paths.config_dir,
            paths.equipment_allocation_database_path,
            paths.equipment_allocation_asset_root,
            scope=MatchingScope.WAREHOUSE,
        )

    def _check_identity(self, request):
        if request != self.request():
            raise CancelledError("账号或静态资料已切换")

    def _check_operation(self, request, cancel):
        self._check_identity(request)
        if cancel.is_set():
            raise CancelledError()

    def _current(self):
        return (
            self.dialog is not None
            and self._closing is None
            and not self._cancel.is_set()
            and self._request == self.request()
        )

    def open(self):
        if self.dialog is not None:
            self.dialog.raise_()
            self.dialog.activateWindow()
            return
        if self.is_running():
            return
        self._request, self._cancel, self._closing = self.request(), Event(), None
        self.dialog = WarehouseManagementDialog(self.catalog, config_dir=self._request.config, parent=self.parent())
        self.dialog.dismiss_requested.connect(self.cancel)
        self.dialog.generate_requested.connect(self.generate)
        self.dialog.rules_requested.connect(self.generate_rules)
        self.dialog.matching.preview_requested.connect(self.preview_matching)
        self.dialog.matching.preview_cancelled.connect(self.cancel_matching)
        self.dialog.show()
        self._task("load", partial(load_warehouse_editor, self._request, self._cancel), self._loaded)

    def is_running(self):
        return self._read.is_running() or self._matching.is_running() or self._commit.is_running()

    def _task(self, kind, read, apply):
        self.cancel_matching()
        self._sequence += 1
        self._key = (self._request, kind, self._sequence)
        self.dialog.set_busy(True, message=self.catalog.render(f"warehouse.management.busy.{kind}"))
        self._read.submit(
            PageRequest(self._key, read, lambda value: apply(value) if self._current() else None, self._failed)
        )

    def _loaded(self, data):
        self._expected = deepcopy(data[:2])
        self.dialog.install(data)
        if self._focus_rules:
            self.dialog.show_rules()
            dialog, identity = self.dialog, self._focus_rules[0]
            QTimer.singleShot(0, dialog, lambda: dialog.rules.focus_rule(identity))
            self._focus_rules = ()

    def open_conflicts(self, conflicts, guard):
        if self.conflict_dialog is not None:
            self.conflict_dialog.raise_()
            return
        try:
            if guard is None:
                raise ValueError(self.catalog.render("warehouse.management.conflict.stale"))
            guard.require_current()
        except ValueError as exc:
            QMessageBox.warning(self.parent(), self.catalog.render("warehouse.management.conflict.title"), ui_enum_text(self.catalog, str(exc)))
            return
        request = self.request()
        dialog = WarehouseConflictDialog(self.catalog, deepcopy(conflicts), self.parent())
        self.conflict_dialog = dialog

        def finished(*_):
            if self.conflict_dialog is dialog:
                self.conflict_dialog = None
            dialog.deleteLater()

        def edit(identities):
            try:
                self._check_identity(request)
                guard.require_current()
            except (ValueError, CancelledError):
                dialog.reject()
                return
            dialog.accept()
            self._focus_rules = tuple(identities)
            self.open()

        dialog.finished.connect(finished)
        dialog.edit_requested.connect(edit)
        dialog.show()

    def _failed(self, reason):
        if self._current():
            if self._timings is not None:
                self._timings.finish("request_failed", outcome="failed")
            self.dialog.set_busy(False)
            self.dialog.show_error(reason)

    def generate_rules(self):
        if not self._current() or self._commit.is_running():
            return
        config, matching = self.dialog.collect()
        request, cancel = self._request, self._cancel

        def ready(value):
            self.dialog.rules.install_generated(value)
            self.dialog.set_busy(False, message=self.catalog.render("warehouse.management.rules.ready"))

        self._task(
            "rules",
            partial(
                generate_editor_rules,
                request,
                config,
                matching,
                cancel,
                partial(self._check_operation, request, cancel),
            ),
            ready,
        )

    def generate(self):
        if not self._current() or self._commit.is_running() or self._expected is None:
            return
        try:
            frozen = self.capture()
            config, matching = self.dialog.collect()
            matching = normalize_matching_settings(matching)
        except ValueError as exc:
            self._failed(str(exc))
            return
        request, cancel = self._request, self._cancel
        if self._timings is not None:
            self._timings.close()
        timings = self._timings = StageTimings(OperationContext.create("warehouse",
            account_id=request.account_id, context_generation=request.generation, snapshot_id=frozen.snapshot_id))
        timings.note("request", manual_target_count=len(frozen.manual_targets))
        timings.mark("settings_queued")
        check = partial(self._check_operation, request, cancel)
        service = ManagementSettingsService(request.database, scope=MatchingScope.WAREHOUSE)
        expected_config, expected_matching = deepcopy(self._expected)
        self.cancel_matching()
        self.dialog.set_busy(True, committing=True, message=self.catalog.render("warehouse.management.busy.save"))

        def committed(saved):
            if not self._current():
                return
            self._expected = deepcopy((saved, matching))
            timings.since("settings_ready", "settings.delivery_wait")
            timed_call(timings, "gui.invalidate_old_preview", self.settings_changed)
            build = partial(
                build_preview,
                database=request.database,
                static_database=request.static,
                asset_root=request.asset_root,
                config_dir=request.config,
                config=saved,
                matching=matching,
                user_targets=dict(frozen.manual_targets),
                check_context=partial(self._check_identity, request),
                cancel_check=cancel.is_set, timings=timings,
            )

            def ready(value):
                result, guard = value
                timings.since("preview_ready", "delivery.wait")
                timings.flush("worker_received")
                timed_call(timings, "gui.project_total", self.project, frozen, result, guard)
                timings.mark("projected")
                timings.flush("gui_submitted")
                self._closing = QDialog.Accepted

            progress_key = (request, "preview", self._sequence + 1)
            def preview():
                timings.since("preview_queued", "preview.queue")
                try:
                    return build(progress=partial(self.progress.emit, progress_key))
                finally:
                    timings.mark("preview_ready")
            timings.mark("preview_queued")
            self._task("preview", preview, ready)

        def save_settings():
            timings.since("settings_queued", "settings.queue")
            try:
                return timed_call(timings, "settings.commit", service.save, config, matching,
                    expected_config=expected_config, expected_matching=expected_matching, check_current=check)
            finally:
                timings.mark("settings_ready")

        self._commit.submit(
            save_settings,
            committed,
            self._failed,
            self._failed,
        )

    def preview_matching(self, character_id, draft):
        if not self._current() or self._commit.is_running():
            return
        self.cancel_matching()
        request, token = self._request, Event()
        self._match_cancel = token
        self._sequence += 1
        settings = deepcopy(draft)

        def ready(states):
            if (
                self._current()
                and not token.is_set()
                and self.dialog.matching._active_id == character_id
                and self.dialog.matching.draft == settings
            ):
                self.dialog.matching.show_preview(states)

        self._matching.submit(
            PageRequest(
                (request, character_id, self._sequence),
                partial(preview_matching_role, request, settings, character_id, token),
                ready,
                self._failed,
            )
        )

    def cancel_matching(self):
        self._match_cancel.set()
        self._matching.cancel()

    def _progress(self, key, message):
        if key == self._key and self._current():
            self.dialog.status.setText(message)

    def cancel(self):
        if not self._commit.is_running():
            self.stop()

    def stop(self):
        if self._timings is not None:
            if self._request == self.request():
                self._timings.finish("request_cancelled", outcome="cancelled")
            self._timings.close()
        self._cancel.set()
        self._read.cancel()
        self.cancel_matching()
        self._focus_rules = ()
        if self.conflict_dialog is not None:
            self.conflict_dialog.reject()
        if self.dialog is not None:
            self._closing = QDialog.Rejected
            self.dialog.set_busy(True, committing=True, message=self.catalog.render("warehouse.management.busy.close"))
        self._retire()

    def _retire(self, *_):
        if self._closing is not None and self.dialog is not None and not self.is_running():
            dialog, result = self.dialog, self._closing
            self.dialog = None
            if self._timings is not None:
                self._timings.since("projected", "owner.retire_wait")
                self._timings.finish("dialog_retired")
            QDialog.done(dialog, result)
            dialog.deleteLater()
            self._closing = None
