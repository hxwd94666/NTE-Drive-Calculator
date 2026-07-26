# 从 MainWindow 抽离的控制器方法。
"""Compatibility-installed MainWindow controller."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QInputDialog,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
)

from src.app import runtime
from src.app.workers import WorkerThread
from src.services.equipment_plugin_deployment import (
    EquipmentPluginDeploymentError,
    deploy_plugin,
    find_game_executables,
    npcap_installation_present,
    packaged_plugin_dll,
    restore_plugin,
)
from src.services.dwmapi_diagnostics import (
    collect_dwmapi_diagnostics,
    format_dwmapi_diagnostics,
)
from src.services.nte_core_diagnostics import (
    collect_nte_core_diagnostics,
    format_nte_core_diagnostics,
)
from src.ui.main_window_method_install import install_methods as _install_main_window_methods

_METHOD_NAMES = ["_refresh_equipment_plugin_status","_select_equipment_plugin_game_executable","_detect_equipment_plugin_game_executable","_open_npcap_download","_show_npcap_status","_diagnose_nte_core","_show_nte_core_diagnostic_report","_diagnose_dwmapi","_show_dwmapi_diagnostic_report","_deploy_equipment_plugin","_restore_equipment_plugin","_focus_environment_configuration"]


def install_methods(app_module, window_cls) -> None:
    _install_main_window_methods(app_module, window_cls, _METHOD_NAMES, globals())


def _refresh_equipment_plugin_status(self):
    label = getattr(self, "_npcap_status_label", None)
    if label is not None:
        label.setText(
            "Npcap：已检测到" if npcap_installation_present()
            else "Npcap：未检测到（请选择官方安装程序安装）"
        )
    plugin_label = getattr(self, "_equipment_plugin_status_label", None)
    if plugin_label is None:
        return
    executable = getattr(self, "_equipment_plugin_game_executable_edit", None)
    bundle_label = getattr(self, "_equipment_plugin_bundle_label", None)
    if executable is None:
        return
    try:
        bundled_plugin = packaged_plugin_dll(runtime.ROOT)
        if bundle_label is not None:
            bundle_label.setText(f"打包插件：{bundled_plugin}")
    except EquipmentPluginDeploymentError:
        bundled_plugin = None
        if bundle_label is not None:
            bundle_label.setText("打包插件缺失：请重新安装完整应用包")
    if not executable.text().strip():
        plugin_label.setText("尚未选择 HTGame.exe")
    elif bundled_plugin is None:
        plugin_label.setText("应用根目录缺少打包的 dwmapi.dll，无法部署")
    else:
        plugin_label.setText("已选择游戏目录；部署前仍需确认")

def _select_equipment_plugin_game_executable(self):
    selected, _ = QFileDialog.getOpenFileName(
        self, "选择游戏主程序", "", "HTGame.exe (HTGame.exe)"
    )
    if selected:
        self._equipment_plugin_game_executable_edit.setText(selected)
        self._refresh_equipment_plugin_status()

def _detect_equipment_plugin_game_executable(self):
    current_worker = getattr(self, "_equipment_plugin_detection_worker", None)
    if current_worker is not None and current_worker.isRunning():
        return
    button = getattr(self, "_equipment_plugin_detect_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("正在检测…")
    worker = WorkerThread(target=find_game_executables, parent=self)
    self._equipment_plugin_detection_worker = worker

    def finish(candidates):
        if button is not None:
            button.setEnabled(True)
            button.setText("自动检测")
        choices = [str(path) for path in candidates]
        if not choices:
            QMessageBox.information(
                self,
                "检测游戏位置",
                "未自动找到 HTGame.exe。你可以手动填写或选择文件，定位步骤如下：\n\n"
                "1. 右键点击桌面游戏图标，选择“打开文件所在位置”。\n"
                "2. 进入 Client\\WindowsNoEditor\\HT\\Binaries\\Win64，找到 HTGame.exe。\n"
                "3. 右键点击 HTGame.exe，选择“复制文件地址”，再粘贴到游戏主程序方框。",
            )
            return
        selected = choices[0]
        if len(choices) > 1:
            selected, accepted = QInputDialog.getItem(
                self, "选择游戏位置", "检测到多个 HTGame.exe，请选择正在使用的游戏：",
                choices, 0, False,
            )
            if not accepted:
                return
        self._equipment_plugin_game_executable_edit.setText(selected)
        self._refresh_equipment_plugin_status()

    def failed(error):
        if button is not None:
            button.setEnabled(True)
            button.setText("自动检测")
        QMessageBox.warning(
            self,
            "检测游戏位置",
            f"自动检测失败：{error}\n\n"
            "你可以手动填写或选择文件：\n"
            "1. 右键点击桌面游戏图标，选择“打开文件所在位置”。\n"
            "2. 进入 Client\\WindowsNoEditor\\HT\\Binaries\\Win64，找到 HTGame.exe。\n"
            "3. 右键点击 HTGame.exe，选择“复制文件地址”，再粘贴到游戏主程序方框。",
        )

    worker.result_ready.connect(finish)
    worker.error.connect(failed)
    worker.start()

def _open_npcap_download(self):
    self._open_url("https://npcap.com/dist/npcap-1.88.exe")

def _show_npcap_status(self):
    if npcap_installation_present():
        QMessageBox.information(
            self, "Npcap 状态", "已检测到 Npcap，背包同步环境已满足该项依赖。"
        )
        return
    QMessageBox.warning(
        self,
        "Npcap 状态",
        "未检测到 Npcap。背包同步无法通过本地核心组件读取游戏数据；"
        "请点击“下载 Npcap 1.88”完成安装后再检测。",
    )


def _diagnose_nte_core(self):
    current_worker = getattr(self, "_nte_core_diagnostic_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "nte-core 诊断", "诊断正在进行，请稍候。")
        return
    button = getattr(self, "_nte_core_diagnostic_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("诊断中…")
    worker = WorkerThread(
        target=lambda: collect_nte_core_diagnostics(cwd=runtime.APP_DIR), parent=self
    )
    self._nte_core_diagnostic_worker = worker

    def finish(result):
        if button is not None:
            button.setEnabled(True)
            button.setText("诊断 nte-core")
        self._show_nte_core_diagnostic_report(format_nte_core_diagnostics(result))

    def failed(error):
        if button is not None:
            button.setEnabled(True)
            button.setText("诊断 nte-core")
        QMessageBox.warning(self, "nte-core 诊断", f"诊断程序执行失败：{error}")

    worker.result_ready.connect(finish)
    worker.error.connect(failed)
    worker.start()


def _show_nte_core_diagnostic_report(self, report: str):
    dialog = QDialog(self)
    dialog.setWindowTitle("nte-core 诊断结果")
    dialog.resize(720, 510)
    layout = QVBoxLayout(dialog)
    hint = QLabel("以下信息可直接复制后发送用于排查；本操作不会启动抓包或保存原始数据。")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    content = QPlainTextEdit(dialog)
    content.setReadOnly(True)
    content.setPlainText(report)
    layout.addWidget(content, 1)
    actions = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
    copy_button = actions.addButton("复制诊断", QDialogButtonBox.ActionRole)
    copy_button.clicked.connect(lambda: QApplication.clipboard().setText(report))
    actions.rejected.connect(dialog.reject)
    layout.addWidget(actions)
    dialog.exec()


def _diagnose_dwmapi(self):
    current_worker = getattr(self, "_dwmapi_diagnostic_worker", None)
    if current_worker is not None and current_worker.isRunning():
        QMessageBox.information(self, "dwmapi 诊断", "诊断正在进行，请稍候。")
        return
    executable_edit = getattr(self, "_equipment_plugin_game_executable_edit", None)
    executable = executable_edit.text().strip() if executable_edit is not None else ""
    button = getattr(self, "_dwmapi_diagnostic_button", None)
    if button is not None:
        button.setEnabled(False)
        button.setText("诊断中…")
    preferences = getattr(self, "_ui_preferences", {}) or {}
    worker = WorkerThread(
        target=lambda: collect_dwmapi_diagnostics(
            game_executable_path=executable,
            application_root=runtime.ROOT,
            recorded_deployed_sha256=str(
                preferences.get("equipment_plugin_deployed_sha256") or ""
            ),
        ),
        parent=self,
    )
    self._dwmapi_diagnostic_worker = worker

    def finish(result):
        if button is not None:
            button.setEnabled(True)
            button.setText("诊断 dwmapi")
        self._show_dwmapi_diagnostic_report(format_dwmapi_diagnostics(result))

    def failed(error):
        if button is not None:
            button.setEnabled(True)
            button.setText("诊断 dwmapi")
        QMessageBox.warning(self, "dwmapi 诊断", f"诊断程序执行失败：{error}")

    worker.result_ready.connect(finish)
    worker.error.connect(failed)
    worker.start()


def _show_dwmapi_diagnostic_report(self, report: str):
    dialog = QDialog(self)
    dialog.setWindowTitle("dwmapi 装备插件诊断结果")
    dialog.resize(760, 540)
    layout = QVBoxLayout(dialog)
    hint = QLabel("以下信息可直接复制后发送用于排查；本操作不会执行装备、复制或修改 DLL。")
    hint.setWordWrap(True)
    layout.addWidget(hint)
    content = QPlainTextEdit(dialog)
    content.setReadOnly(True)
    content.setPlainText(report)
    layout.addWidget(content, 1)
    actions = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
    copy_button = actions.addButton("复制诊断", QDialogButtonBox.ActionRole)
    copy_button.clicked.connect(lambda: QApplication.clipboard().setText(report))
    actions.rejected.connect(dialog.reject)
    layout.addWidget(actions)
    dialog.exec()

def _deploy_equipment_plugin(self):
    consent = getattr(self, "_equipment_plugin_consent", None)
    if consent is None or not consent.isChecked():
        QMessageBox.warning(self, "部署装备插件", "请先确认已获授权并理解这会修改所选游戏目录。")
        return
    executable = self._equipment_plugin_game_executable_edit.text().strip()
    try:
        source = packaged_plugin_dll(runtime.ROOT)
    except EquipmentPluginDeploymentError as exc:
        QMessageBox.warning(self, "部署装备插件", str(exc))
        return
    if QMessageBox.question(
        self,
        "确认部署装备插件",
        "将把应用打包的 dwmapi.dll 复制到所选 HTGame.exe 同目录。\n"
        "若目录已有同名文件，会先备份到当前账号数据目录。请先关闭游戏。\n\n"
        f"游戏：{executable}\n打包插件：{source}",
        QMessageBox.Yes | QMessageBox.No,
        QMessageBox.No,
    ) != QMessageBox.Yes:
        return
    try:
        deployed = deploy_plugin(
            game_executable_path=executable,
            plugin_dll_path=source,
            backup_directory=runtime.ACCOUNT_DATA_ROOT / "equipment_plugin_backups",
        )
        self._ui_preferences.update({
            "equipment_plugin_game_executable": str(deployed.game_executable),
            "equipment_plugin_dll_source": str(source),
            "equipment_plugin_backup_path": str(deployed.backup_path or ""),
            "equipment_plugin_deployed_sha256": deployed.deployed_sha256,
        })
        self._save_ui_preferences()
        self._equipment_plugin_status_label.setText("装备插件已部署；退出游戏前可在此还原。")
        QMessageBox.information(self, "部署装备插件", "已部署 dwmapi.dll，并已记录可恢复信息。")
    except EquipmentPluginDeploymentError as exc:
        QMessageBox.warning(self, "部署装备插件", str(exc))

def _restore_equipment_plugin(self):
    preferences = self._ui_preferences or {}
    executable = self._equipment_plugin_game_executable_edit.text().strip()
    deployed_sha256 = str(preferences.get("equipment_plugin_deployed_sha256") or "")
    if not executable or not deployed_sha256:
        QMessageBox.information(self, "还原装备插件", "当前账号没有可还原的部署记录。")
        return
    if QMessageBox.question(
        self, "还原装备插件", "将还原部署前备份的 dwmapi.dll；若没有备份，则只删除本程序部署的文件。",
        QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
    ) != QMessageBox.Yes:
        return
    try:
        restore_plugin(
            game_executable_path=executable,
            deployed_sha256=deployed_sha256,
            backup_path=preferences.get("equipment_plugin_backup_path"),
        )
        self._ui_preferences.update({
            "equipment_plugin_backup_path": "",
            "equipment_plugin_deployed_sha256": "",
        })
        self._save_ui_preferences()
        self._equipment_plugin_status_label.setText("已还原游戏目录中的 dwmapi.dll。")
        QMessageBox.information(self, "还原装备插件", "已完成还原。")
    except EquipmentPluginDeploymentError as exc:
        QMessageBox.warning(self, "还原装备插件", str(exc))

def _focus_environment_configuration(self):
    self._go("settings")
    scroll = getattr(self, "_settings_scroll", None)
    card = getattr(self, "_environment_configuration_card", None)
    if scroll is not None and card is not None:
        QTimer.singleShot(0, lambda: scroll.verticalScrollBar().setValue(card.y()))
