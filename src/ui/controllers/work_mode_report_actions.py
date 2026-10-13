# 编排检测详情开启同步的显式入口和成功后的工作台导航。
from PySide6.QtCore import QTimer

from src.domain.work_mode import WorkMode


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
