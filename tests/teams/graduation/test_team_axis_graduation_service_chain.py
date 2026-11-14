# 从真实DAO/静态资料到随附EXE及显式保存的合成全链路；不模拟伤害、组件、冻结输入或原始战报身份。
from concurrent.futures import CancelledError
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

from src.domain.teams.graduation.axis_graduation_configuration import configuration_seeds
from src.domain.teams.report_matching import ReportRange
from src.integrations.analysis_core.analysis_core_release import create_bundled_analysis_client
from src.services.teams.axis_graduation.service import TeamAxisGraduationService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.graduation.test_team_axis_graduation_configuration import configuration_case
from tests.teams.graduation.test_team_axis_graduation_configuration import target_for


NTE_TEST_TIER = "core"


def test_real_input_freezing_target_native_solver_history_and_current_update(tmp_path):
    _unused, inputs, team, unused_calls, original, _request = configuration_case(tmp_path)
    clients = []

    def factory(**kwargs):
        client = create_bundled_analysis_client(**kwargs)
        assert client is not None and client.supports_team_axis_graduation_fixed_axis_ratio
        clients.append(client)
        return client

    service = TeamAxisGraduationService(current_inputs=inputs,
        semantics_path=original["selector"]["semantics_path"], client_factory=factory)
    current = inputs.freeze_current(team)
    assert current.source_ready and not current.has_gaps
    configuration = service.load_configuration(team)
    target = target_for(configuration)
    axis = ReportRange(original["selector"]["battle_record_id"])
    assert clients == unused_calls == []  # 编辑资料只读真实来源，不创建分析client或开始求解。
    assert current.sha256 == inputs.freeze_current(team).sha256
    common = dict(target=target, rules=configuration.rules)
    task = service.prepare_benchmark(team, axis, **common, request_id="real-service-benchmark",
        seeds=configuration_seeds(target, configuration.rules), max_evaluations=2, max_millis=15000)
    assert task.current == current and task.payload["current"]["source_sha256"] == current.sha256
    benchmark = service.solve_benchmark(task)
    response = benchmark.response
    assert response["current"]["axis_evaluation"]["status"] == "complete"
    assert response["fixed_axis_graduation"]["ratio_percent"] is not None
    assert response["fixed_axis_graduation"]["axis_feasibility"] == "unverified"
    assert response["current_ratio_percent"] is None
    reference, snapshot = benchmark.reference, benchmark.axis_snapshot
    assert reference is not None
    with UserDataDao(inputs.database_path) as dao:
        assert dao.get_team_axis_selection(team.team_id) is None
        assert dao.list_team_axis_targets(team.team_id) == ()
    service.save_completed_result(benchmark, expected_target_revision=0, expected_selection_revision=0)
    selected, stored = service.selected_result(team)
    count = len(clients)
    history = service.load_history(team)
    assert history.selected_result == stored and len(clients) == count
    assert stored.reference == reference and selected.revision == 1
    validity = service.check_validity(team, evaluation_id=stored.evaluation_id, axis=axis, **common)
    assert validity.state == "context_matching"

    # 只改这一份隔离账号的实际档案并删除源战报；所选配装仍读自己的不可变来源快照。
    cid = team.character_ids[0]
    with UserDataDao(inputs.database_path) as dao:
        profile = deepcopy(next(row for row in task.payload["current"]["build"]["characters"]
                                if row["character_id"] == cid)["profile"])
        assert profile["likeability_level_10_enabled"] is False
        profile["likeability_level_10_enabled"] = True
        dao.save_character_profile(character_id=cid, **profile)
        assert dao.delete_battle_record(axis.record_id)
    changed = inputs.freeze_current(team)
    assert changed.sha256 != current.sha256 and not changed.has_gaps
    with pytest.raises(ValueError, match="当前构筑"):
        service.verify_preview(benchmark)
    assert service.selected_result(team) == (selected, stored)
    assert service.check_validity(team, evaluation_id=stored.evaluation_id, axis=axis, **common).state == "current_stale"
    followups = dict(reference=reference, axis_snapshot=snapshot, **common)
    current_task = service.prepare_current(team, axis, **followups, request_id="real-service-current")
    updated = service.evaluate_current(current_task)
    assert updated.reference == reference and updated.axis_snapshot == snapshot
    assert updated.request.current.sha256 == changed.sha256
    assert updated.response["fixed_axis_graduation"]["ratio_percent"] is not None
    service.save_completed_result(updated, expected_target_revision=1, expected_selection_revision=1)
    new_selected, new_stored = service.selected_result(team)
    assert new_selected.revision == 2 and new_stored.reference == reference
    assert new_stored.evaluation_id != stored.evaluation_id
    count = len(clients)
    history = service.load_history(team)
    assert len(history.evaluations) == 2 and len(clients) == count

    advice_task = service.prepare_advice(team, axis, **followups, request_id="real-service-advice")
    advice = service.evaluate_advice(advice_task)
    equipment_task = service.prepare_equipment_advice(team, axis, **followups, request_id="real-service-equipment")
    equipment = service.evaluate_equipment_advice(equipment_task)
    for preview in (advice, equipment):
        assert preview.reference == reference and preview.axis_snapshot == snapshot
        assert "budget" not in preview.request.payload and "seeds" not in preview.request.payload
        assert preview.request.current.sha256 == changed.sha256
    assert advice.response["recommendations"]["items"]
    assert equipment.response["equipment_recommendations"]["items"]
    assert service.selected_result(team) == (new_selected, new_stored)  # 建议没有隐式保存或改选中分母。
    count = len(clients)
    with pytest.raises(CancelledError):
        service.evaluate_equipment_advice(equipment_task, cancelled=lambda: True)
    assert len(clients) == count
    assert service.selected_result(team) == (new_selected, new_stored)
    for key, digest in original["source_sha256"].items():
        assert hashlib.sha256(Path(original["selector"][key]).read_bytes()).hexdigest() == digest

    result = {"fixture_kind": "synthetic_real_graduation_service_chain_v1",
        "current_input_sha256": current.sha256, "updated_input_sha256": changed.sha256,
        "benchmark_ratio": response["fixed_axis_graduation"]["ratio_percent"],
        "updated_ratio": updated.response["fixed_axis_graduation"]["ratio_percent"],
        "reference_damage": reference.to_payload()["payload"]["reference_damage"],
        "benchmark_id": reference.benchmark_id, "snapshot_sha256": snapshot.sha256,
        "cultivation_advice_count": len(advice.response["recommendations"]["items"]),
        "equipment_advice_count": len(equipment.response["equipment_recommendations"]["items"]),
        "original_source_deleted_in_synthetic_copy": True, "selected_revision": new_selected.revision,
        "mathematical_model_mocked": False, "axis_feasibility": "unverified"}
    output = os.environ.get("NTE_TEAM_AXIS_GRADUATION_SERVICE_CHAIN_RESULT")
    if output:
        path = Path(output).resolve()
        root = Path(__file__).resolve().parents[3] / "build/team-axis-graduation-service-chain-20261105"
        assert path.parent == root and path.name == "synthetic-service-chain-v1.json" and not path.exists()
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(result, ensure_ascii=False))


def test_default_search_budget_preserves_same_axis_and_never_replaces_selected_reference(tmp_path):
    _unused, inputs, team, _calls, original, _request = configuration_case(tmp_path)
    service = TeamAxisGraduationService(current_inputs=inputs, semantics_path=original["selector"]["semantics_path"])
    configuration = service.load_configuration(team)
    target = target_for(configuration)
    axis = ReportRange(original["selector"]["battle_record_id"])
    common = dict(target=target, rules=configuration.rules, seeds=configuration_seeds(target, configuration.rules))
    small_task = service.prepare_benchmark(team, axis, **common, request_id="search-budget-small",
                                           max_evaluations=2, max_millis=15000)
    small = service.solve_benchmark(small_task)
    assert small.reference is not None
    service.save_completed_result(small, expected_target_revision=0, expected_selection_revision=0)
    selected = service.selected_result(team)
    start = time.monotonic()
    deep_task = service.prepare_benchmark(team, axis, **common, request_id="search-budget-default",
                                          max_evaluations=256, max_millis=15000)
    deep = service.solve_benchmark(deep_task)
    wall_seconds = time.monotonic() - start
    assert deep.reference is not None and deep_task.current.sha256 == small_task.current.sha256
    assert deep.axis_snapshot == small.axis_snapshot
    old, new = (preview.reference.to_payload()["payload"] for preview in (small, deep))
    assert new["scoring_domain_sha256"] == old["scoring_domain_sha256"]
    assert new["reference_damage"] >= old["reference_damage"]
    assert deep.reference.benchmark_id != small.reference.benchmark_id
    search = new["search"]
    assert search["optimality"] == "bounded_best" and search["evaluation_budget"] == 256
    assert 1 <= search["evaluated_candidates"] <= 256 and search["time_budget_ms"] == 15000
    assert search["unquantified_candidates"] == 0
    assert service.selected_result(team) == selected  # 深搜仍是独立预览，不自动替换已保存的100%标尺。
    with UserDataDao(inputs.database_path) as dao:
        assert len(dao.list_team_axis_benchmarks(team.team_id)) == 1
    result = {"fixture_kind": "synthetic_graduation_default_budget_v1", "small_damage": old["reference_damage"],
        "default_damage": new["reference_damage"], "small_evaluations": old["search"]["evaluated_candidates"],
        "default_search": search, "wall_seconds_including_input_and_component_guards": wall_seconds,
        "same_current_axis_and_scoring_domain": True, "selected_reference_unchanged": True,
        "global_optimum_proven": False, "mathematical_model_mocked": False}
    output = os.environ.get("NTE_TEAM_AXIS_GRADUATION_SERVICE_CHAIN_RESULT")
    if output:
        path = Path(output).resolve().with_name("synthetic-default-budget-v1.json")
        root = Path(__file__).resolve().parents[3] / "build/team-axis-graduation-service-chain-20261105"
        assert path.parent == root and not path.exists()
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(result, ensure_ascii=False))


def test_real_first_time_dialog_target_solver_save_restore_and_equipment_advice(tmp_path):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QDialogButtonBox
    from src.features.teams.graduation.axis_graduation_controller import TeamAxisGraduationController
    from src.features.teams.graduation.axis_graduation_dialog import TeamAxisGraduationDialog
    from src.features.teams.graduation.axis_graduation_target_dialog import GraduationTargetDialog
    from tests.teams.graduation.test_team_axis_graduation_configuration import application
    from tests.teams.graduation.test_team_axis_graduation_configuration import cleanup
    from tests.teams.graduation.test_team_axis_graduation_configuration import wait_for

    application()
    _unused, inputs, team, _calls, original, _request = configuration_case(tmp_path)
    clients = []

    def factory(**kwargs):
        client = create_bundled_analysis_client(**kwargs)
        assert client is not None and client.supports_team_axis_graduation_benchmark
        clients.append(client)
        return client

    service = TeamAxisGraduationService(current_inputs=inputs,
        semantics_path=original["selector"]["semantics_path"], client_factory=factory)
    owner = TeamAxisGraduationController(service_provider=lambda: service)
    dialog = TeamAxisGraduationDialog(owner=owner, team=team)
    dialog.open()
    editor = None
    try:
        wait_for(lambda: not owner.is_running(), timeout=60)
        assert clients == [] and not dialog.benchmark.isEnabled()
        QTest.mouseClick(dialog.configure, Qt.LeftButton)
        wait_for(lambda: not owner.is_running() and any(
            w.isVisible() for w in dialog.findChildren(GraduationTargetDialog)), timeout=60)
        editor = next(w for w in dialog.findChildren(GraduationTargetDialog) if w.isVisible())
        QTest.mouseClick(editor.buttons.button(QDialogButtonBox.Ok), Qt.LeftButton)
        dialog.set_reference_axis(ReportRange(original["selector"]["battle_record_id"]))
        assert clients == [] and dialog.benchmark.isEnabled()
        assert dialog.evaluation_budget.value() == 256 and dialog.millis_budget.value() == 15000
        QTest.mouseClick(dialog.benchmark, Qt.LeftButton)
        wait_for(lambda: not owner.is_running(), timeout=60)
        assert owner.preview is not None and owner.can_save
        reference = owner.preview.reference
        ratio = owner.preview.response["fixed_axis_graduation"]["ratio_percent"]
        assert ratio is not None and f"{ratio:.2f}%" in dialog.headline.text()
        assert "可执行性未证实" in dialog.evidence.toPlainText()
        assert not dialog.equipment_recommend.isEnabled()  # 未保存首个基准，不让建议覆盖唯一待保存预览。
        QTest.mouseClick(dialog.save, Qt.LeftButton)
        wait_for(lambda: not owner.is_running(), timeout=60)
        assert owner.state == "saved_math" and dialog.equipment_recommend.isEnabled()
        selected_before = service.selected_result(team)
        assert selected_before[1].reference == reference
        count = len(clients)
        QTest.mouseClick(dialog.restore, Qt.LeftButton)
        wait_for(lambda: not owner.is_running(), timeout=60)
        assert len(clients) == count and owner.preview is None and dialog.current.isEnabled()
        QTest.mouseClick(dialog.validity, Qt.LeftButton)
        wait_for(lambda: not owner.is_running(), timeout=60)
        assert owner.state == "context_matching" and not owner.can_save
        QTest.mouseClick(dialog.equipment_recommend, Qt.LeftButton)
        wait_for(lambda: not owner.is_running(), timeout=60)
        assert owner.preview is not None and owner.preview.reference == reference
        assert owner.preview.request.payload["operation"] == "equipment_advice_evaluation"
        assert owner.preview.response["equipment_recommendations"]["items"]
        assert "理论" in dialog.advice.text() and owner.can_save
        assert service.selected_result(team)[1].reference == reference
        assert service.selected_result(team)[1].evaluation_id == selected_before[1].evaluation_id
        print(json.dumps({"fixture_kind": "synthetic_real_graduation_ui_chain_v1", "displayed_ratio": ratio,
            "explicit_target_benchmark_save_restore_and_equipment": True,
            "restoring_history_created_no_client": True, "reference_unchanged": True,
            "mathematical_model_mocked": False}, ensure_ascii=False))
    finally:
        if editor is not None and editor.isVisible():
            editor.reject()
        cleanup(owner, dialog)
