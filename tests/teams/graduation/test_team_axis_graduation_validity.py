# 验证只读有效性核对、选中来源通知和草稿展示撤权；合成client仅证明编排，不冒称实战伤害验收。
from concurrent.futures import CancelledError
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from src.domain.equipment.loadouts.plan_changes import SavedPlansChanged
from src.domain.teams.graduation.axis_graduation_validity import AxisPlanChangeScope
from src.domain.teams.overview import SourceMode
from src.domain.teams.models import TeamRole
from src.domain.teams.report_matching import ReportRange
from src.features.teams.graduation.axis_graduation_controller import AxisGraduationSelection, TeamAxisGraduationController
from src.features.teams.graduation.axis_graduation_dialog import TeamAxisGraduationDialog
from src.features.teams.controller import TeamController
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
from src.integrations.analysis_core.analysis_core_release import create_bundled_analysis_client
from src.services.teams.axis_graduation.service import TeamAxisGraduationService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.graduation.test_team_axis_graduation_history_ui import case
from tests.teams.graduation.test_team_axis_graduation_history_ui import cleanup
from tests.teams.graduation.test_team_axis_graduation_history_ui import wait_for
from tests.teams.graduation.test_team_axis_graduation_history_ui import application
from tests.teams.graduation.test_team_axis_graduation_history_ui import Gated
from tests.teams.graduation.test_team_axis_graduation_service import fixture
from tests.teams.graduation.test_team_axis_graduation_service import solve


NTE_TEST_TIER = "core"


def prepare(monkeypatch, tmp_path=None):
    if tmp_path is None:
        service, inputs, client, values, _ = fixture()
        team = inputs.current.team
    else:
        service, inputs, client, values, team = case(tmp_path)
    inputs.account_id, inputs.generation = inputs.current.account_id, inputs.current.generation
    # 本组只验证有效性编排；数值/合法词条口径由已有真实native专项负责。
    monkeypatch.setattr("src.services.teams.axis_graduation.validity.read_allocation_scoring_rules",
        lambda _path: SimpleNamespace(source_sha256=values["rules"].scoring_source_sha256,
                                      rule_sha256=values["rules"].scoring_rule_sha256))
    return service, inputs, client, values, team


def arguments(values):
    return dict(axis=ReportRange(1), target=values["target"], rules=values["rules"])


def test_matching_context_is_read_only_and_original_battle_changes_do_not_invalidate(monkeypatch):
    service, inputs, client, values, team = prepare(monkeypatch)
    preview = solve(service, inputs, values)
    previous = (len(client.operations), len(client.identity_calls), inputs.freezes)
    client.source = "f" * 64  # 旧战报被删/改不应要求重读；该结果已有正式冻结轴。
    result = service.check_validity(team, preview=preview, **arguments(values))
    assert result.state == "context_matching" and result.reasons == ()
    assert (len(client.operations), len(client.identity_calls), inputs.freezes) == (*previous[:2], previous[2] + 1)
    assert service.check_validity(team, preview=preview, **arguments(values)) == result


@pytest.mark.parametrize("change,expected", [("world", "current_stale"), ("source", "current_stale"),
    ("static", "benchmark_stale"), ("target", "benchmark_stale"), ("axis", "benchmark_stale")])
def test_changed_inputs_separate_current_from_benchmark(monkeypatch, change, expected):
    service, inputs, client, values, team = prepare(monkeypatch)
    preview = solve(service, inputs, values)
    args = arguments(values)
    if change == "world":
        world = inputs.current.world_bonus
        world[next(iter(world))] += 0.01
        inputs.current = replace(inputs.current, world_bonus_json=json.dumps(world))
    elif change == "source":
        inputs.current = replace(inputs.current, source=replace(inputs.current.source, revision=2))
    elif change == "static":
        inputs.current = replace(inputs.current, static_sha256="f" * 64)
    elif change == "target":
        args["target"] = replace(values["target"], revision=values["target"].revision + 1)
    else:
        args["axis"] = ReportRange(2)
    before = len(client.operations)
    result = service.check_validity(team, preview=preview, **args)
    assert result.state == expected and result.reasons
    assert len(client.operations) == before and preview.reference is not None


@pytest.mark.parametrize("mode,expected", [("missing", "component_pending"), ("capability", "component_pending"),
    ("hash", "benchmark_stale"), ("version", "benchmark_stale"), ("broken", "component_pending")])
def test_component_check_uses_metadata_without_starting_analysis(monkeypatch, mode, expected):
    service, inputs, client, values, team = prepare(monkeypatch)
    preview = solve(service, inputs, values)
    if mode == "capability":
        client.supports_team_axis_graduation_current = False
    elif mode == "hash":
        client.component_provenance = {**client.component_provenance, "component_executable_sha256": "f" * 64}
    elif mode == "version":
        client.engine_version = "synthetic_changed"
    def factory(**_kwargs):
        if mode == "broken":
            raise NativeAnalysisError("synthetic manifest mismatch")
        return None if mode == "missing" else client
    checker = TeamAxisGraduationService(current_inputs=inputs, semantics_path="synthetic.json", client_factory=factory)
    before = (len(client.operations), len(client.identity_calls))
    assert checker.check_validity(team, preview=preview, **arguments(values)).state == expected
    assert (len(client.operations), len(client.identity_calls)) == before


def test_rules_change_and_unverified_source_never_become_current(monkeypatch):
    service, inputs, _client, values, team = prepare(monkeypatch)
    preview = solve(service, inputs, values)
    monkeypatch.setattr("src.services.teams.axis_graduation.validity.read_allocation_scoring_rules",
        lambda _path: SimpleNamespace(source_sha256="f" * 64, rule_sha256="f" * 64))
    assert service.check_validity(team, preview=preview, **arguments(values)).state == "benchmark_stale"
    def missing(*_args, **_kwargs):
        raise ValueError("synthetic source incomplete")
    monkeypatch.setattr(inputs, "freeze_current", missing)
    result = service.check_validity(team, preview=preview, **arguments(values))
    assert result.current == "unverified" and result.benchmark == "stale"


def test_current_generation_and_foreign_account_are_rejected(monkeypatch):
    service, inputs, _client, values, team = prepare(monkeypatch)
    preview = solve(service, inputs, values)
    with pytest.raises(CancelledError):
        service.check_validity(team, preview=preview, cancelled=lambda: True, **arguments(values))
    inputs.generation += 1
    with pytest.raises(CancelledError):
        service.check_validity(team, preview=preview, **arguments(values))
    inputs.generation -= 1
    inputs.account_id = "different-account"
    with pytest.raises(CancelledError):
        service.check_validity(team, preview=preview, **arguments(values))


def test_saved_old_generation_and_new_unselected_target_leave_selected_reference_readable(tmp_path, monkeypatch):
    service, inputs, client, values, team = prepare(monkeypatch, tmp_path)
    old_selection, record = service.selected_result(team)
    service.save_target(team, replace(values["target"], revision=2), expected_revision=1)
    inputs.generation += 1
    inputs.current = replace(inputs.current, generation=inputs.generation)
    before = len(client.operations)
    result = service.check_validity(team, evaluation_id=record.evaluation_id, **arguments(values))
    assert result.state == "context_matching" and service.selected_result(team) == (old_selection, record)
    assert len(client.operations) == before
    with UserDataDao(inputs.database_path) as dao:
        assert dao.get_team_axis_target(team.team_id).revision == 2


@pytest.mark.parametrize("mode,slots,expected", [("game", (7,), False), ("current", (7,), True),
    ("current", (8,), False), ("slots", (8,), False), ("slots", (), True)])
def test_only_selected_plan_scope_is_affected(mode, slots, expected):
    scope = AxisPlanChangeScope(mode, ((1003, 7), (1004, 9)))
    event = SavedPlansChanged(0, "synthetic", "synthetic.sqlite3", (1003,), ("team",), slots)
    assert scope.affected(event) is expected
    assert not scope.affected(replace(event, character_ids=(9999,)))


def start_owner(service, inputs, values, team):
    application()
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    errors = []
    owner.error.connect(errors.append)
    try:
        owner.set_selection(AxisGraduationSelection(team, ReportRange(1), values["target"], values["rules"]))
        owner.load_history()
        wait_for(lambda: not owner.is_running())
        owner.run_benchmark(**{key: values[key] for key in ("seeds", "max_evaluations", "max_millis")})
        wait_for(lambda: not owner.is_running())
        assert owner.preview is not None, errors
    except BaseException:
        cleanup(owner)
        raise
    return owner


def test_owner_validity_stale_preview_recheck_and_benchmark_change_keep_old_math(tmp_path, monkeypatch):
    service, inputs, client, values, team = prepare(monkeypatch, tmp_path)
    owner = start_owner(service, inputs, values, team)
    try:
        preview = owner.preview
        assert owner.can_save and owner.check_validity()
        assert not owner.can_save  # 核对在途/被取消时不恢复旧保存权。
        wait_for(lambda: not owner.is_running())
        assert owner.state == "context_matching" and owner.can_save
        inputs.current = replace(inputs.current, source=replace(inputs.current.source, revision=2))
        owner.check_validity()
        wait_for(lambda: not owner.is_running())
        assert owner.current_stale and owner.can_update and not owner.can_save
        assert "当前评价过期" in owner.display.headline and owner.preview is preview
        count = len(client.operations)
        client.component_provenance = {**client.component_provenance, "component_manifest_sha256": "f" * 64}
        owner.check_validity()
        wait_for(lambda: not owner.is_running())
        assert owner.state == "benchmark_stale" and not owner.can_update and not owner.update_current()
        owner.invalidate_current(team.team_id)
        assert owner.state == "benchmark_stale" and not owner.can_update
        assert "基准需重建" in owner.display.headline and len(client.operations) == count
    finally:
        cleanup(owner)


def test_plan_notice_checks_session_and_draft_change_clears_old_ui(tmp_path, monkeypatch):
    service, inputs, _client, values, team = prepare(monkeypatch, tmp_path)
    inputs.current = replace(inputs.current, source=replace(inputs.current.source, mode=SourceMode.CALC,
        bindings=tuple((row.character_id, 7+i) for i, row in enumerate(inputs.current.members))),
        members=tuple(replace(row, slot_id=7 + i, plan_id=20 + i) for i, row in enumerate(inputs.current.members)))
    owner = start_owner(service, inputs, values, team)
    dialog = None
    try:
        event = SavedPlansChanged(inputs.generation, inputs.account_id, str(inputs.database_path),
            (team.character_ids[0],), (team.team_id,), (7,))
        for foreign in (replace(event, generation=event.generation + 1), replace(event, account_id="other"),
                        replace(event, database_path=str(tmp_path / "other.sqlite3")), replace(event, slot_ids=(999,))):
            owner.plans_saved(foreign)
            assert not owner.current_stale
        owner.plans_saved(event)
        assert owner.current_stale and "当前评价过期" in owner.display.headline
        dialog = TeamAxisGraduationDialog(owner=owner, team=team)
        dialog.open()
        wait_for(lambda: not owner.is_running())
        QTest.mouseClick(dialog.validity, Qt.LeftButton)
        wait_for(lambda: not owner.is_running())
        assert owner.state == "context_matching" and dialog.save.isEnabled()
        owner.set_selection(replace(owner.axis_selection, axis=ReportRange(2)))
        assert not owner.can_update and owner.preview is None
        assert "待建立或选用" in dialog.headline.text() and not dialog.damage.text()
        owner.load_history()  # 旧历史仍可读，但不冒充新草稿的比例。
        wait_for(lambda: not owner.is_running())
        assert "待建立或选用" in dialog.headline.text() and not dialog.damage.text()
    finally:
        cleanup(owner, dialog)


def test_cancelled_validity_cannot_regrant_save_or_publish_after_account_reset(tmp_path, monkeypatch):
    service, inputs, _client, values, team = prepare(monkeypatch, tmp_path)
    gate = Gated(service, "check_validity")
    owner = start_owner(gate, inputs, values, team)
    try:
        owner.check_validity()
        wait_for(gate.entered.is_set)
        owner.cancel()
        gate.release.set()
        wait_for(lambda: not owner.is_running())
        assert not owner.can_save and owner.current_stale
        owner.check_validity()
        owner.reset_account()
        inputs.active = False
        wait_for(lambda: not owner.is_running())
        assert owner.team is None and owner.display is None and not owner.can_save
    finally:
        cleanup(owner, gate=gate)


def test_real_bundled_factory_only_reads_metadata_and_never_launches_process(monkeypatch):
    _service, inputs, _client, values, team = prepare(monkeypatch)
    preview = solve(_service, inputs, values)
    inputs.static_database_path = Path(__file__).resolve().parents[3] / "data/game_static.sqlite3"
    def no_process(*_args, **_kwargs):
        raise AssertionError("validity check launched a process")
    monkeypatch.setattr("subprocess.Popen", no_process)
    checker = TeamAxisGraduationService(current_inputs=inputs, semantics_path="synthetic.json",
                                       client_factory=create_bundled_analysis_client)
    result = checker.check_validity(team, preview=preview, **arguments(values))
    # 随附组件经真实工厂校验；与测试伪组件身份不同，不重新签署该合成参照。
    assert result.benchmark == "stale" and result.component == "changed"


def test_reentrant_invalidation_does_not_emit_old_display_after_account_reset(tmp_path, monkeypatch):
    service, inputs, _client, values, team = prepare(monkeypatch, tmp_path)
    owner = start_owner(service, inputs, values, team)
    displays = []
    owner.display_ready.connect(displays.append)
    owner.state_changed.connect(lambda state: owner.reset_account() if state == "current_stale" else None)
    try:
        owner.invalidate_current(team.team_id)
        assert owner.team is None and owner.display is None and displays == []
    finally:
        cleanup(owner)


def test_failed_update_marks_retained_math_as_history_not_current(tmp_path, monkeypatch):
    service, inputs, client, values, team = prepare(monkeypatch, tmp_path)
    owner = start_owner(service, inputs, values, team)
    try:
        previous = owner.preview
        client.supports_team_axis_graduation_current = False
        assert owner.update_current()
        wait_for(lambda: not owner.is_running())
        assert owner.state == "component_pending" and owner.preview is previous
        assert owner.current_stale and not owner.can_save and "历史结果" in owner.display.headline
    finally:
        cleanup(owner)


def test_team_page_refresh_only_marks_history_and_changed_team_revokes_old_context(tmp_path, monkeypatch):
    service, inputs, client, values, team = prepare(monkeypatch, tmp_path)
    current_teams = [team]
    management = SimpleNamespace(list_teams=lambda: tuple(current_teams), checkpoint=lambda: None,
        roles=lambda: tuple(TeamRole(key, str(key)) for key in team.character_ids))
    controller = TeamController(service_provider=lambda: management, axis_graduation_service_provider=lambda: service)
    owner = controller.axis_graduation
    try:
        assert controller.refresh()
        owner.set_selection(AxisGraduationSelection(team, ReportRange(1), values["target"], values["rules"]))
        owner.load_history()
        wait_for(lambda: not owner.is_running())
        owner.run_benchmark(**{key: values[key] for key in ("seeds", "max_evaluations", "max_millis")})
        wait_for(lambda: not owner.is_running())
        before = len(client.operations)
        assert owner.preview is not None and controller.refresh()
        assert owner.current_stale and "历史结果" in owner.display.headline
        current_teams[0] = replace(team, name="改名后", revision=team.revision + 1)
        assert controller.refresh()
        assert owner.team == current_teams[0] and owner.preview is None and not owner.can_update
        assert len(client.operations) == before
    finally:
        cleanup(owner)
        controller.page.close()
        controller.page.deleteLater()
        controller.deleteLater()
