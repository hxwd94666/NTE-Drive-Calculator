# 验证直伤组收益均值与轴共享目标是一次原生作业，缺员与枚举截断不能伪装完整。
from __future__ import annotations

from copy import deepcopy
import json
from unittest.mock import Mock

import pytest

from tests.allocation.test_axis_postprocess_wire import request_fixture, response_fixture
from src.integrations.allocation_group_postprocess import postprocess_allocation_groups
from src.integrations.nte_analysis_core import NativeAnalysisError


NTE_TEST_TIER = "core"


def group_request(*, axis=False):
    old = request_fixture()
    allocation = old["allocation_request"]
    allocation.update(groups=[[0]], role_order=[0])
    request = {key: deepcopy(old[key]) for key in ("version", "input_identity", "allocation_request", "baseline_plans", "reserved_uids", "role_character_ids", "budget")}
    request.update(objective="frozen_axis_expected_v1" if axis else "standard_direct_v1", neighborhood="single_pair_exchange")
    if axis:
        request.update({key: deepcopy(old[key]) for key in ("battle_request", "anchor_mode", "equipment_value_mode", "baseline_equipment_by_character", "inventory_equipment_by_uid", "team_preflight")})
    else:
        request["role_models"] = {"A": {"model_id": "role_panel_standard_direct_v1", "baseline": {}, "item_values": {}, "crit_rate_cap": 1.0}}
    return request


def group_response(*, axis=False):
    response = response_fixture()
    response["batch_kind"] = "battle_axis_group_postprocess_v1" if axis else "allocation_group_postprocess_v1"
    group = {"group_index": 0, "members": [{"role": "A", "character_id": 1072}], "evaluations": 3, "rounds": 2,
             "improvements": 1, "termination": "local_optimum", "reason": "", "single_complete": True,
             "pair_complete": True, "exchange_complete": True, "quantification_complete": True, "neighborhood_complete": True}
    if axis:
        del response["scope"]["selected_character_id"]
        response["scope"]["selected_character_ids"] = [1072]
        response.update(ordering_policy="previous_single_exact_delta_then_uid", neighborhood="single_pair_exchange_fixed_signature")
        opt = response["optimization"]
        del opt["selected_role"]
        opt.update(shared_value=True, team_event_scope="all_frozen_axis_events_v1", candidate_visits=20, groups=[group],
                   selection_policy="whole_team_best_move_v1", exchange_links=[])
        group["enumeration_complete"] = True
    else:
        group["members"][0].update(baseline_value=100.0, candidate_value=110.0, relative_gain=.1)
        group.update(objective_value=.1, candidate_visits=20)
        response["optimization"] = {"objective": "standard_direct_v1", "model_id": "role_panel_standard_direct_v1",
            "neighborhood": "single_pair_exchange", "evaluations": 3, "rounds": 2, "candidate_visits": 20,
            "termination": "local_optimum", "neighborhood_complete": True, "groups": [group], "exchange_links": []}
    return response


def client(response):
    return Mock(dataset_version="fixture-static", engine_version="0.3.0", supports_allocation_group_postprocess=True,
        supports_battle_axis_group_postprocess=True, _run=Mock(return_value=json.dumps(response).encode("utf8")))


@pytest.mark.parametrize("axis", [False, True])
def test_group_uses_exact_one_job_and_distinct_objective_contract(axis):
    transport = client(group_response(axis=axis))
    result = postprocess_allocation_groups(transport, group_request(axis=axis), axis=axis)
    assert transport._run.call_count == 1
    assert result["optimization"]["groups"][0]["group_index"] == 0
    if axis:
        assert result["optimization"]["shared_value"] and "relative_gain" not in result["optimization"]["groups"][0]["members"][0]


def test_direct_mean_cannot_drop_missing_member_or_forge_gain():
    response = group_response()
    response["optimization"]["groups"][0]["objective_value"] = .2
    with pytest.raises(NativeAnalysisError):
        postprocess_allocation_groups(client(response), group_request())


def test_positive_group_mean_cannot_hide_a_member_below_initial_b0():
    request, response = group_request(), group_response()
    request["allocation_request"]["roles"].append({"name": "B"})
    request["allocation_request"].update(groups=[[0, 1]], role_order=[0, 1])
    request["role_character_ids"]["B"] = 1003
    request["role_models"]["B"] = deepcopy(request["role_models"]["A"])
    request["baseline_plans"]["B"] = {"valid": True, "score": 1.0}
    response["plans"]["B"] = deepcopy(request["baseline_plans"]["B"])
    members = response["optimization"]["groups"][0]["members"]
    members[0].update(candidate_value=90.0, relative_gain=-.1)
    members.append({"role": "B", "character_id": 1003, "baseline_value": 100.0, "candidate_value": 130.0, "relative_gain": .3})
    with pytest.raises(NativeAnalysisError):
        postprocess_allocation_groups(client(response), request)


@pytest.mark.parametrize("axis", [False, True])
def test_enumeration_budget_is_own_truncation_state_not_local_optimum(axis):
    response = group_response(axis=axis)
    opt = response["optimization"]
    opt.update(termination="enumeration_budget", neighborhood_complete=False, candidate_visits=1_000_000)
    group = opt["groups"][0]
    group.update(termination="enumeration_budget", neighborhood_complete=False, pair_complete=False)
    if axis:
        opt["enumeration_complete"] = group["enumeration_complete"] = False
    else:
        group["candidate_visits"] = 1_000_000
    result = postprocess_allocation_groups(client(response), group_request(axis=axis), axis=axis)
    assert result["optimization"]["termination"] == "enumeration_budget" and not result["optimization"]["neighborhood_complete"]


@pytest.mark.parametrize("identity", [{"A": -1}, {"A": 1072, "unknown": 1003}])
def test_malformed_given_role_identity_is_protocol_error_not_semantic_not_ready(identity):
    request = group_request(axis=True)
    request["role_character_ids"] = identity
    transport = client(group_response(axis=True))
    with pytest.raises(NativeAnalysisError):
        postprocess_allocation_groups(transport, request, axis=True)
    transport._run.assert_not_called()


@pytest.mark.parametrize("unquantified,gaps", [(1, {}), (0, {"formula_family_unsupported": 1}), (1, {"formula_family_unsupported": 1})])
def test_axis_local_optimum_rejects_unquantified_candidates_or_mechanism_gaps(unquantified, gaps):
    response = group_response(axis=True)
    response["optimization"].update(unquantified_candidates=unquantified, gaps=gaps)
    with pytest.raises(NativeAnalysisError):
        postprocess_allocation_groups(client(response), group_request(axis=True), axis=True)


@pytest.mark.parametrize("policy", [None, "priority_group_first"])
def test_axis_rejects_old_or_wrong_group_first_selection_policy(policy):
    response = group_response(axis=True)
    if policy is None:
        response["optimization"].pop("selection_policy")
    else:
        response["optimization"]["selection_policy"] = policy
    with pytest.raises(NativeAnalysisError):
        postprocess_allocation_groups(client(response), group_request(axis=True), axis=True)


def test_exchange_coverage_and_links_are_required_not_legacy_success():
    for axis in (False, True):
        response = group_response(axis=axis)
        del response["optimization"]["groups"][0]["exchange_complete"]
        with pytest.raises(NativeAnalysisError):
            postprocess_allocation_groups(client(response), group_request(axis=axis), axis=axis)
    response = group_response()
    response["optimization"]["exchange_links"] = [["A", "outside"]]
    with pytest.raises(NativeAnalysisError):
        postprocess_allocation_groups(client(response), group_request())


def test_exchange_save_requires_linked_groups_and_keeps_preview_identity():
    from unittest.mock import patch
    from src.services.allocation.direct_group_postprocess import (
        FrozenDirectGroupInputs, DirectGroupPreviewSource, verify_direct_group_save,
    )
    from src.services.allocation.postprocess_facts import (
        allocation_inputs_digest, allocation_row_save_projection, linked_save_dependencies,
    )
    groups = [{"members": [{"role": role}]} for role in ("A", "B", "C")]
    links = linked_save_dependencies(groups, [["A", "B"], ["B", "C"]])
    assert all(roles == ["A", "B", "C"] for roles in links.values())
    frozen = FrozenDirectGroupInputs((), (), (), (), allocation_inputs_digest([]), 1, "")
    rows = []
    for index, role in enumerate(("A", "B", "C")):
        meta = {"status": "completed", "objective": "standard_direct_v1", "model_id": "role_panel_standard_direct_v1",
                "dataset_id": "test", "source_snapshot_id": 1, "character_id": index + 1,
                "input_identity": {"request_id": "same-job"}, "group_index": index,
                "group_members": [{"role": role}], "save_dependencies": links[role]}
        row = {"role_name": role, "character_id": index + 1, "snapshot_id": 1,
               "payload": {"damage_optimization": meta}}
        row["direct_group_preview_source"] = DirectGroupPreviewSource(
            frozen, allocation_inputs_digest(meta), allocation_inputs_digest(allocation_row_save_projection(row)))
        rows.append(row)
    with patch("src.services.allocation.direct_group_postprocess.locked_allocation_owners", return_value=[]):
        verify_direct_group_save(Mock(), rows, dataset_id="test")
        with pytest.raises(RuntimeError, match="关联组"):
            verify_direct_group_save(Mock(), rows[:1], dataset_id="test")
        rows[0]["payload"]["damage_optimization"]["save_dependencies"] = ["A"]
        with pytest.raises(RuntimeError, match="冻结预览"):
            verify_direct_group_save(Mock(), rows[:1], dataset_id="test")


def projected_request_response():
    request, response = group_request(), group_response()
    request.pop("role_models")
    request.update(version=2, neighborhood="single_pair_exchange_blueprint", projection_inputs={
        "details": {"A": {"character": {"character_id": 1072}, "profile": {}, "growth_rows": [],
                           "forks": [], "shape_bonus": {}, "attributes": {}, "likeability_bonus": {}, "world_bonus": {}}},
        "inventory": {item["uid"]: {} for item in request["allocation_request"]["inventory"]}, "geometry_cells": {},
    })
    request["budget"].update(max_visits=100, max_millis=1000)
    response["version"] = 2
    response["optimization"].update(neighborhood="single_pair_exchange_blueprint",
        projection_policy="raw_official_panel_rust_v1", budget_policy="balanced_neighborhoods_v1", elapsed_millis=12)
    response["optimization"]["groups"][0]["blueprint_complete"] = True
    _add_native_layout(response)
    return request, response


def test_raw_projection_protocol_carries_policy_coverage_and_rejects_old_component_before_dispatch():
    request, response = projected_request_response()
    transport = client(response)
    transport.supports_allocation_group_projection = False
    with pytest.raises(NativeAnalysisError):
        postprocess_allocation_groups(transport, request)
    transport._run.assert_not_called()
    transport.supports_allocation_group_projection = True
    result = postprocess_allocation_groups(transport, request)
    assert result["optimization"]["projection_policy"] == "raw_official_panel_rust_v1"
    assert result["optimization"]["groups"][0]["blueprint_complete"]
    assert result["optimization"]["elapsed_millis"] == 12
    assert "role_models" not in json.loads(transport._run.call_args.args[0])["request"]


def test_raw_projection_protocol_rejects_forged_policy_and_incomplete_blueprint_coverage():
    request, response = projected_request_response()
    for field, value in (("projection_policy", "provided_model_v1"), ("budget_policy", "unknown"),
                         ("elapsed_millis", True), ("candidate_visits", 101)):
        bad = deepcopy(response)
        bad["optimization"][field] = value
        with pytest.raises(NativeAnalysisError):
            postprocess_allocation_groups(client(bad), request)
    bad = deepcopy(response)
    bad["optimization"]["groups"][0]["blueprint_complete"] = False
    with pytest.raises(NativeAnalysisError):
        postprocess_allocation_groups(client(bad), request)


def _add_native_layout(response):
    response["plans"]["A"].update(assigned_tape={"uid": "nte-core-1-1"}, assigned_set_drives=[],
        assigned_extra_drives=[{"uid": "nte-module-2-2", "shape_id": "H_2"}],
        native_layout={"version": 1, "tape_uid": "nte-core-1-1", "drives": [
            {"uid": "nte-module-2-2", "shape_id": "H_2", "target_row": 1, "target_column": 1}]})


def test_axis_rematch_protocol_requires_new_capability_budget_policy_and_native_coordinates():
    request, response = group_request(axis=True), group_response(axis=True)
    request.update(version=2, neighborhood="single_pair_exchange_blueprint")
    response.update(version=2, neighborhood="single_pair_exchange_blueprint")
    response["optimization"].update(budget_policy="balanced_neighborhoods_v1", elapsed_millis=20)
    response["optimization"]["groups"][0]["blueprint_complete"] = True
    _add_native_layout(response)
    transport = client(response)
    transport.supports_battle_axis_group_rematch = False
    with pytest.raises(NativeAnalysisError):
        postprocess_allocation_groups(transport, request, axis=True)
    transport._run.assert_not_called()
    transport.supports_battle_axis_group_rematch = True
    result = postprocess_allocation_groups(transport, request, axis=True)
    assert result["optimization"]["groups"][0]["blueprint_complete"]
    for key, value in (("target_column", True), ("target_row", 6), ("uid", "wrong")):
        bad = deepcopy(response)
        bad["plans"]["A"]["native_layout"]["drives"][0][key] = value
        with pytest.raises(NativeAnalysisError):
            postprocess_allocation_groups(client(bad), request, axis=True)
