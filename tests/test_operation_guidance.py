# 验证手动入口引导仅导航，取消无副作用且组件原因不被误作模式限制。
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox, QLabel, QWidget
from src.services.work_mode_service import WorkModeService
from src.ui.operation_guidance import allow_operation_entry, explain_operation_unavailable


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("capability", ["battle_capture", "game_sync", "packet_capture", "interface_input", "native_load", "native_equipment", "diagnostics"])
@pytest.mark.parametrize("action", ["settings", "cancel", "escape", "close"])
def test_denied_entry_never_changes_policy_or_continues(tmp_path, monkeypatch, app, capability, action):
    policy = WorkModeService(tmp_path / "mode.json")
    frozen = policy.settings
    parent = QWidget()
    routes = []

    def interact(dialog):
        buttons = dialog.findChild(QDialogButtonBox)
        cancel = buttons.button(QDialogButtonBox.Cancel)
        assert cancel.isDefault()
        text = dialog.findChild(QLabel).text()
        assert "当前离线模式" in text and "本次操作不会自动继续" in text
        if capability == "battle_capture":
            assert "低风险模式使用抓包战报" in text and "中风险模式使用 DLL 战报" in text
        if action == "settings":
            next(button for button in buttons.buttons() if button.text() == "前往设置").click()
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
    assert allow_operation_entry(parent, policy, capability, "测试功能", routes.append) is False
    assert routes == (["mode"] if action == "settings" else [])
    assert policy.settings == frozen and not (tmp_path / "mode.json").exists()
    parent.close()


def test_allowed_entry_does_not_prompt_or_probe(tmp_path, monkeypatch, app):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    monkeypatch.setattr(QDialog, "exec", lambda _self: pytest.fail("allowed mode must not prompt"))
    for capability in ("battle_capture", "game_sync", "native_load", "native_equipment", "interface_input"):
        assert allow_operation_entry(None, policy, capability, "测试功能", lambda _target: pytest.fail("must not navigate"))


def test_missing_component_keeps_true_reason_and_opens_deployment(tmp_path, monkeypatch, app):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    snapshot = (tmp_path / "mode.json").read_bytes()
    routes = []

    def interact(dialog):
        text = dialog.findChild(QLabel).text()
        assert "缺少配套采集 DLL" in text and "组件部署设置" in text
        assert "请选择低风险" not in text and "当前中风险模式不允许" not in text
        dialog.accept()
        return dialog.result()
    monkeypatch.setattr(QDialog, "exec", interact)
    explain_operation_unavailable(None, "原生连接", "缺少配套采集 DLL", routes.append, "deployment")
    assert routes == ["deployment"] and (tmp_path / "mode.json").read_bytes() == snapshot


@pytest.mark.parametrize("navigate", [False, True])
def test_only_explicit_navigation_closes_origin_feature_dialog(tmp_path, monkeypatch, app, navigate):
    parent = QWidget()
    parent.show()
    origin = QDialog(parent)
    origin.setModal(True)
    origin.show()
    app.processEvents()
    routes = []
    def interact(dialog):
        dialog.accept() if navigate else dialog.reject()
        return dialog.result()
    monkeypatch.setattr(QDialog, "exec", interact)
    policy = WorkModeService(tmp_path / "mode.json")
    assert not allow_operation_entry(parent, policy, "interface_input", "倒带执行", routes.append)
    assert origin.isVisible() is (not navigate)
    assert routes == (["mode"] if navigate else [])
    origin.close()
    parent.close()
