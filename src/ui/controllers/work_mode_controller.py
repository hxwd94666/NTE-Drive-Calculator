# 管理模式切换意图、单一后台观察和一次性检测弹窗。
from __future__ import annotations

from dataclasses import dataclass, replace
from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtWidgets import QDialog, QMessageBox, QInputDialog

from src.domain.work_mode import WorkMode, allowed_capabilities
from src.app.theme import theme_color
from src.ui.operation_guidance import allow_operation_entry, explain_operation_unavailable
from src.features.settings.work_mode_card import (
    MODE_LABELS, CleanupResultDialog, ModeReportDialog, confirm_mode,
    prompt_offline_sync_mode, report_summary,
)
from src.services.game_observation_service import GameObservationService, ObservationResult
from src.services.work_mode_diagnostics import detection_failure_detail
from src.services.sync_enable_preflight import decide_sync_activation, decide_sync_enable
from src.ui.controllers.component_upgrade_guide import ComponentUpgradeGuideMixin, UpgradeResult


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


@dataclass(frozen=True)
class _CleanupResult:
    request_id: int
    generation: int
    pending: bool
    state: str
    detail: str




class WorkModeController(ComponentUpgradeGuideMixin, QObject):
    observed = Signal(object)
    plugins_applied = Signal(object)

    def __init__(self, *, window, policy, runtime, navigate=None, observe_plugins=None,
                 apply_plugin_policy=None, apply_plugins=None) -> None:
        super().__init__(window)
        self.window, self.policy, self.runtime = window, policy, runtime
        self._navigate = navigate
        self._observe_plugins = observe_plugins
        self._apply_plugin_policy = apply_plugin_policy
        self._apply_plugins = apply_plugins
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
        self._report_dialog = None
        self._cleanup_dialog = None
        self._cleanup_request_id = None
        self._upgrade_dialog = None
        self._upgrade_evidence = None
        self._upgrade_flow_active = False
        self._upgrade_prompted = False
        self._upgrade_cleanup_resume = False
        self._upgrade_cleanup_assessed = False
        self._sync_preflight_callback = None
        self._sync_preflight_request = None
        self._sync_preflight_context = None
        self._sync_activation_request = None
        self._sync_activation_callback = None
        self._sync_activation_prior_ready = False
        self._request_serial = 0
        self._closed = False
        self._teardown_pending = 0
        self.observed.connect(self._apply)
        self._observer = GameObservationService(tick=self._observe, publish=self.observed.emit)
        self._plugin_worker = GameObservationService(tick=lambda: None, publish=self.plugins_applied.emit)

    def attach_controls(self, *controls) -> None:
        self._controls = controls
        settings = self.policy.settings
        self._selector_settings = settings.mode.value

    @property
    def is_transitioning(self) -> bool:
        return self._closed or self._teardown_pending > 0

    def attach_settings_targets(self, *, scroll, mode_card, component_card, component_focus,
                                game_path_focus=None, loading_method_focus=None, npcap_focus=None,
                                core_focus=None) -> None:
        self._settings_scroll = scroll
        self._settings_targets = {
            "mode": (mode_card, self._controls[0]),
            "detection": (mode_card, self._controls[2]),
            "deployment": (component_card, component_focus),
            "game_path": (component_card, game_path_focus or component_focus),
            "loading_method": (component_card, loading_method_focus or component_focus),
            "npcap": (component_card, npcap_focus or component_focus),
            "core": (component_card, core_focus or component_focus),
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
            navigate = self._navigate if target == "home" and self._navigate is not None else self.open_settings
            explain_operation_unavailable(self.window, feature, detail, navigate, target)
        finally:
            self._guidance_active = False

    def start(self) -> None:
        if not self._closed:
            self._observer.start()

    def _observe(
        self, *, allow_connect=False, request_id=0, expected=None,
        allow_unrecorded_legacy_cleanup=False,
        preview=False,
    ):
        revision, generation = expected or (
            self.policy.settings.revision, self.window.app_context.generation,
        )
        if self._closed or (revision, generation) != (
            self.policy.settings.revision, self.window.app_context.generation,
        ):
            return None
        try:
            tick_args = {"allow_connect": allow_connect and not self.policy.settings.paused}
            if allow_unrecorded_legacy_cleanup:
                tick_args["allow_unrecorded_legacy_cleanup"] = True
            if preview:
                tick_args["preview"] = True
            probe = self.runtime.tick(**tick_args)
            if self._observe_plugins is not None:
                self._observe_plugins(probe)
            return revision, generation, probe, request_id
        except Exception as error:
            detail = detection_failure_detail(error, record=bool(request_id or allow_connect))
            return ObservationResult("fault", detail, revision, generation, request_id)

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
            try:
                if self._apply_plugin_policy is not None:
                    self._apply_plugin_policy()
            finally:
                self._stop_live_work()
            self.refresh_controls(reset_selection=True)
            self.check(show=True)
            self.request_upgrade_assessment()
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

    def refresh_plugins(self) -> None:
        if not self._closed:
            def apply():
                if self._closed or self._apply_plugins is None:
                    return None
                self._apply_plugins()
                return True
            self._plugin_worker.submit(apply, key="plugins")

    def component_state_changed(self) -> None:
        """Refresh a visible report and the compact summary after manual component work."""
        if not self._closed:
            self.check(show=self._report_dialog is not None,
                       preview=self._sync_preflight_callback is not None)
            self.request_upgrade_assessment()

    def begin_sync_enable(self, confirm) -> None:
        if self._closed:
            return
        if self.policy.settings.mode == WorkMode.OFFLINE:
            if prompt_offline_sync_mode(self.window):
                self.open_settings("mode")
            return
        if self._sync_activation_request is not None:
            if self._report_dialog is not None:
                self._report_dialog.raise_()
            return
        self._sync_preflight_callback = confirm
        self._sync_preflight_context = (
            self.policy.settings.revision, self.window.app_context.generation,
        )
        self.check(show=True, preview=True)

    def check(self, *, show: bool = False, allow_unrecorded_legacy_cleanup: bool = False,
              preview: bool = False) -> None:
        if self._closed or self._sync_activation_request is not None:
            return
        self._request_serial += 1
        request_id = self._request_serial
        if preview:
            self._sync_preflight_request = request_id
            self._sync_preflight_context = (
                self.policy.settings.revision, self.window.app_context.generation,
            )
        else:
            self._sync_preflight_request = None
            self._sync_preflight_callback = None
        if show or self._show_request_id is not None:
            self._show_request_id = request_id
            if self._report_dialog is None:
                self._report_dialog = ModeReportDialog(self.window, self)
                self._report_dialog.finished.connect(self._dismiss_report)
            self._report_dialog.begin(self.policy.settings.mode.value, preview=preview)
            if self._controls:
                self._controls[1].setText("正在核对同步条件…" if preview else "正在检测并处理组件…")
        expected = self.policy.settings.revision, self.window.app_context.generation

        def perform():
            if self._closed:
                return None
            self.runtime.invalidate()
            result = self._observe(
                allow_connect=not preview,
                request_id=request_id,
                expected=expected,
                allow_unrecorded_legacy_cleanup=allow_unrecorded_legacy_cleanup,
                preview=preview,
            )
            return result or ObservationResult("superseded", "检测上下文已改变，请重新检测。", *expected, request_id)
        self._observer.submit(perform, key="check")

    def _dismiss_report(self, _result=0) -> None:
        dialog, self._report_dialog = self._report_dialog, None
        self._show_request_id = None
        self._sync_preflight_request = None
        self._sync_preflight_callback = None
        if self._sync_activation_callback is not None:
            self._sync_activation_callback = None
            if not self._sync_activation_prior_ready:
                try:
                    self.policy.set_component_auto_ready(False)
                except OSError:
                    pass  # The in-process automatic authority was revoked before saving.
                self.refresh_controls()
        if dialog is not None:
            dialog.deleteLater()

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
        if self._cleanup_request_id is not None:
            if self._cleanup_dialog is not None:
                self._cleanup_dialog.raise_()
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
        self._request_serial += 1
        request_id = self._request_serial
        self._cleanup_request_id = request_id
        generation = self.window.app_context.generation
        dialog = CleanupResultDialog(self.window, continue_upgrade=self._upgrade_cleanup_resume)
        self._cleanup_dialog = dialog
        dialog.finished.connect(lambda result: self._dismiss_cleanup_dialog(dialog, result))
        dialog.begin()
        if errors:
            self._apply(_CleanupResult(
                request_id, generation, True, "fault",
                "清理准备未完成；请检查配置保存权限与后台服务状态后重试。",
            ))
            return

        def perform():
            failure = ""
            try:
                self.runtime.cleanup(allow_unrecorded_legacy_workspace=True)
            except Exception:
                failure = (self.runtime.cleanup_detail or
                           "清理过程异常；已保留待清理状态，请检查环境设置后重试。")
            state = getattr(self.runtime, "cleanup_state", None)
            return _CleanupResult(
                request_id, generation, self.policy.settings.pending_cleanup,
                "fault" if failure else getattr(state, "value", state) or "waiting",
                failure or self.runtime.cleanup_detail or "尚未确认清理结果，请重试。",
            )

        if not self._observer.submit(perform, key="cleanup"):
            self._apply(_CleanupResult(
                request_id, generation, True, "fault", "后台服务已停止；请重新打开程序后清理。",
            ))

    def _dismiss_cleanup_dialog(self, dialog, result: int) -> None:
        if self._cleanup_dialog is dialog:
            self._cleanup_dialog = None
        dialog.deleteLater()
        if result == QDialog.Accepted:
            self._resume_upgrade_after_cleanup_if_ready()
        else:
            self._upgrade_cleanup_resume = False
            self._upgrade_cleanup_assessed = False

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
        if isinstance(result, UpgradeResult):
            if (result.revision, result.generation) != (
                self.policy.settings.revision, self.window.app_context.generation,
            ):
                if result.generation == self.window.app_context.generation:
                    self.request_upgrade_assessment(startup=result.startup)
                return
            self._upgrade_evidence = result.evidence
            if (result.evidence.kind == "ready" and self._upgrade_flow_active
                    and not self.policy.settings.paused
                    and self.policy.settings.mode.value in {"medium", "developer"}):
                try:
                    self._set_upgrade_progress(False)
                except OSError:
                    pass  # The next launch can retry clearing this app-local guide marker.
            self._refresh_upgrade_banner()
            if self._upgrade_cleanup_resume:
                self._upgrade_cleanup_assessed = True
                self._resume_upgrade_after_cleanup_if_ready()
            if (result.startup and self._upgrade_stage() is not None
                    and not self._upgrade_prompted):
                self._upgrade_prompted = True
                QTimer.singleShot(800, self._show_upgrade_when_idle)
            return
        if isinstance(result, _CleanupResult):
            if result.request_id != self._cleanup_request_id:
                return
            self._cleanup_request_id = None
            if result.generation != self.window.app_context.generation:
                if self._cleanup_dialog is not None:
                    self._cleanup_dialog.close()
                return
            self.refresh_controls()
            if self._cleanup_dialog is not None:
                self._cleanup_dialog.set_result(
                    pending=result.pending, state=result.state, detail=result.detail,
                )
            if self._upgrade_cleanup_resume:
                if result.pending or result.state == "fault":
                    self._upgrade_cleanup_resume = False
                    self._upgrade_cleanup_assessed = False
            self.request_upgrade_assessment()
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
            if result.request_id == self._sync_activation_request:
                self._finish_sync_activation(error=result.detail)
                self._show_request_id = None
                return
            if (result.revision, result.generation) != (
                self.policy.settings.revision, self.window.app_context.generation,
            ):
                if result.request_id == self._show_request_id and self._report_dialog is not None:
                    if result.generation == self.window.app_context.generation:
                        self.check(show=True, preview=result.request_id == self._sync_preflight_request)
                    else:
                        self._show_request_id = None
                        self._report_dialog.set_error("账号已切换，请重新检测当前账号环境。")
                return
            if self._controls:
                self._controls[1].setText("检测失败，请查看检测详情。")
            if result.request_id == self._show_request_id:
                self._show_request_id = None
                if self._report_dialog is not None:
                    self._report_dialog.set_error(result.detail)
            return
        if result is None:
            return
        revision, generation, probe, request_id = result
        if generation != self.window.app_context.generation or revision != self.policy.settings.revision:
            if request_id == self._sync_activation_request:
                self._finish_sync_activation(error="准备上下文已改变，自动同步仍保持关闭。")
                self._show_request_id = None
                return
            if generation == self.window.app_context.generation and request_id == self._show_request_id:
                self.check(show=True, preview=request_id == self._sync_preflight_request)
            elif request_id == self._show_request_id and self._report_dialog is not None:
                self._show_request_id = None
                self._report_dialog.set_error("账号已切换，请重新检测当前账号环境。")
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
        if self._controls and (self._show_request_id is None or request_id == self._show_request_id):
            self._controls[1].setText(report_summary(report))
        if request_id == self._show_request_id:
            self._show_request_id = None
            if self._report_dialog is not None:
                self._report_dialog.set_report(report)
                if request_id == self._sync_preflight_request and self._sync_preflight_callback:
                    decision = decide_sync_enable(
                        self.policy.settings, self.policy.deployment_record, probe,
                    )
                    if decision.ready:
                        self._confirm_sync_enable()
                    else:
                        self._report_dialog.set_sync_preflight(decision)
        if request_id == self._sync_activation_request:
            self._finish_sync_activation(probe)

    def _confirm_sync_enable(self) -> None:
        callback = self._sync_preflight_callback
        if callback is None:
            return
        if self._sync_preflight_context != (
            self.policy.settings.revision, self.window.app_context.generation,
        ):
            self.check(show=True, preview=True)
            return
        prior_ready = self.policy.settings.component_auto_ready
        try:
            if not prior_ready:
                self.policy.set_component_auto_ready(True)
        except OSError:
            if self._report_dialog is not None:
                self._report_dialog.set_error("组件准备状态未能保存，请检查配置目录后重试。")
            return
        self._sync_preflight_callback = None
        self._sync_preflight_request = None
        self._sync_activation_callback = callback
        self._sync_activation_prior_ready = prior_ready
        self._request_serial += 1
        request_id = self._request_serial
        self._sync_activation_request = request_id
        self._show_request_id = request_id
        if self._report_dialog is not None:
            self._report_dialog.begin(self.policy.settings.mode.value)
        frozen = (self.policy.operation_revision, self.window.app_context.generation,
                  self.policy.settings.mode, self.policy.settings.game_executable)

        def perform():
            settings = self.policy.settings
            expected = settings.revision, frozen[1]
            self.runtime.invalidate()
            result = self._observe(allow_connect=True, request_id=request_id, expected=expected)
            if isinstance(result, ObservationResult) or result is None:
                return result or ObservationResult("superseded", "准备上下文已改变，请重新检测。", *expected, request_id)
            current = self.policy.settings
            if frozen != (self.policy.operation_revision, self.window.app_context.generation,
                          current.mode, current.game_executable):
                return ObservationResult("superseded", "准备期间模式或游戏路径已改变，请重新开启同步。",
                                         current.revision, self.window.app_context.generation, request_id)
            verified = self._observe(preview=True, request_id=request_id,
                                     expected=(current.revision, frozen[1]))
            return verified or ObservationResult(
                "superseded", "准备上下文已改变，请重新开启同步。",
                current.revision, frozen[1], request_id,
            )

        if not self._observer.submit(perform, key="check"):
            self._finish_sync_activation(error="后台检测已结束，自动同步仍保持关闭。")

    def _finish_sync_activation(self, probe=None, *, error="") -> None:
        callback = self._sync_activation_callback
        prior_ready = self._sync_activation_prior_ready
        self._sync_activation_request = None
        self._sync_activation_callback = None
        if callback is None:
            return
        decision = (decide_sync_activation(self.policy.settings, probe) if probe is not None
                    else None)
        success = False
        if decision and decision.ready:
            try:
                success = bool(callback())
            except Exception:
                error = "同步偏好保存未完成，自动同步仍保持关闭。"
        if not success and not prior_ready:
            try:
                self.policy.set_component_auto_ready(False)
            except OSError:
                pass  # revoke_first already removed the in-process automatic authority.
            self.refresh_controls()
        if self._report_dialog is not None:
            if success:
                self._report_dialog.accept()
            else:
                self._report_dialog.set_activation_result(
                    False,
                    (decision.detail if decision is not None and not decision.ready
                     else error or "同步偏好未能保存，请重新检测。"),
                )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._sync_activation_request is not None:
            self._finish_sync_activation(error="程序正在退出，自动同步仍保持关闭。")
        if self._report_dialog is not None:
            self._report_dialog.close()
        if self._cleanup_dialog is not None:
            self._cleanup_dialog.close()
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
        self._plugin_worker.close()
        self._observer.close(finalize=lambda: self._finish_live_work(service, shutdown=True))
        detail = self.runtime.cleanup_exit_detail
        if detail:
            QMessageBox.information(
                self.window, "游戏组件清理提示",
                detail + "\n\nCalc 退出后不会继续后台监控。按上述原因处理后，重新打开 Calc "
                "继续核对和清理；无需重装或清空账号数据。",
            )
