# 验证风险确认需要明确接受，取消与退出均不授予模式权限。
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton
from auto_sync_ui_fixture import application, dispose
from src.features.settings.work_mode_card import confirm_mode


@pytest.mark.parametrize("mode", ["low", "medium", "developer"])
@pytest.mark.parametrize("action", ["consent", "cancel", "escape", "close"])
def test_risk_confirmation_only_accepts_explicit_consent(monkeypatch, mode, action):
    app = application()
    dialogs = []

    def interact(dialog):
        dialogs.append(dialog)
        buttons = dialog.findChild(QDialogButtonBox)
        cancel = buttons.button(QDialogButtonBox.Cancel)
        consent = dialog.findChild(QPushButton, "workModeRiskConsent")
        assert consent.text() == "自愿承担风险并使用此模式\n同时接受后续更新依旧使用此模式"
        assert "#b42318" in consent.styleSheet() and "#ffffff" in consent.styleSheet()
        assert cancel.isDefault() and not consent.isDefault()
        assert not consent.autoDefault()
        text = dialog.findChild(QLabel).text()
        assert ("虚拟键盘鼠标" in text and "不推荐" in text) if mode == "low" else True
        assert ("游戏自身方法" in text and "推荐使用" in text) if mode == "medium" else True
        assert "请勿作为日常模式开启" in text if mode == "developer" else True
        assert "自动同步开启时会连接游戏并同步背包" in text and "可在首页随时关闭" in text
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


def test_offline_does_not_show_risk_dialog(monkeypatch):
    monkeypatch.setattr(QDialog, "exec", lambda _dialog: pytest.fail("offline must not prompt"))
    assert confirm_mode(None, "offline")


@pytest.mark.parametrize("installed", [False, True, None])
def test_low_mode_report_offers_download_only_for_confirmed_missing_npcap(tmp_path, monkeypatch, installed):
    from types import SimpleNamespace
    from unittest.mock import Mock
    from PySide6.QtWidgets import QWidget
    from src.domain.work_mode import WorkModeProbe
    from src.services.work_mode_service import WorkModeService
    from src.features.settings.work_mode_card import show_mode_report
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
    monkeypatch.setattr(QDialog, 'exec', interact)
    show_mode_report(parent, report, controller)
    dispose(parent)
