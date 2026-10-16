# 验证轴评价一次冻结实际装备与账号文件身份，取消和过期结果不提交候选或派生快照。
from __future__ import annotations

from concurrent.futures import CancelledError
import hashlib
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from tests.battle_report.analysis.axis_evaluation_fixture import gear, wire_response
from src.integrations.nte_analysis_core import NativeAnalysisError, NteAnalysisCoreClient
from src.services.battle_report.analysis.axis_evaluation import freeze_actual_axis_equipment
from src.services.battle_report.analysis.load import BattleReportAnalysisLoadRequest
from src.services.battle_report.analysis.native_page import BattleNativePageService
from src.services.battle_report.persistence import BattleReportPersistenceDependencies


NTE_TEST_TIER = "core"


def owner(tmp_path, current=lambda _: True):
    user, static, semantics, executable = (tmp_path / name for name in
                                         ("user.sqlite3", "static.sqlite3", "semantics.json", "nte-analysis-core.exe"))
    for path in (user, static, semantics, executable):
        path.write_bytes(b"public-fixture")
    client = NteAnalysisCoreClient(executable, "fixture-static", capabilities=frozenset({"battle_axis_evaluate_v1", "battle_page_identity_v1", "battle_page_v1"}))
    client.load_battle_page_identity = Mock(return_value="a" * 64)
    dependencies = BattleReportPersistenceDependencies("fixture", user, 3, static)
    service = BattleNativePageService(client=client, dependencies=dependencies,
                                     semantics_path=semantics, context_is_current=current)
    return service, client, dependencies, semantics


def freeze(service, *, request=None, baseline=None, candidates=None):
    return service.freeze_axis_evaluation(request or BattleReportAnalysisLoadRequest(7),
        anchor_mode="original", selected_character_id=1072,
        baseline_equipment_by_character=baseline or [{"character_id": 1072, "equipment": gear()}],
        candidates=candidates or [{"candidate_id": "candidate-1", "equipment": gear(0.10)}])


def test_actual_equipment_projection_never_consumes_calculation_items():
    context = {"items": gear(0.05), "calculation_items": gear(0.90)}
    result = freeze_actual_axis_equipment(context)
    assert result[0]["stats"][0]["value"] == 0.05 and result[0]["level"] == 0
    context["items"][0]["stats"][0]["value"] = 0.20
    assert result[0]["stats"][0]["value"] == 0.05


def test_frozen_pointer_free_request_survives_caller_mutation_and_uses_one_native_batch(tmp_path):
    service, client, dependencies, _ = owner(tmp_path)
    baseline, candidates = [{"character_id": 1072, "equipment": gear()}], [{"candidate_id": "candidate-1", "equipment": gear(0.10)}]
    frozen = freeze(service, baseline=baseline, candidates=candidates)
    baseline[0]["equipment"][0]["stats"][0]["value"] = 0.90
    candidates[0]["equipment"][0]["stats"][0]["value"] = 0.99
    before = hashlib.sha256(dependencies.user_database_path.read_bytes()).hexdigest()

    def respond(payload, *, checkpoint):
        checkpoint()
        request = json.loads(payload)["request"]
        assert request["baseline_equipment_by_character"][0]["equipment"][0]["stats"][0]["value"] == 0.05
        assert request["candidates"][0]["equipment"][0]["stats"][0]["value"] == 0.10
        assert request["battle_request"]["account_id"] == "fixture" and request["battle_request"]["generation"] == 3
        assert request["equipment_value_mode"] == "actual_level"
        assert not any(key.startswith("marginal") for key in request["battle_request"])
        return json.dumps(wire_response(request)).encode("utf8")

    with patch.object(client, "_run", side_effect=respond) as run, \
            patch.object(service, "load", side_effect=AssertionError("page loop")), \
            patch("src.services.battle_report.analysis.native_page.persist_native_snapshot", side_effect=AssertionError("candidate persistence")):
        result = service.evaluate_axis(frozen)
    assert run.call_count == 1 and result["candidates"][0]["candidate_value"] == 125.0
    assert hashlib.sha256(dependencies.user_database_path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("resource", ["static", "semantics"])
def test_frozen_resource_changes_cancel_before_dispatch(tmp_path, resource):
    service, client, dependencies, semantics = owner(tmp_path)
    frozen = freeze(service)
    paths = {"static": dependencies.static_database_path, "semantics": semantics,
             "user": dependencies.user_database_path, "wal": Path(str(dependencies.user_database_path) + "-wal")}
    paths[resource].write_bytes(b"changed-public-fixture")
    with patch.object(client, "_run") as run, pytest.raises(CancelledError):
        service.evaluate_axis(frozen)
    run.assert_not_called()


def test_unrelated_database_and_wal_changes_do_not_invalidate_scoped_report(tmp_path):
    service, client, dependencies, _ = owner(tmp_path)
    frozen = freeze(service)
    dependencies.user_database_path.write_bytes(b"unrelated-ui-setting")
    Path(str(dependencies.user_database_path) + "-wal").write_bytes(b"unrelated-ui-setting")
    with patch.object(client, "_run", side_effect=lambda data, **_: json.dumps(wire_response(json.loads(data)["request"])).encode("utf8")):
        assert service.evaluate_axis(frozen)["baseline"]["status"] == "complete"


@pytest.mark.parametrize("when", ["before", "during"])
def test_scoped_report_or_active_a_change_cancels_frozen_request(tmp_path, when):
    service, client, _, _ = owner(tmp_path)
    frozen = freeze(service)
    if when == "before":
        client.load_battle_page_identity.return_value = "b" * 64
    def respond(data, **_):
        client.load_battle_page_identity.return_value = "b" * 64
        return json.dumps(wire_response(json.loads(data)["request"])).encode("utf8")
    with patch.object(client, "_run", side_effect=respond) as run, pytest.raises(CancelledError):
        service.evaluate_axis(frozen)
    assert run.call_count == (0 if when == "before" else 1)


def test_account_generation_and_external_cancellation_prevent_stale_result(tmp_path):
    state = {"current": True}
    service, client, _, _ = owner(tmp_path, current=lambda _: state["current"])
    frozen = freeze(service)
    state["current"] = False
    with patch.object(client, "_run") as run, pytest.raises(CancelledError):
        service.evaluate_axis(frozen)
    run.assert_not_called()
    state["current"] = True
    def cancelled():
        raise CancelledError
    with patch.object(client, "_run") as run, pytest.raises(CancelledError):
        service.evaluate_axis(frozen, checkpoint=cancelled)
    run.assert_not_called()


def test_missing_stat_value_and_marginal_edits_are_not_filled_as_zero_or_selector(tmp_path):
    service, client, _, _ = owner(tmp_path)
    equipment = gear()
    del equipment[0]["stats"][0]["value"]
    with patch.object(client, "_run") as run:
        with pytest.raises((ValueError, NativeAnalysisError)):
            freeze(service, baseline=[{"character_id": 1072, "equipment": equipment}])
        with pytest.raises((ValueError, NativeAnalysisError)):
            freeze(service, request=BattleReportAnalysisLoadRequest(7, marginal_drive_units=(("AtkUp", 0.1),)))
    run.assert_not_called()


def test_another_owner_cannot_consume_a_frozen_request(tmp_path):
    service, _, _, _ = owner(tmp_path)
    frozen = freeze(service)
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other, client, _, _ = owner(other_dir)
    with patch.object(client, "_run") as run, pytest.raises(CancelledError):
        other.evaluate_axis(frozen)
    run.assert_not_called()



def test_large_actual_inventory_projection_cancels_before_full_copy():
    import src.services.battle_report.analysis.axis_evaluation as module
    source = {"items": gear() * 80}
    cancelled = False
    original = module.freeze_equipment_context
    copied = 0

    def project(context):
        nonlocal cancelled, copied
        copied += len(context["items"])
        result = original(context)
        cancelled = True
        return result

    def checkpoint():
        if cancelled:
            raise CancelledError

    with patch.object(module, "freeze_equipment_context", side_effect=project):
        with pytest.raises(CancelledError):
            module.freeze_actual_axis_equipment(source, checkpoint=checkpoint)
    assert 0 < copied <= 64


def test_actual_inventory_batched_projection_matches_whole_and_never_uses_full_level_values():
    from src.services.battle_report.build.equipment import freeze_equipment_context
    source = {"items": gear(0.05) * 80, "calculation_items": gear(0.95) * 80}
    checkpoint = Mock()
    actual = freeze_actual_axis_equipment(source, checkpoint=checkpoint)
    assert actual == freeze_equipment_context({"items": source["items"]})
    assert all(item["level"] == 0 for item in actual)
    assert checkpoint.call_count > 1


@pytest.mark.parametrize("phase", ["inventory", "source"])
def test_axis_preparation_native_cancel_is_not_reported_as_model_unavailable(tmp_path, phase):
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    from src.integrations.nte_analysis_core import NativeAnalysisCancelled
    from src.services.allocation.axis_postprocess import freeze_axis_allocation_inputs
    from src.services.allocation.axis_selection import AxisAllocationSelection
    _service, client, dependencies, semantics = owner(tmp_path)
    release = SimpleNamespace(database_path=dependencies.static_database_path.resolve(), dataset_id=client.dataset_version)
    selection = AxisAllocationSelection(dependencies.account_id, dependencies.generation,
        dependencies.user_database_path.resolve(), release.database_path, release.dataset_id,
        "A", 1072, BattleReportAnalysisLoadRequest(7), "original", (1072,),
        team_source_input_digest="a" * 64, selected_team_character_ids=(1072,),
        team_inventory_snapshot_id=7, fixed_team_proof_json='{}')
    request = SimpleNamespace(role_order=("A",), roles_db={"A": {"character_id": 1072}})
    dao = MagicMock()
    dao.__enter__.return_value = dao
    dao.profile.return_value = {"account_id": dependencies.account_id}
    dao.list_current_loadout_equipment_owners.return_value = []
    dao.current_inventory_snapshot_id.return_value = 7
    cancelled = NativeAnalysisCancelled("cancelled by user")
    base = "src.services.allocation.axis_postprocess."
    with patch(base + "UserDataDao", return_value=dao), \
         patch(base + "freeze_actual_allocation_inventory", **({"side_effect": cancelled} if phase == "inventory" else {"return_value": {}})), \
         patch(base + "freeze_axis_source", side_effect=cancelled):
        with pytest.raises(CancelledError):
            freeze_axis_allocation_inputs(client, request, selection,
                user_database_path=dependencies.user_database_path,
                static_read_unit=SimpleNamespace(release=release),
                source_inventory=[{"official": {"snapshot_id": 7}}], semantics_path=semantics)
