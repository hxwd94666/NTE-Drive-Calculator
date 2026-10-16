# 编排检测报告、只读预检、同步激活及对话框收尾，冻结请求身份。
from PySide6.QtCore import QTimer

from src.domain.work_mode import WorkMode
from src.features.settings.work_mode_card import ModeReportDialog, prompt_offline_sync_mode
from src.services.work_modes.observation import ObservationResult
from src.services.work_modes.sync_preflight import decide_sync_enable


class WorkModeReportActionsMixin:
    def attach_sync_enable_action(self, request_enable):
        self._request_report_sync_enable = request_enable
        self._report_sync_home_generation = None

    def sync_enable_action_for_report(self, report):
        settings = self.policy.settings
        callback = getattr(self, "_request_report_sync_enable", None)
        if (not callback or self.is_transitioning or self._sync_activation_request is not None
                or settings.auto_sync_enabled
                or settings.mode == WorkMode.OFFLINE or not settings.risk_confirmed
                or report.mode != settings.mode or not report.can_offer_sync_enable):
            return None
        frozen = settings.revision, self.window.app_context.generation, self._request_serial

        def request():
            if self.is_transitioning or frozen != (
                self.policy.settings.revision, self.window.app_context.generation, self._request_serial,
            ):
                return
            self._report_sync_home_generation = frozen[1]
            # 复用工作台显式开关入口，重新预检，不拿已显示报告直接授权同步。
            callback()

        return request

    def cancel_report_sync_guidance(self):
        self._report_sync_home_generation = None

    def finish_report_sync_guidance(self, success):
        generation = getattr(self, "_report_sync_home_generation", None)
        self.cancel_report_sync_guidance()
        if not success or generation is None:
            return

        def show_workbench():
            if (not self._closed and self._navigate is not None
                    and generation == self.window.app_context.generation
                    and self.policy.settings.auto_sync_enabled and not self.policy.settings.paused):
                self._navigate("home")

        QTimer.singleShot(0, show_workbench)

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
              preview: bool = False, retry_deployment: bool = False) -> None:
        if self._closed or self._sync_activation_request is not None:
            return
        preview = preview or self.policy.machine_revalidation_required is True
        if not preview:
            self.cancel_report_sync_guidance()
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
                self._report_dialog = ModeReportDialog(
                    self.window, self, sync_action_provider=self.sync_enable_action_for_report,
                )
                self._report_dialog.finished.connect(self._dismiss_report)
            self._report_dialog.begin(self.policy.settings.mode.value, preview=preview)
            if self._controls:
                self._controls[1].setText("正在核对同步条件…" if preview else "正在检测并处理组件…")
        expected = self.policy.settings.revision, self.window.app_context.generation

        def perform():
            if self._closed:
                return None
            self.runtime.invalidate(retry_deployment=retry_deployment and not preview)
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
        self.cancel_report_sync_guidance()
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
                    pass  # 保存前已撤销进程内自动权限，保存失败不恢复副作用。
                self.refresh_controls()
        if dialog is not None:
            dialog.deleteLater()

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
            self.runtime.invalidate(retry_deployment=True)
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
        # 最终激活复用首轮的全部条件；文件仍在不代表路径与组件包仍有效。
        decision = (decide_sync_enable(self.policy.settings, self.policy.deployment_record, probe)
                    if probe is not None else None)
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
                pass  # revoke_first 已撤销进程内自动权限，保存失败不恢复副作用。
            self.refresh_controls()
        self.finish_report_sync_guidance(success)
        if self._report_dialog is not None:
            if success:
                self._report_dialog.accept()
            else:
                self._report_dialog.set_activation_result(
                    False,
                    (decision.detail if decision is not None and not decision.ready
                     else error or "同步偏好未能保存，请重新检测。"),
                )
