# 验证旧版组件清理结果能继续引导，等待与故障不越级。
from __future__ import annotations

from src.services.component_upgrade_guide import UpgradeEvidence
from src.features.settings.component_upgrade_dialog import ComponentUpgradeDialog
from test_work_mode_controller import controller, qt_app  # noqa: F401 - 复用同一离屏控制器契约夹具。


def test_upgrade_cleanup_continues_to_mode_step_after_result(controller, qt_app):
    c, _window, policy, _events, _popups, _probe = controller
    c._upgrade_flow_active = True
    c._upgrade_cleanup_resume = True
    c._upgrade_evidence = UpgradeEvidence("legacy", "旧组件待清理。", "", "native-capture")
    opened = []
    c._show_upgrade_when_idle = lambda: opened.append(c._upgrade_stage())

    def clean(**_kwargs):
        c.runtime.cleanup_detail = "旧组件已清理。"
        policy.set_cleanup_pending(False)

    c.runtime.cleanup = clean
    c.cleanup()
    c._observer.run_jobs()
    assert c._cleanup_dialog is not None
    assert c._cleanup_dialog.continue_button.isVisible()
    assert "继续升级引导" in c._cleanup_dialog.message.text()
    c._cleanup_dialog.continue_button.click()
    qt_app.processEvents()
    assert opened == ["mode"]
    assert c._cleanup_dialog is None


def test_upgrade_cleanup_waiting_does_not_advance(controller, qt_app):
    c, _window, _policy, _events, _popups, _probe = controller
    c._upgrade_flow_active = True
    c._upgrade_cleanup_resume = True
    c.runtime.cleanup = lambda **_kwargs: setattr(c.runtime, "cleanup_detail", "请先退出游戏。")
    c.cleanup()
    c._observer.run_jobs()
    assert not c._cleanup_dialog.continue_button.isVisible()
    c._cleanup_dialog.reject()
    qt_app.processEvents()
    assert not c._upgrade_cleanup_resume


def test_closing_success_result_does_not_force_reopen(controller, qt_app):
    c, _window, policy, _events, _popups, _probe = controller
    c._upgrade_flow_active = True
    c._upgrade_cleanup_resume = True
    opened = []
    c._show_upgrade_when_idle = lambda: opened.append(True)

    def clean(**_kwargs):
        c.runtime.cleanup_detail = "旧组件已清理。"
        policy.set_cleanup_pending(False)

    c.runtime.cleanup = clean
    c.cleanup()
    c._observer.run_jobs()
    c._cleanup_dialog.reject()
    qt_app.processEvents()
    assert opened == []
    assert not c._upgrade_cleanup_resume


def test_upgrade_steps_end_at_deployment(qt_app):
    dialog = ComponentUpgradeDialog(
        None, stage="deploy", detail="", method="native-capture",
        on_action=lambda *_args: None,
    )
    try:
        assert "部署当前组件" in dialog.steps.text()
        assert "启动后验证" not in dialog.steps.text()
        assert dialog.action.text() == "前往部署"
    finally:
        dialog.close()
