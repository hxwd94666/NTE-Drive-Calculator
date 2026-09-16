# 管理模式切换意图、单一后台观察和一次性检测弹窗。
from __future__ import annotations

from dataclasses import dataclass, replace
from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtWidgets import QMessageBox, QInputDialog

from src.domain.work_mode import allowed_capabilities
from src.app.theme import theme_color
from src.ui.operation_guidance import allow_operation_entry, explain_operation_unavailable
from src.features.settings.work_mode_card import (
    MODE_LABELS, confirm_mode, report_summary, show_mode_report,
)
from src.services.game_observation_service import GameObservationService, ObservationResult


@dataclass(frozen=True)
class _TeardownResult:
    revision: int
    generation: int
    error: str = ""
    service: object = None


@dataclass(frozen=True)
class _PathResult:
    revision: int
    generation: int
    paths: tuple[str, ...]


class WorkModeController(QObject):
    observed = Signal(object)

    def __init__(self, *, window, policy, runtime, navigate=None) -> None:
        super().__init__(window)
        self.window, self.policy, self.runtime = window, policy, runtime
        self._navigate = navigate
        self._settings_scroll = None
        self._settings_targets = {}
        self._highlighted_card = None
        self._highlight_style = ""
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setSingleShot(True)
        self._highlight_timer.timeout.connect(self._clear_settings_highlight)
        self._guidance_active = False
        self._controls = None
        self._selector_settings = None
        self._show_request_id = None
        self._request_serial = 0
        self._closed = False
        self._teardown_pending = 0
        self.observed.connect(self._apply)
        self._observer = GameObservationService(tick=self._observe, publish=self.observed.emit)

    def attach_controls(self, *controls) -> None:
        self._controls = controls
        settings = self.policy.settings
        self._selector_settings = settings.mode.value

    @property
    def is_transitioning(self) -> bool:
        return self._closed or self._teardown_pending > 0

    def attach_settings_targets(self, *, scroll, mode_card, component_card, component_focus) -> None:
        self._settings_scroll = scroll
        self._settings_targets = {
            "mode": (mode_card, self._controls[0]),
            "detection": (mode_card, self._controls[2]),
            "deployment": (component_card, component_focus),
        }
        self._controls[2].setFocusPolicy(Qt.StrongFocus)

    def open_settings(self, target: str = "mode") -> None:
        if self._closed or self._navigate is None:
            return
        if self.window.isMinimized():
            self.window.showNormal()
        elif not self.window.isVisible():
            self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self._navigate("settings")
        QTimer.singleShot(0, lambda: self._focus_settings(target))

    def _focus_settings(self, target: str) -> None:
        if self._closed or self._settings_scroll is None:
            return
        widgets = self._settings_targets.get(target)
        if widgets is None:
            return
        card, focus = widgets
        if not card.isVisible():
            return  # Navigation may have been cancelled by another page's unsaved-edit prompt.
        self._clear_settings_highlight()
        self._highlighted_card, self._highlight_style = card, card.styleSheet()
        card.setProperty("workModeHighlight", True)
        card.setStyleSheet(self._highlight_style + (
            '\nQWidget[workModeHighlight="true"] { border:2px solid ' + theme_color("#58a6ff") + '; }'
        ))
        self._settings_scroll.ensureWidgetVisible(focus, 24, 48)
        focus.setFocus(Qt.OtherFocusReason)
        self._highlight_timer.start(1800)

    def _clear_settings_highlight(self) -> None:
        self._highlight_timer.stop()
        if self._highlighted_card is not None:
            self._highlighted_card.setProperty("workModeHighlight", False)
            self._highlighted_card.setStyleSheet(self._highlight_style)
            self._highlighted_card = None

    def operation_entry(self, capability: str, feature: str) -> bool:
        if self._closed or self._guidance_active:
            return False
        self._guidance_active = True
        try:
            return allow_operation_entry(self.window, self.policy, capability, feature, self.open_settings)
        finally:
            self._guidance_active = False

    def operation_unavailable(self, feature: str, detail: str, target: str = "detection") -> None:
        if self._closed or self._guidance_active:
            return
        self._guidance_active = True
        try:
            explain_operation_unavailable(self.window, feature, detail, self.open_settings, target)
        finally:
            self._guidance_active = False

    def start(self) -> None:
        if not self._closed:
            self._observer.start()

    def _observe(self, *, allow_connect=False, request_id=0, expected=None):
        revision, generation = expected or (
            self.policy.settings.revision, self.window.app_context.generation,
        )
        if self._closed or (revision, generation) != (
            self.policy.settings.revision, self.window.app_context.generation,
        ):
            return None
        try:
            probe = self.runtime.tick(allow_connect=allow_connect and not self.policy.settings.paused)
            return revision, generation, probe, request_id
        except Exception:
            return ObservationResult("fault", "检测失败，请重新检测。", revision, generation, request_id)

    def _stop_live_work(self) -> None:
        service = self.window._inventory_sync_service
        errors = []
        actions = [
            self.window.global_hotkey_manager.request_stop,
            self.window.battle_report_controller.stop,
            self.window.scanning_controller.request_stop,
        ]
        if service is not None:
            actions.extend([self.window.invalidate_inventory_sync_notifications, service.request_stop])
        actions.append(self.runtime.native_session.request_close)
        for action in actions:
            try:
                action()
            except Exception:
                errors.append("停止请求未全部完成")
        self._queue_teardown(service)
        if errors:
            raise RuntimeError("；".join(errors))

    def _finish_live_work(self, service, *, shutdown=False):
        errors = []
        for action in (
            service.stop if service is not None else lambda: None,
            self.window._mod_plugin_loading_service.stop_loader,
            self.runtime.close if shutdown else self.runtime.native_session.close,
        ):
            try:
                action()
            except Exception:
                errors.append("后台服务收尾未完成，请重新检测。")
        return "; ".join(errors)

    def _queue_teardown(self, service) -> None:
        revision, generation = self.policy.settings.revision, self.window.app_context.generation
        self._teardown_pending += 1
        self._observer.submit(lambda: _TeardownResult(
            revision, generation, self._finish_live_work(service), service,
        ))

    def _stop_after_failed_revocation(self, before) -> str:
        after = self.policy.settings
        revoked = (
            not allowed_capabilities(before).issubset(allowed_capabilities(after))
            or (not before.paused and after.paused)
            or (not before.pending_cleanup and after.pending_cleanup)
        )
        if revoked:
            try:
                self._stop_live_work()
            except Exception:
                return "；停止请求未全部完成，请保持当前模式并重新检测"
        return ""

    def select_mode(self, mode: str) -> None:
        if self._closed:
            return
        if not confirm_mode(self.window, mode):
            self.refresh_controls(reset_selection=True)
            return
        before = self.policy.settings
        try:
            # Revoke at the policy boundary before stopping owners; finish calls remain legal.
            self.policy.select_mode(mode, risk_confirmed=mode != "offline")
            self._stop_live_work()
            self.refresh_controls(reset_selection=True)
            self.check(show=True)
        except Exception as error:
            stop_error = self._stop_after_failed_revocation(before)
            self.refresh_controls(reset_selection=True)
            QMessageBox.warning(self.window, "工作模式", f"模式设置或收尾未完成：{error}{stop_error}")

    def set_raw_capture_draft(self, enabled: bool) -> None:
        if not enabled or self.operation_entry("diagnostics", "保存原始抓包"):
            return
        toggle = self.window._sync_raw_capture_toggle
        toggle.blockSignals(True)
        toggle.setChecked(False)
        toggle.blockSignals(False)

    def check(self, *, show: bool = False) -> None:
        if self._closed:
            return
        self._request_serial += 1
        request_id = self._request_serial
        if show or self._show_request_id is not None:
            self._show_request_id = request_id
        expected = self.policy.settings.revision, self.window.app_context.generation

        def perform():
            if self._closed:
                return None
            self.runtime.invalidate()
            return self._observe(allow_connect=True, request_id=request_id, expected=expected)
        self._observer.submit(perform, key="check")

    def detect_path(self) -> None:
        if self._closed:
            return
        settings, generation = self.policy.settings, self.window.app_context.generation

        def discover():
            if self._closed:
                return None
            paths = tuple(self.runtime.discover())
            current = self.policy.settings
            revised = replace(settings, game_executable=current.game_executable, revision=current.revision)
            revision = current.revision if revised == current else settings.revision
            return _PathResult(revision, generation, paths)
        self._observer.submit(discover, key="path")

    def cleanup(self) -> None:
        if self._closed:
            return
        errors = []
        for action in (
            lambda: self.policy.set_paused(True),
            lambda: self.policy.set_cleanup_pending(True), self._stop_live_work,
        ):
            try:
                action()
            except Exception as error:
                errors.append(str(error))
        self.refresh_controls()
        if errors:
            QMessageBox.warning(self.window, "清理游戏目录", "清理设置或收尾未完成：" + "; ".join(errors))
        else:
            self.check(show=True)

    def refresh_controls(self, *, reset_selection: bool = False) -> None:
        settings = self.policy.settings
        if getattr(self, "_presentation_revision", None) != settings.revision:
            self._presentation_revision = settings.revision
            refresh = getattr(self.window, "_refresh_equipment_plugin_status", None)
            if callable(refresh):
                refresh()
            if getattr(self.window, "warehouse_view", None) is not None:
                self.window._on_warehouse_selection_changed()
                self.window._update_warehouse_save_state()
        auto_sync = getattr(self.window, "auto_sync_controller", None)
        if auto_sync is not None:
            auto_sync.refresh()
        raw = getattr(self.window, "_sync_raw_capture_toggle", None)
        if raw is not None:
            raw.setVisible(True)
        raw_open = getattr(self.window, "_sync_raw_capture_open_button", None)
        if raw_open is not None:
            raw_open.setVisible(True)
        if self._controls:
            combo, _status, _check = self._controls
            selection = settings.mode.value
            # Background status updates must preserve the user's unconfirmed choice.
            if reset_selection or selection != self._selector_settings:
                combo.blockSignals(True)
                combo.setCurrentIndex(combo.findData(selection))
                combo.blockSignals(False)
                self._selector_settings = selection
        button = getattr(self.window, "work_mode_button", None)
        if button is not None:
            button.setText("模式：" + MODE_LABELS[settings.mode.value])
        self.window.battle_report_controller.set_work_mode_presentation()

    def _apply(self, result) -> None:
        if self._closed:
            return
        if isinstance(result, _TeardownResult):
            self._teardown_pending = max(0, self._teardown_pending - 1)
            if (result.service is not None and result.service is self.window._inventory_sync_service
                    and not result.service.is_running):
                self.window._stop_inventory_sync()
            if result.error and (result.revision, result.generation) == (
                self.policy.settings.revision, self.window.app_context.generation,
            ):
                QMessageBox.warning(self.window, "服务收尾", result.error)
            return
        if isinstance(result, _PathResult):
            if (result.revision, result.generation) != (
                self.policy.settings.revision, self.window.app_context.generation,
            ):
                return
            if len(result.paths) > 1:
                path, accepted = QInputDialog.getItem(
                    self.window, "选择游戏位置", "发现多个游戏目录，请选择正在使用的：",
                    list(result.paths), 0, False,
                )
                if accepted:
                    self.policy.set_game_executable(path)
            elif not result.paths:
                QMessageBox.information(self.window, "游戏位置", "有限范围检测未发现游戏，请选择 HTGame.exe。")
            self.check(show=True)
            return
        if isinstance(result, ObservationResult):
            if (result.revision, result.generation) != (
                self.policy.settings.revision, self.window.app_context.generation,
            ):
                return
            if self._controls:
                self._controls[1].setText("检测失败，请查看检测详情。")
            if result.request_id == self._show_request_id:
                self._show_request_id = None
                QMessageBox.warning(self.window, "模式检测", result.detail)
            return
        if result is None:
            return
        revision, generation, probe, request_id = result
        if generation != self.window.app_context.generation or revision != self.policy.settings.revision:
            if generation == self.window.app_context.generation and request_id == self._show_request_id:
                self.check(show=True)
            return
        service = self.window._inventory_sync_service
        if service is not None and getattr(service.state, "capture_source", "packet") == "packet":
            state = service.state
            probe = replace(probe, packet_listening=state.capturing,
                            packet_snapshot=state.source_snapshot_ready,
                            logged_in=probe.logged_in or state.source_snapshot_ready,
                            packet_fault=state.error or "")
        if service is not None and getattr(service.state, "capture_source", "packet") == "native":
            probe = replace(probe, native_inventory=replace(
                probe.native_inventory, projection_complete=bool(service.is_running and service.state.source_snapshot_ready),
            ))
        auto_sync = getattr(self.window, "auto_sync_controller", None)
        if auto_sync is not None:
            auto_sync.observe_probe(probe)
        self.refresh_controls()
        report = self.policy.build_report(probe)
        if self._controls:
            self._controls[1].setText(report_summary(report))
        if request_id == self._show_request_id:
            self._show_request_id = None
            show_mode_report(self.window, report, self)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._clear_settings_highlight()
        self._show_request_id = None
        self.window.global_hotkey_manager.request_stop()
        self.window.battle_report_controller.stop()
        self.window.scanning_controller.request_stop()
        service = self.window._inventory_sync_service
        if service is not None:
            service.request_stop()
            self.window.invalidate_inventory_sync_notifications()
        self.runtime.request_close()
        self._observer.close(finalize=lambda: self._finish_live_work(service, shutdown=True))
        detail = self.runtime.cleanup_exit_detail
        if detail:
            QMessageBox.information(
                self.window, "游戏组件清理提示",
                detail + "\n\nCalc 退出后不会继续后台监控。按上述原因处理后，重新打开 Calc "
                "继续核对和清理；无需重装或清空账号数据。",
            )
