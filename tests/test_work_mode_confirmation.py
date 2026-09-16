# 验证风险确认需要明确接受，取消与退出均不授予模式权限。
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton
from auto_sync_ui_fixture import application, dispose
from src.features.settings.work_mode_card import confirm_mode


@pytest.mark.parametrize("mode", ["offline", "low", "medium", "developer"])
@pytest.mark.parametrize("action", ["consent", "cancel", "escape", "close"])
def test_risk_confirmation_only_accepts_explicit_consent(monkeypatch, mode, action):
    app = application()
    dialogs = []

    def interact(dialog):
        dialogs.append(dialog)
        buttons = dialog.findChild(QDialogButtonBox)
        cancel = buttons.button(QDialogButtonBox.Cancel)
        consent = dialog.findChild(QPushButton, "workModeConfirm" if mode == "offline" else "workModeRiskConsent")
        if mode == "offline":
            assert consent.text() == "切换到离线模式"
            assert not consent.styleSheet()
        else:
            assert consent.text() == "自愿承担风险并使用此模式\n同时接受后续更新依旧使用此模式"
            assert "#b42318" in consent.styleSheet() and "#ffffff" in consent.styleSheet()
        assert cancel.isDefault() and not consent.isDefault()
        assert not consent.autoDefault()
        text = dialog.findChild(QLabel).text()
        assert ("模拟输入" in text and "极速装配等功能不可用" in text) if mode == "low" else True
        assert ("更新游戏中的对应状态" in text and "兼容问题" in text) if mode == "medium" else True
        assert "问题排查" in text if mode == "developer" else True
        if mode == "offline":
            assert "停止采集与同步" in text and "清理已部署" in text
            assert "自动同步开启时会连接游戏" not in text
        else:
            assert "自动同步开启时会连接游戏" in text and "可在首页随时关闭" in text
        assert "推荐使用" not in text and "暂未发现" not in text and "极低" not in text
        assert "暂停自动管理" not in text
        assert ("自动部署或更新" in text) == (mode in {"medium", "developer"})
        assert "双路对照" in text if mode == "developer" else True
        if action == "consent":
            consent.click()
        elif action == "cancel":
            cancel.click()
        else:
            dialog.show()
            app.processEvents()
            if action == "escape":
                QTest.keyClick(dialog, Qt.Key_Escape)
            else:
                dialog.close()
        return dialog.result()

    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        assert confirm_mode(None, mode) == (action == "consent")
    finally:
        for dialog in dialogs:
            dispose(dialog)


@pytest.mark.parametrize("installed", [False, True, None])
def test_low_mode_report_offers_download_only_for_confirmed_missing_npcap(tmp_path, monkeypatch, installed):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from PySide6.QtWidgets import QWidget
    from src.domain.work_mode import WorkModeProbe
    from src.services.work_mode_service import WorkModeService
    from src.features.settings.work_mode_card import ModeReportDialog
    application()
    parent = QWidget()
    parent._open_npcap_download = Mock()
    parent._deploy_equipment_plugin = Mock()
    controller = SimpleNamespace(detect_path=Mock(), check=Mock())
    policy = WorkModeService(tmp_path / 'mode.json')
    policy.select_mode('low', risk_confirmed=True)
    report = policy.build_report(WorkModeProbe(npcap_available=installed, core_available=False, game_path_valid=False))
    def interact(dialog):
        buttons = {b.text(): b for b in dialog.findChildren(QPushButton)}
        assert ('下载 Npcap' in buttons) == (installed is False)
        if installed is False:
            assert '安装完成后' in dialog.findChild(QLabel).text()
            buttons['下载 Npcap'].click()
            parent._open_npcap_download.assert_called_once()
        else:
            parent._open_npcap_download.assert_not_called()
        return 0
    dialog = ModeReportDialog(parent, controller)
    dialog.begin('low')
    dialog.set_report(report)
    assert dialog.close_button.width() == 72
    assert dialog.retry_button.width() == 88
    assert dialog.footer.indexOf(dialog.retry_button) < dialog.footer.indexOf(dialog.close_button)
    dialog.retry_button.click()
    controller.check.assert_called_once_with(show=True)
    interact(dialog)
    dispose(parent)
