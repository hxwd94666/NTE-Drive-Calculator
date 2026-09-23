# 验证风险确认需要明确接受，取消与退出均不授予模式权限。
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFrame, QLabel, QPushButton
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
            assert consent.text() == "确认切换"
        else:
            assert consent.text() == "确认风险并切换"
        assert dialog.windowTitle() == "确认切换到" + {
            "offline": "离线", "low": "低风险", "medium": "中风险", "developer": "开发",
        }[mode] + "模式"
        assert dialog.findChild(QFrame, "workModeWarning") is not None
        assert buttons.layout().indexOf(cancel) < buttons.layout().indexOf(consent)
        assert cancel.isDefault() and not consent.isDefault()
        assert not consent.autoDefault()
        text = "\n".join(label.text() for label in dialog.findChildren(QLabel))
        assert {"可以使用", "不可使用", "切换后"} <= {
            label.text() for label in dialog.findChildren(QLabel)
        }
        assert ("鼠标或手柄扫描" in text and "极速装配" in text) if mode == "low" else True
        assert ("加载游戏组件" in text and "兼容问题" in text) if mode == "medium" else True
        assert "仅供开发人员使用" in text if mode == "developer" else True
        if mode == "offline":
            assert "停止采集与同步" in text and "清理已部署组件" in text
            assert "工作台开关控制" not in text
        else:
            assert "自动同步仍由工作台开关控制" in text and "后续版本更新继续沿用" in text
        assert "推荐使用" not in text and "暂未发现" not in text and "极低" not in text
        assert "暂停自动管理" not in text
        assert ("自动部署或更新" in text) == (mode in {"medium", "developer"})
        assert "双线战报对比" in text if mode == "developer" else True
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
    assert dialog.footer.indexOf(dialog.retry_button) < dialog.footer.indexOf(dialog.close_button)
    dialog.retry_button.click()
    controller.check.assert_called_once_with(show=True)
    interact(dialog)
    dispose(parent)
