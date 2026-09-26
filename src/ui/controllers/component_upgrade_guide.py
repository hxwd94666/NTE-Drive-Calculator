# 将旧版组件证据投影为工作台升级引导。
from __future__ import annotations

from dataclasses import dataclass
import os

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from src.features.settings.component_upgrade_dialog import ComponentUpgradeDialog
from src.services.component_upgrade_guide import UpgradeEvidence, inspect_upgrade_evidence
from src.services.equipment_plugin_deployment import EquipmentPluginDeploymentError


@dataclass(frozen=True)
class UpgradeResult:
    revision: int
    generation: int
    evidence: UpgradeEvidence
    startup: bool


class ComponentUpgradeGuideMixin:
    def _resume_upgrade_after_cleanup_if_ready(self) -> None:
        if (not self._upgrade_cleanup_resume or not self._upgrade_cleanup_assessed
                or self._cleanup_dialog is not None):
            return
        self._upgrade_cleanup_resume = False
        self._upgrade_cleanup_assessed = False
        QTimer.singleShot(0, self._show_upgrade_when_idle)

    def _upgrade_progress_path(self):
        return self.window.app_context.paths.config_dir / "component_upgrade_pending"

    def _set_upgrade_progress(self, active: bool) -> None:
        path = self._upgrade_progress_path()
        if active:
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_text("1", encoding="ascii")
            os.replace(temporary, path)
        else:
            path.unlink(missing_ok=True)
        self._upgrade_flow_active = active

    def start_upgrade_guidance(self) -> None:
        """Assess old deployments after the normal first-run dialog has finished."""
        self._upgrade_flow_active = self._upgrade_progress_path().is_file()
        self.request_upgrade_assessment(startup=True)

    def _show_upgrade_when_idle(self) -> None:
        if self._closed:
            return
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(1000, self._show_upgrade_when_idle)
            return
        self.show_upgrade_guide()

    def request_upgrade_assessment(self, *, startup: bool = False) -> None:
        if self._closed:
            return
        settings = self.policy.settings
        generation = self.window.app_context.generation
        deployment = self.policy.deployment_record

        def perform():
            try:
                evidence = inspect_upgrade_evidence(
                    application_root=self.window.app_context.paths.root,
                    game_path=settings.game_executable, deployment=deployment,
                    loader=self.runtime.loader,
                )
            except (OSError, ValueError, EquipmentPluginDeploymentError) as error:
                evidence = UpgradeEvidence(
                    "path_unknown" if deployment else "none",
                    "旧组件核对遇到问题，请在环境设置中重新检测。",
                    type(error).__name__,
                    "loader" if deployment.get("loading_method") == "loader" else "native-capture",
                )
            return UpgradeResult(settings.revision, generation, evidence, startup)

        self._observer.submit(perform, key="upgrade-guide")

    def _upgrade_stage(self) -> str | None:
        evidence = self._upgrade_evidence
        if evidence is None:
            return None
        if evidence.kind == "path_unknown":
            return "path"
        if evidence.kind in {"legacy", "update"} or self.policy.settings.pending_cleanup and self._upgrade_flow_active:
            return "cleanup"
        if not self._upgrade_flow_active:
            return None
        settings = self.policy.settings
        if settings.paused:
            return "mode"
        if settings.mode.value in {"offline", "low"}:
            return "done"
        return None if evidence.kind == "ready" else "deploy"

    def _refresh_upgrade_banner(self) -> None:
        banner = getattr(self.window, "home_upgrade_banner", None)
        if banner is None:
            return
        stage = self._upgrade_stage()
        banner.setVisible(stage is not None)
        if stage is None:
            return
        summary = {
            "path": "旧组件升级：先确认游戏位置。",
            "cleanup": "旧组件升级：请先关闭游戏并清理旧部署。",
            "mode": "旧组件已清理：请重新确认工作模式。",
            "deploy": "工作模式已确认：请部署当前版本组件。",
            "done": "旧组件已清理；当前模式无需原生部署。",
        }
        self.window.home_upgrade_summary.setText(summary[stage])

    def show_upgrade_guide(self) -> None:
        if self._closed:
            return
        if self._upgrade_dialog is not None:
            self._upgrade_dialog.raise_()
            return
        stage = self._upgrade_stage()
        if stage is None:
            self.request_upgrade_assessment()
            return
        evidence = self._upgrade_evidence
        dialog = ComponentUpgradeDialog(
            self.window, stage=stage,
            detail=evidence.reason + "\n" + evidence.detail,
            method=evidence.method, on_action=self._upgrade_action,
        )
        self._upgrade_dialog = dialog
        dialog.finished.connect(lambda _result: self._dismiss_upgrade_dialog(dialog))
        dialog.show()

    def _dismiss_upgrade_dialog(self, dialog) -> None:
        if self._upgrade_dialog is dialog:
            self._upgrade_dialog = None
        dialog.deleteLater()

    def _upgrade_action(self, stage: str) -> None:
        if stage == "cleanup":
            try:
                self._set_upgrade_progress(True)
                self.policy.set_auto_sync_enabled(False)
                self.policy.set_component_auto_ready(False)
            except OSError:
                QMessageBox.warning(self.window, "旧版插件升级", "保存引导状态失败，请检查配置目录权限后重试。")
                return
            self._upgrade_cleanup_resume = True
            self._upgrade_cleanup_assessed = False
            self.cleanup()
        elif stage == "path":
            self.open_settings("game_path")
        elif stage == "done":
            self._set_upgrade_progress(False)
            self._refresh_upgrade_banner()
        elif stage == "mode":
            self.open_settings("mode")
        elif stage == "deploy":
            self.open_settings("loading_method")
