# 验证直伤后处理的冻结单位、原分数隔离与独立协议身份。
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from src.integrations.allocation_postprocess import postprocess_allocation
from src.integrations.nte_analysis_core import (
    ENGINE_VERSION, NativeAnalysisCancelled, NativeAnalysisError, NteAnalysisCoreClient,
)
from src.models.equipment import Drive, Tape
from src.optimizer.allocation_kernel import AllocationKernel, AllocationKernelRequest
from src.optimizer.scoring import ScoringEngine
from src.services.allocation.direct_postprocess import (
    direct_strategy_selection_error, optimization_payload,
)
from src.features.allocation.preference_modes import allocation_strategy_for_button, role_preference_mode_error


ROOT = Path(__file__).resolve().parents[2]
NTE_TEST_TIER = "core"


def fixture():
    roles = {"A": {"character_id": 101, "default_set": "Set", "weights": {"攻击力%": 1.0},
                   "board_matrix": [[1]], "crit_source_known": True,
                   "non_equipment_crit_rate": 0.0, "effective_fork_id": ""}}
    drives = tuple(Drive(uid=f"nte-module-{index}-10", quality="Gold", area=1,
                         shape_id="X", main_stats={"攻击力": 42.0, "生命值": 560.0},
                         sub_stats={"攻击力%": value}) for index, value in ((1, 6.0), (2, 3.0)))
    tape = Tape(uid="nte-core-3-30", quality="Gold", area=15, set_name="Set", suit_id="suit-1",
                main_stats="攻击力%", main_value=37.5, sub_stats={"暴击伤害%": 2.0})
    request = AllocationKernelRequest(
        inventory=(*drives, tape), roles_db=roles, sets_db={"Set": {"suit_id": "suit-1", "shapes": []}},
        shapes_db={}, blueprints_db={"A": [{"set_pieces": [], "extra_pieces": ["X"],
                                           "board": [], "set_effect_mode": "none"}]},
        role_order=("A",), strategy="role_priority", module_set_targets={}, set_effect_modes={},
        core_main_filters={}, core_set_targets={}, stat_priority_configs={}, property_limits={},
    )
    scorer = ScoringEngine(config_dir=ROOT / "config", roles_db=roles)
    plans = AllocationKernel(scorer).execute(request)
    raw_items = []
    for item in request.inventory:
        slot = int(item.uid.split("-")[2])
        serial = int(item.uid.split("-")[3])
        official = {
            "uid": {"slot": slot, "serial": serial}, "kind": "module" if item.item_type == "drive" else "core",
            "item_id": "item", "suit_id": None if item.item_type == "drive" else "suit-1",
            "geometry": "geometry-1" if item.item_type == "drive" else None, "grid_count": item.area,
            "quality": "orange", "snapshot_id": 7,
            "main_stats": [] if item.item_type == "drive" else [
                {"property_id": "AtkUp", "value": 0.10, "percent": True}],
            "sub_stats": [{"property_id": "AtkUp", "value": item.sub_stats["攻击力%"] / 100.0,
                           "percent": True}] if item.item_type == "drive" else [
                {"property_id": "CritDamageBase", "value": 0.02, "percent": True}],
        }
        raw_items.append({**item.model_dump(), "official": official})
    detail = {
        "character": {"character_id": 101, "element_type": "NATURE"},
        "profile": {"character_level": 80, "breakthrough_stage": 0, "fork_id": "",
                    "likeability_level_10_enabled": False},
        "growth_rows": [{"level": 80, "breakthrough_stage": 0,
                         "atk_base": 1000.0, "hp_base": 5000.0, "def_base": 400.0}],
        "forks": [], "world_bonus": {"yaodao_attack_add": 0.0, "quantao_crit_damage": 0.0},
        "shape_bonus": {"shape_label": "Type-1", "properties": [
            {"property_id": "AtkUp", "display_value": 5.0}]},
        "attributes": {"AtkUp": {"show_percent": True}, "CritDamageBase": {"show_percent": True}},
    }
    return request, scorer, plans, raw_items, detail


def inner_request():
    return {
        "version": 1, "objective": "standard_direct_v1",
        "input_identity": {"request_id": "fixture-job", "input_sha256": "a" * 64},
        "selected_role": "A", "allocation_request": {"version": 2}, "baseline_plans": {},
        "reserved_uids": [], "role_model": {"model_id": "role_panel_standard_direct_v1"},
        "budget": {"max_evaluations": 20, "max_rounds": 5},
    }


def response(request):
    return {
        "schema_version": "nte-analysis-response-v1", "engine_version": ENGINE_VERSION,
        "dataset_version": "fixture-static", "batch_kind": "allocation_postprocess_v1", "version": 1,
        "input_identity": deepcopy(request["input_identity"]), "plans": {},
        "optimization": {
            "objective": "standard_direct_v1", "model_id": "role_panel_standard_direct_v1",
            "selected_role": "A", "baseline_value": 10.0, "candidate_value": 12.0, "delta": 2.0,
            "evaluations": 3, "rounds": 1, "improvements": 1, "termination": "local_optimum",
            "reason": "", "neighborhood_complete": True,
        },
    }










def test_direct_selection_is_explicit_and_does_not_silently_choose_first():
    assert direct_strategy_selection_error("role_priority", ["A", "B"]) is None
    assert direct_strategy_selection_error("direct_priority", ["A"]) is None
    assert direct_strategy_selection_error("direct_priority", ["A", "B"]) is None
    assert direct_strategy_selection_error("direct_priority", [])
    assert [allocation_strategy_for_button(index) for index in (0, 1, 2)] == [
        "role_priority", "update_mode", "direct_priority"]
    assert role_preference_mode_error("direct_priority", {"A": ["攻击力%"]}, {"A": {}}, {"A": 90}) is None


def test_damage_metadata_is_separate_from_frozen_assignment_scores():
    plan = {"score": 84.0, "damage_optimization": response(inner_request())["optimization"]}
    before = deepcopy(plan)
    payload = optimization_payload(plan)
    assert payload["damage_optimization"]["candidate_value"] == 12.0
    assert "assignment_scores" not in payload and "score" not in payload
    assert plan == before
    assert optimization_payload({"score": 84.0}) == {}


def test_independent_protocol_validates_identity_and_keeps_dataset_envelope():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "nte-analysis-core.exe"
        path.touch()
        client = NteAnalysisCoreClient(path, "fixture-static",
                                       capabilities=frozenset({"allocation_postprocess_v1"}))
        request = inner_request()
        result = response(request)
        with patch.object(client, "_run", return_value=json.dumps(result).encode()) as run:
            assert postprocess_allocation(client, request) == result
        sent = json.loads(run.call_args.args[0])
        assert sent["batch_kind"] == "allocation_postprocess_v1"
        assert sent["dataset_version"] == "fixture-static"
        assert sent["request"] == request
        for field, value in (("dataset_version", "other"), ("version", True),
                             ("input_identity", {"request_id": "new", "input_sha256": "a" * 64})):
            invalid = {**result, field: value}
            with patch.object(client, "_run", return_value=json.dumps(invalid).encode()), \
                    pytest.raises(NativeAnalysisError):
                postprocess_allocation(client, request)


def test_protocol_rejects_nonfinite_or_inconsistent_objective_and_missing_capability():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "nte-analysis-core.exe"
        path.touch()
        client = NteAnalysisCoreClient(path, "fixture-static",
                                       capabilities=frozenset({"allocation_postprocess_v1"}))
        request = inner_request()
        for changes in ({"candidate_value": float("nan")}, {"candidate_value": 9.0},
                        {"delta": 7.0}, {"evaluations": True}, {"selected_role": "B"},
                        {"termination": "evaluation_budget", "neighborhood_complete": True}):
            invalid = response(request)
            invalid["optimization"].update(changes)
            with patch.object(client, "_run", return_value=json.dumps(invalid).encode()), \
                    pytest.raises(NativeAnalysisError):
                postprocess_allocation(client, request)
        unsupported = NteAnalysisCoreClient(path, "fixture-static", capabilities=frozenset())
        with patch.object(unsupported, "_run") as run, pytest.raises(NativeAnalysisError):
            postprocess_allocation(unsupported, request)
        run.assert_not_called()


def test_cancel_before_postprocess_never_spawns_and_unknown_metadata_is_removed():
    with TemporaryDirectory() as directory:
        path = Path(directory) / "nte-analysis-core.exe"
        path.touch()
        client = NteAnalysisCoreClient(path, "fixture-static",
            capabilities=frozenset({"allocation_postprocess_v1"}))
        def cancel():
            raise NativeAnalysisCancelled("public cancellation")
        with patch.object(client, "_run") as run, pytest.raises(NativeAnalysisCancelled):
            postprocess_allocation(client, inner_request(), checkpoint=cancel)
        run.assert_not_called()
        result = response(inner_request())
        result["optimization"]["unvalidated_payload"] = {"public_unknown": [1, 2, 3]}
        with patch.object(client, "_run", return_value=json.dumps(result).encode()):
            decoded = postprocess_allocation(client, inner_request())
        assert "unvalidated_payload" not in decoded["optimization"]
        assert "unvalidated_payload" not in optimization_payload({
            "damage_optimization": {**decoded["optimization"], "unvalidated_payload": "not persisted"}
        })["damage_optimization"]






