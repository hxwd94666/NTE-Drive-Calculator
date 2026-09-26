# 验证检测失败保留可分享诊断，并把共同故障与未完成的业务检查分开。
from types import SimpleNamespace
from unittest.mock import Mock
import os

import pytest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from src.domain.work_mode import (
    CheckState, NativeFeatureProbe, WorkMode, WorkModeProbe, WorkModeSettings,
)
from src.integrations.nte_core_protocol import (
    NteCoreProcessError, NteCoreRpcError, NteCoreTimeoutError,
)
from src.services.work_mode_checks import build_work_mode_report
from src.services.work_mode_diagnostics import detection_failure_detail


def test_known_identity_failure_has_actionable_reason():
    error = NteCoreProcessError(
        'Core 与游戏的 Windows 登录身份或权限不一致，请以与游戏相同的用户和权限启动 Calc。'
    )
    detail = detection_failure_detail(error)
    assert '权限' in detail and '相同' in detail
    assert 'peer_identity_mismatch' in detail


def test_timeout_keeps_method_and_duration_without_claiming_handshake_failed():
    detail = detection_failure_detail(NteCoreTimeoutError('native.snapshot.status', 12))
    assert 'native.snapshot.status' in detail and '12' in detail
    assert '握手失败' not in detail


def test_transport_failure_only_keeps_known_code_from_stderr():
    detail = detection_failure_detail(NteCoreProcessError(
        'private text', return_code=1,
        stderr_lines=['token=abc', 'error: native capture pipe_server_mismatch'],
    ))
    assert 'pipe_server_mismatch' in detail and 'Core 退出码：1' in detail
    assert 'private text' not in detail and 'token=abc' not in detail


@pytest.mark.parametrize('error', [
    RuntimeError('secret-user UID=123456789 C:\\private\\secret.json token=abc'),
    NteCoreRpcError({'code': -32001, 'message': 'secret-user',
                     'data': {'reason': 'secret-user', 'payload': 'token=abc'}}),
    NteCoreProcessError('secret-user', stderr_lines=['token=abc']),
])
def test_unknown_failures_do_not_copy_private_provider_text(error):
    detail = detection_failure_detail(error)
    for secret in ('secret-user', '123456789', 'private', 'token=abc'):
        assert secret not in detail
    assert '复制检测结果' in detail


def failed_report():
    native = NativeFeatureProbe(files=True, pipe=True)
    return build_work_mode_report(
        WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True, pending_cleanup=False),
        WorkModeProbe(
            game_path_valid=True, game_running=True, core_available=True,
            native_load=native, native_battle=native, native_equipment=native,
            native_character=native, native_inventory=native, native_team=native,
            native_environment=native,
            native_diagnostic=detection_failure_detail(NteCoreTimeoutError('core.hello', 10)),
        ),
    )


def test_shared_failure_does_not_claim_every_business_failed_handshake():
    checks = {item.feature: item for item in failed_report().features}
    assert checks['native_connection'].state == CheckState.FAULT
    assert checks['native_load'].state == CheckState.AVAILABLE
    for name in ('native_battle', 'native_equipment', 'native_inventory', 'native_character'):
        assert checks[name].state == CheckState.WAITING
        assert '本次未完成检测' in checks[name].detail
        assert dict(checks[name].facts)['handshake'] is None


def test_unconfirmed_or_unsupported_business_does_not_offer_deploy_action():
    from src.domain.work_mode import NativeFeatureProbe

    settings = WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True,
                                pending_cleanup=False)
    probe = WorkModeProbe(
        game_path_valid=True, core_available=True,
        native_load=NativeFeatureProbe(files=True),
        native_inventory=NativeFeatureProbe(files=None),
        native_character=NativeFeatureProbe(files=False, reason='packaged_capability_missing'),
    )
    checks = {item.feature: item for item in build_work_mode_report(settings, probe).features}
    assert checks['native_inventory'].state == CheckState.WAITING
    assert 'manual_deploy' not in checks['native_inventory'].actions
    assert checks['native_character'].state == CheckState.MISSING
    assert 'manual_deploy' not in checks['native_character'].actions


def test_unknown_game_path_does_not_offer_path_detection_as_a_fix():
    from src.domain.work_mode import NativeFeatureProbe

    settings = WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True,
                                pending_cleanup=False)
    probe = WorkModeProbe(game_path_valid=None, native_load=NativeFeatureProbe(files=None))
    checks = {item.feature: item for item in build_work_mode_report(settings, probe).features}
    assert checks['native_load'].state == CheckState.WAITING
    assert 'detect_game_path' not in checks['native_load'].actions


def test_missing_package_does_not_offer_unusable_deployment():
    from src.domain.work_mode import NativeFeatureProbe

    settings = WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True,
                                pending_cleanup=False)
    probe = WorkModeProbe(game_path_valid=True, core_available=False,
                          native_load=NativeFeatureProbe(files=False))
    checks = {item.feature: item for item in build_work_mode_report(settings, probe).features}
    assert checks['native_load'].state == CheckState.MISSING
    assert 'manual_deploy' not in checks['native_load'].actions


def test_missing_component_guides_to_selected_deployment_instead_of_forcing_d3d():
    from auto_sync_ui_fixture import application, dispose
    from PySide6.QtWidgets import QPushButton, QWidget
    from src.domain.work_mode import NativeFeatureProbe
    from src.features.settings.work_mode_card import ModeReportDialog

    app = application()
    parent = QWidget()
    controller = SimpleNamespace(check=Mock(), detect_path=Mock(), open_settings=Mock())
    report = build_work_mode_report(
        WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True, pending_cleanup=False),
        WorkModeProbe(game_path_valid=True, core_available=True,
                      native_load=NativeFeatureProbe(files=False)),
    )
    dialog = ModeReportDialog(parent, controller)
    try:
        dialog.begin('medium')
        dialog.set_report(report)
        buttons = {button.text(): button for button in dialog.findChildren(QPushButton)}
        assert '前往部署组件' in buttons
        buttons['前往部署组件'].click()
        app.processEvents()
        controller.open_settings.assert_called_once_with('deployment')
    finally:
        dispose(parent)


def test_report_can_be_copied_and_clears_stale_result_on_recheck():
    from auto_sync_ui_fixture import application, dispose
    from PySide6.QtWidgets import QFrame, QLabel, QWidget
    from src.features.settings.work_mode_card import ModeReportDialog

    app = application()
    parent = QWidget()
    parent._deploy_equipment_plugin = Mock()
    controller = SimpleNamespace(check=Mock(), detect_path=Mock())
    dialog = ModeReportDialog(parent, controller)
    try:
        dialog.begin('medium')
        assert not dialog.copy_button.isEnabled()
        dialog.set_report(failed_report())
        dialog.set_report(failed_report())  # A refreshed result must replace existing rows safely.
        assert dialog.overview.text().startswith('需处理')
        rows = dialog.results.findChildren(QFrame, 'modeReportFeatureRow')
        assert '原生连接与业务检测' in rows[0].findChild(QLabel).text()
        assert any('DLL 战报' in row.findChild(QLabel).text() and
                   'DLL 装配' in row.findChild(QLabel).text() for row in rows)
        assert dialog.label.isHidden()
        assert dialog.diagnostic_toggle.text() == '展开排查信息（开发/反馈用）'
        assert dialog.layout().itemAt(0).layout().indexOf(dialog.copy_button) >= 0
        assert dialog.footer.indexOf(dialog.copy_button) == -1
        dialog.diagnostic_toggle.click()
        assert dialog.label.isVisible()
        dialog.copy_button.click()
        copied = app.clipboard().text()
        assert 'core.hello' in copied and '检测时间' in copied
        assert 'DLL 战报：未完成检测' in copied
        assert '握手：未确认' in copied and '管道：是' in copied
        dialog.begin('medium')
        assert not dialog.copy_button.isEnabled()
        assert dialog.label.isHidden()
        dialog.set_error('检测未完成')
        dialog.copy_button.click()
        assert '检测未完成' in app.clipboard().text()
        assert 'core.hello' not in app.clipboard().text()
    finally:
        dispose(parent)


def test_guided_actions_stay_below_secondary_buttons_and_fill_dialog_width():
    from auto_sync_ui_fixture import application, dispose
    from PySide6.QtWidgets import QPushButton, QWidget
    from src.features.settings.work_mode_card import ModeReportDialog

    app = application()
    parent = QWidget()
    controller = SimpleNamespace(check=Mock(), detect_path=Mock())
    dialog = ModeReportDialog(parent, controller)
    try:
        dialog.begin('low')
        dialog._add_action('处理第一项', Mock())
        dialog._add_action('处理第二项', Mock())
        app.processEvents()
        actions = {button.text(): button for button in dialog.findChildren(QPushButton)}
        first, second = actions['处理第一项'], actions['处理第二项']
        first_top = first.mapTo(dialog, first.rect().topLeft()).y()
        second_top = second.mapTo(dialog, second.rect().topLeft()).y()
        footer_bottom = max(
            button.mapTo(dialog, button.rect().bottomLeft()).y()
            for button in (dialog.settings_button, dialog.retry_button, dialog.close_button)
        )
        assert footer_bottom < first_top < second_top
        assert first.width() >= dialog.width() * 0.8
        assert second.width() >= dialog.width() * 0.8
    finally:
        dispose(parent)
