# 验证模式检测的白名单诊断、逐域事实与同步引导边界。
from __future__ import annotations

from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from dataclasses import replace
from pathlib import Path
from src.domain.work_mode import (
    CheckState, FeatureCheck, NativeFeatureProbe, WorkMode, WorkModeProbe,
    WorkModeReport, WorkModeSettings,
)
from src.domain.work_mode import (
    CheckState, NativeFeatureProbe, WorkMode, WorkModeProbe, WorkModeSettings,
)
from src.domain.work_mode import CheckState, NativeFeatureProbe, WorkModeProbe
from src.features.settings.work_mode_card import ModeReportDialog
from src.integrations.nte_core_protocol import (
    NteCoreProcessError, NteCoreRequestContext, NteCoreRpcError, NteCoreTimeoutError,
)
from src.services.work_modes.runtime import WorkModeRuntime
from src.services.work_modes.service import WorkModeService
from src.services.work_modes.inspection import build_work_mode_report
from src.services.work_modes.inspection import detection_failure_detail
from src.services.work_modes.inspection import detection_failure_state
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from unittest.mock import Mock
import json
import os
import pytest


os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')


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
    assert detection_failure_state(NteCoreTimeoutError('native.snapshot.status', 12)) == CheckState.WARNING


def test_protocol_and_mapping_failures_remain_actionable():
    error = NteCoreRpcError({'code': -32000, 'data': {'domain_code': 'NATIVE_MAPPING_UNSUPPORTED'}})
    assert detection_failure_state(error) == CheckState.FAULT


def test_busy_and_game_readiness_are_not_reported_as_hard_failures():
    busy = NteCoreRpcError({'code': -32000, 'data': {'domain_code': 'REQUEST_IN_PROGRESS'}})
    waiting = NteCoreRpcError({
        'code': -32001, 'message': 'not_ready', 'data': {'reason': 'pawn_unavailable'},
    })
    assert detection_failure_state(busy) == CheckState.WARNING
    assert detection_failure_state(waiting) == CheckState.WAITING_LOGIN


@pytest.mark.parametrize('code,reason', [
    ('NATIVE_MAPPING_UNSUPPORTED', '映射'),
    ('NATIVE_SNAPSHOT_INCOMPLETE', '完整'),
    ('NATIVE_CAPABILITY_MISSING', '能力'),
    ('REQUEST_IN_PROGRESS', '请求'),
])
def test_business_failure_identifies_rpc_and_component_without_copying_payload(code, reason):
    error = NteCoreRpcError({'code': -32000, 'message': 'secret-user',
                            'data': {'domain_code': code, 'detail': 'uid=123456789'}})
    error.request_context = NteCoreRequestContext(
        'native.inventory.page', r'C:\private\nte-core.exe', 'a' * 64, True,
    )
    detail = detection_failure_detail(error)
    assert code in detail and reason in detail
    assert 'native.inventory.page' in detail and 'a' * 64 in detail
    assert '本次握手：已确认' in detail
    assert 'secret-user' not in detail and '123456789' not in detail
    assert 'C:\\private' not in detail


def test_local_failure_log_keeps_actual_component_path(monkeypatch):
    from src.utils.logger import logger

    records = []
    monkeypatch.setattr(logger, 'warning', lambda template, *args: records.append(template.format(*args)))
    error = NteCoreRpcError({'code': -32000, 'data': {'domain_code': 'NATIVE_MAPPING_UNSUPPORTED'}})
    error.request_context = NteCoreRequestContext(
        'native.inventory.page', r'C:\local-core\nte-core.exe', 'b' * 64, True,
    )
    detection_failure_detail(error, record=True)
    assert r'C:\local-core\nte-core.exe' in records[0]


def test_unknown_rpc_context_does_not_invent_handshake_or_copy_external_fields():
    error = NteCoreRpcError({'code': -32000, 'message': 'secret-user',
                            'data': {'domain_code': 'secret-user', 'detail': 'token=abc'}})
    error.request_context = NteCoreRequestContext('private-method', None, 'private-hash', False)
    detail = detection_failure_detail(error)
    assert '本次握手：已确认' not in detail
    assert all(value not in detail for value in ('secret-user', 'token=abc', 'private-method', 'private-hash'))


@pytest.mark.parametrize('code,reason', [
    ('control_timeout', '游戏内采集请求等待执行或完成超时'),
    ('not_ready', '游戏内采集接口尚未就绪'),
])
def test_native_control_failure_preserves_known_reason(code, reason):
    error = NteCoreRpcError({'code': -32001, 'message': code, 'data': {'detail': 'secret'}})
    detail = detection_failure_detail(error)
    assert reason in detail and f'原因码：{code}' in detail
    assert 'secret' not in detail
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
    from PySide6.QtWidgets import QWidget
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
        assert dialog.label.isHidden()
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


def test_detection_exception_is_only_in_folded_diagnostics():
    from auto_sync_ui_fixture import application, dispose
    from PySide6.QtWidgets import QLabel, QWidget
    from src.features.settings.work_mode_card import ModeReportDialog

    application()
    parent = QWidget()
    dialog = ModeReportDialog(parent, SimpleNamespace(check=Mock()))
    try:
        dialog.begin("low", preview=True)
        dialog.set_error("Traceback at private_path")
        assert "Traceback" not in dialog.preflight_summary.text()
        assert "Traceback" not in " ".join(
            label.text() for label in dialog.results.findChildren(QLabel)
        )
        assert "Traceback" in dialog.label.text()
        assert dialog.label.isHidden()
    finally:
        dispose(parent)


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def native_report(**changes):
    native = NativeFeatureProbe(files=True)
    probe = WorkModeProbe(
        analysis_available=True, component_update_state=CheckState.AVAILABLE,
        game_path_valid=True, core_available=True, input_available=True,
        native_load=native, native_battle=native, native_equipment=native,
        native_character=native, native_inventory=native, native_team=native,
        native_environment=native,
    )
    settings = WorkModeSettings(mode=WorkMode.MEDIUM, risk_confirmed=True, pending_cleanup=False)
    return build_work_mode_report(settings, replace(probe, **changes))


def test_verified_components_allow_sync_before_game_and_snapshots_are_ready():
    report = native_report()
    assert not report.ready
    assert report.can_offer_sync_enable
    checks = {item.feature: item for item in report.features}
    assert checks["native_inventory"].state == CheckState.WAITING


@pytest.mark.parametrize("changes", (
    {"native_load": NativeFeatureProbe(files=False)},
    {"native_load": NativeFeatureProbe(files=None)},
    {"native_inventory": NativeFeatureProbe(files=None)},
    {"core_available": False},
    {"core_available": None},
    {"game_path_valid": False},
    {"game_path_valid": None},
    {"analysis_available": False},
    {"component_update_state": CheckState.FAULT},
    {"component_update_state": CheckState.MISSING},
    {"component_update_state": CheckState.CLEANUP_PENDING},
    {"component_update_state": CheckState.WAITING},
    {"native_diagnostic": "本次业务检测尚未完成"},
))
def test_missing_or_unverified_conditions_do_not_offer_sync(changes):
    assert not native_report(**changes).can_offer_sync_enable


def test_missing_components_remain_a_shared_cause_with_deployment_action():
    native = NativeFeatureProbe(files=False)
    report = native_report(**{name: native for name in (
        "native_load", "native_battle", "native_equipment", "native_character",
        "native_inventory", "native_team", "native_environment",
    )})
    blocked = [item for item in report.features if item.state == CheckState.MISSING]
    assert len(blocked) == 7
    assert len({item.detail for item in blocked}) == 1
    assert all("manual_deploy" in item.actions for item in blocked)
    assert not report.can_offer_sync_enable


@pytest.mark.parametrize("npcap", (False, None))
def test_low_mode_requires_verified_capture_dependencies(npcap):
    settings = WorkModeSettings(mode=WorkMode.LOW, risk_confirmed=True, pending_cleanup=False)
    report = build_work_mode_report(settings, WorkModeProbe(
        analysis_available=True, core_available=True, input_available=True, npcap_available=npcap,
    ))
    assert not report.can_offer_sync_enable


def test_low_mode_allows_starting_listener_before_login():
    settings = WorkModeSettings(mode=WorkMode.LOW, risk_confirmed=True, pending_cleanup=False)
    report = build_work_mode_report(settings, WorkModeProbe(
        analysis_available=True, core_available=True, input_available=True, npcap_available=True,
    ))
    assert report.can_offer_sync_enable


@pytest.mark.parametrize("feature,state,facts", (
    ("sync", CheckState.MISSING, ()),
    ("sync", CheckState.FAULT, ()),
    ("sync", CheckState.CLEANUP_PENDING, ()),
    ("sync", CheckState.WARNING, ()),
    ("cleanup", CheckState.WAITING, ()),
    ("sync", CheckState.WAITING, (("inspection_incomplete", True),)),
    ("native_inventory", CheckState.WAITING, (("files", None),)),
))
def test_dialog_hides_sync_even_if_provider_would_offer_it(feature, state, facts):
    app = QApplication.instance() or QApplication([])
    parent = QWidget()
    provider = Mock(return_value=Mock())
    dialog = ModeReportDialog(parent, SimpleNamespace(check=Mock()), sync_action_provider=provider)
    try:
        dialog.begin("medium")
        report = WorkModeReport(WorkMode.MEDIUM, (
            FeatureCheck(feature, "同步条件", state, "请查看检测详情", facts=facts),
        ), sync_enable_ready=True)
        dialog.set_report(report)
        provider.assert_not_called()
        assert not any(button.text() == "开启自动同步" and not button.isHidden()
                       for button in dialog.findChildren(QPushButton))
    finally:
        dialog.close()
        parent.close()
        app.processEvents()


def test_unverified_report_defaults_to_not_offering_sync():
    report = WorkModeReport(WorkMode.MEDIUM, (
        FeatureCheck("sync", "同步", CheckState.WAITING, "待核对"),
    ))
    assert not report.can_offer_sync_enable


def test_login_wait_can_keep_sync_guidance_when_environment_is_verified():
    report = WorkModeReport(WorkMode.MEDIUM, (
        FeatureCheck("native_inventory", "DLL 完整背包库存", CheckState.WAITING_LOGIN,
                     "请登录并等待完整数据", facts=(("files", True),)),
    ), sync_enable_ready=True)
    assert report.can_offer_sync_enable


@pytest.mark.parametrize("domain_error", [None, "not_ready", "source_changed"])
@pytest.mark.parametrize("producer", [False, True])
def test_partial_native_fixture_does_not_block_ready_battle(tmp_path, domain_error, producer):
    fixture = json.loads((Path(__file__).resolve().parents[1] / "fixtures/native_snapshot_222.json").read_text(encoding="utf-8"))
    assert fixture["synthetic"] is True
    status = fixture["steps"][0]["response"]["result"]
    character = (fixture["producerSteps"][0] if producer else fixture["steps"][1])["response"]["result"]
    domains = [character if item["domain"] == "character" else item for item in status["domains"]]
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    policy.set_cleanup_pending(False)
    game = tmp_path / "HTGame.exe"
    game.write_bytes(b"synthetic game marker")
    policy.set_game_executable(str(game))
    dll_caps = frozenset(fixture["nativeHandshake"]["result"]["capabilities"])
    core_caps = ["native_hit_buff_v1", "native_context_observation_v1", "battle_axis_v1",
                 "character.snapshot.v1", "inventory.snapshot.v1", "native_inventory_dto_v1", "team.snapshot.v1", "environment.snapshot.v1"]
    native = SimpleNamespace(battle_active=False, close=MagicMock(), inspect=MagicMock(return_value={
        "hello": {"capabilities": core_caps}, "domains": {"domains": domains},
        "status": {"native_status": {"ready": True, "readyReason": "ready"}},
        "domain_errors": {"inventory": {"code": -32001, "message": domain_error, "reason": ""}} if domain_error else {},
    }))
    runtime = WorkModeRuntime(policy=policy, native_session=native,
        loader=SimpleNamespace(snapshot=lambda: SimpleNamespace(phase="stopped")), application_root=tmp_path, config_dir=tmp_path, game_running=lambda: True)
    runtime._bundle = SimpleNamespace(ready=True, layout="native-capture-v1", issues=(), native_capabilities=dll_caps)
    runtime._native_deployed = SimpleNamespace(files_compatible=True)
    with (patch.object(runtime, "_inspect_component_files"),
          patch("src.services.work_modes.runtime.resolve_nte_core_executable", return_value=game),
          patch("src.services.work_modes.runtime.create_bundled_analysis_client", return_value=None),
          patch("src.services.work_modes.runtime.native_capture_game_pid", return_value=1234),
          patch("src.services.work_modes.runtime.npcap_installation_present", return_value=False)):
        probe = runtime.tick(allow_connect=True)
    checks = {row.feature: row for row in policy.build_report(probe).features}
    assert checks["native_battle"].state == CheckState.AVAILABLE
    assert checks["native_character"].state == CheckState.MISSING
    assert checks["native_inventory"].state != CheckState.AVAILABLE
    if domain_error:
        assert checks["native_inventory"].state == CheckState.WAITING_LOGIN
        assert probe.native_inventory.handshake is True
        assert not probe.native_inventory.fault
    assert checks["history_analysis"].state == CheckState.MISSING
    assert "packet_capture" not in checks
    native.inspect.assert_called_once_with(refresh=True, check_equipment=True)


def test_ready_domains_do_not_depend_on_battle_login_projection(tmp_path):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    ready = NativeFeatureProbe(True, True, True, True, True, ready=True, complete=True, source_coverage="complete")
    probe = WorkModeProbe(game_path_valid=True, game_running=True, core_available=True,
        logged_in=False, native_character=ready,
        native_battle=replace(ready, ready=False, reason="scene_transition"))
    checks = {row.feature: row for row in policy.build_report(probe).features}
    assert checks["native_character"].state == CheckState.AVAILABLE
    assert checks["native_battle"].state == CheckState.WAITING


def test_manual_compatible_components_do_not_require_auto_bundle(tmp_path):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    probe = WorkModeProbe(game_path_valid=True, native_load=NativeFeatureProbe(files=True))
    checks = {row.feature: row for row in policy.build_report(probe).features}
    assert checks["native_load"].state == CheckState.AVAILABLE
    assert checks["component_update"].state == CheckState.WAITING
    probe = replace(probe, component_update_state=CheckState.MISSING, component_update_detail="未提供整套清单")
    checks = {row.feature: row for row in policy.build_report(probe).features}
    assert checks["native_load"].state == CheckState.AVAILABLE
    assert checks["component_update"].state == CheckState.MISSING


def test_formal_inventory_proof_is_distinct_from_raw_coverage(tmp_path):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    native = NativeFeatureProbe(files=True, pipe=True, handshake=True, supported=True,
                                snapshot=True, ready=True, complete=False, source_coverage="unknown",
                                projection_complete=True)
    probe = WorkModeProbe(game_path_valid=True, core_available=True, game_running=True,
                          native_inventory=native)
    check = next(row for row in policy.build_report(probe).features if row.feature == "native_inventory")
    assert check.state == CheckState.AVAILABLE
    assert dict(check.facts)["complete"] is False and dict(check.facts)["source_coverage"] == "unknown"
    stale = replace(probe, native_inventory=replace(native, projection_complete=False))
    assert next(row for row in policy.build_report(stale).features if row.feature == "native_inventory").state == CheckState.WAITING_LOGIN


def test_supported_sparse_character_fields_do_not_claim_full_character_observation(tmp_path):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    native = NativeFeatureProbe(files=True, pipe=True, handshake=True, supported=True,
                                snapshot=True, ready=True, complete=False, source_coverage="unknown",
                                profile_projection_supported=True)
    probe = WorkModeProbe(game_path_valid=True, core_available=True, game_running=True,
                          native_character=native)
    check = next(row for row in policy.build_report(probe).features if row.feature == "native_character")
    assert check.state == CheckState.AVAILABLE and "未观测字段保持原值" in check.detail
    assert dict(check.facts)["complete"] is False


@pytest.mark.parametrize("feature", ["native_team", "native_environment"])
def test_ready_observation_does_not_require_full_domain_coverage(tmp_path, feature):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    native = NativeFeatureProbe(files=True, pipe=True, handshake=True, supported=True,
                                snapshot=True, ready=True, complete=False, source_coverage="unknown")
    probe = WorkModeProbe(game_path_valid=True, core_available=True, game_running=True, **{feature: native})
    check = next(row for row in policy.build_report(probe).features if row.feature == feature)
    assert check.state == CheckState.AVAILABLE
    assert "已支持字段" in check.label and "已取得" in check.detail
    assert dict(check.facts)["complete"] is False
    assert dict(check.facts)["source_coverage"] == "unknown"
    assert getattr(probe, feature) == native


@pytest.mark.parametrize("feature", ["native_team", "native_environment"])
@pytest.mark.parametrize("change,state", [
    ({"snapshot": False}, CheckState.WAITING),
    ({"ready": False, "reason": "source_changed"}, CheckState.WAITING),
    ({"pipe": False}, CheckState.WAITING),
    ({"handshake": False}, CheckState.FAULT),
    ({"fault": "读取失败"}, CheckState.FAULT),
    ({"supported": False}, CheckState.MISSING),
])
def test_observation_still_requires_live_supported_data(tmp_path, feature, change, state):
    policy = WorkModeService(tmp_path / "mode.json")
    policy.select_mode("medium", risk_confirmed=True)
    native = NativeFeatureProbe(files=True, pipe=True, handshake=True, supported=True,
                                snapshot=True, ready=True, complete=False, source_coverage="unknown")
    probe = WorkModeProbe(game_path_valid=True, core_available=True, game_running=True,
                          **{feature: replace(native, **change)})
    check = next(row for row in policy.build_report(probe).features if row.feature == feature)
    assert check.state == state
