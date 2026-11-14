# 验证正式毕业基准协议的严格身份、未证实状态、受界传输及Rust合成响应，不复制伤害公式。
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
from concurrent.futures import CancelledError

import pytest

from src.domain.teams.graduation.axis_graduation import MODEL_ID, CRITICAL_POLICY, DAMAGE_UNIT
from src.domain.teams.graduation.axis_graduation_target import decode_graduation_target
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError, NteAnalysisCoreClient, RESPONSE_SCHEMA
from src.integrations.battle_analysis.graduation.team_axis_graduation import (
    BATCH_KIND, MAX_WIRE_BYTES, _decode, validate_request, validate_response,
)
from tests.teams.graduation.test_team_axis_graduation_target import rules as fixture_rules


NTE_TEST_TIER = "core"


def wire_fixture():
    target = decode_graduation_target((Path(__file__).resolve().parents[2] / "fixtures/team_axis_graduation_target_v1.json").read_text("utf8"))
    rules = asdict(fixture_rules())
    rules["items"] = list(rules["items"]) + [{"item_id": "M", "kind": "module", "quality": "Gold", "grid_count": 4,
        "geometry_id": "EquipmentGeometry_Test4", "suit_id": None, "max_level": 20, "sub_stat_count": 1, "intrinsic_main_stats": []}]
    actors, choices = [], []
    for member, layout in zip(target.presets[0].members, target.presets[0].layouts):
        profile = {"character_level": member.character_level, "breakthrough_stage": member.breakthrough_stage,
            "awakening_level": 0, "awakening_selection_initialized": True, "selected_awaken_effect_ids": [],
            "likeability_level_10_enabled": False, "fork_id": None, "fork_level": None,
            "fork_breakthrough_stage": None, "fork_refinement_level": None, "skill_levels": dict(member.skill_levels)}
        items, pieces = [], []
        for n, (pid, iid) in enumerate([(layout.core_piece_id, layout.core_item_id), *((p.piece_id, p.item_id) for p in layout.pieces)]):
            items.append({"kind": "core" if n == 0 else "module", "item_id": iid, "suit_id": "S" if n == 0 else None,
                "quality": "Gold", "geometry": None if n == 0 else "EquipmentGeometry_Test4", "grid_count": 0 if n == 0 else 4,
                "level": 0, "max_level": 20, "locked": False, "uid_slot": 0, "uid_serial": member.character_id*100+n,
                "stats": [{"stat_group": "main" if n == 0 else "sub", "property_id": "CritBase" if n == 0 else "AtkUp", "value": .05, "is_percent": True}]})
            pieces.append({"piece_id": pid, "item_id": iid, "main_property_id": "CritBase" if n == 0 else None, "sub_property_ids": ["AtkUp"]})
        actors.append({"character_id": member.character_id, "profile": profile, "equipment": items, "equipment_complete": True})
        choices.append({"character_id": member.character_id, "pieces": pieces})
    selector = {"account_id": "synthetic-current", "generation": 1, "battle_record_id": 1,
        "user_database_path": "F:/synthetic/user.sqlite3", "static_database_path": "F:/synthetic/static.sqlite3",
        "semantics_path": "F:/synthetic/semantics.json", "detail_level": "hit"}
    result = {"version": 2, "operation": "theoretical_benchmark", "request_id": "synthetic-request", "source_input_digest": "b"*64,
        "target_sha256": target.sha256, "battle_request": selector, "target": target.to_payload(), "rules": rules,
        "current": {"account_id": selector["account_id"], "generation": 1, "team_id": target.team_id,
            "character_ids": list(target.character_ids), "dataset_id": "fixture", "static_schema": 42,
            "static_sha256": "a"*64, "source_sha256": "d"*64,
            "world_bonus": {"yaodao_attack_add": 0, "quantao_crit_damage": 0}, "build": {"characters": actors}},
        "seeds": [{"preset_id": p.preset_id, "seeds": [deepcopy(choices)]} for p in target.presets],
        "budget": {"max_evaluations": 2, "max_millis": 60000}}
    result["component_provenance"] = {"component_executable_sha256": "1"*64, "component_manifest_sha256": "2"*64}
    result["current_provenance"] = {"team_revision": 1, "source_mode": "game", "source_revision": 1,
        "bindings": [], "world_source": "account", "members": [{"character_id": actor["character_id"],
            "slot_id": None, "plan_id": None, "snapshot_id": 1, "source_fingerprint": "a"*64,
            "field_sources": {key: "account" for key in actor["profile"]},
            "skill_level_sources": {key: "account" for key in actor["profile"]["skill_levels"]}} for actor in actors]}
    return json.loads(json.dumps(result))


def snapshot_fixture(request):
    # 仅合成协议夹具改写不透明正文；生产端只透传正文，由独立组件还原语义。
    value = json.loads((Path(__file__).resolve().parents[2] / "fixtures/team_axis_context_snapshot_v1.json").read_text("utf8"))
    value.update(account_id=request["current"]["account_id"],
        scope={k: request["battle_request"].get(k) for k in ("battle_record_id", "start_us", "end_us", "detail_scope")},
        source_input_digest=request["source_input_digest"])
    value.update({k: request["rules"][k] for k in ("dataset_id", "static_schema", "static_sha256")})
    facts = json.loads(value["payload_json"])
    facts["world_bonus"] = deepcopy(request["current"]["world_bonus"])
    value["payload_json"] = json.dumps(facts, ensure_ascii=False, separators=(",", ":"))
    value["payload_sha256"] = hashlib.sha256(value["payload_json"].encode("utf8")).hexdigest()
    meta = {k: v for k, v in value.items() if k not in {"payload_json", "snapshot_sha256"}}
    value["snapshot_sha256"] = hashlib.sha256(json.dumps(meta, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf8")).hexdigest()
    return value


def response_fixture(request, request_hash):
    snapshot = snapshot_fixture(request)
    domain = "e"*64
    def axis(damage):
        return {"model_id": MODEL_ID, "critical_policy": CRITICAL_POLICY, "unit": DAMAGE_UNIT, "scoring_domain_sha256": domain,
            "status": "complete", "damage": damage, "known_damage": damage, "total_events": 1, "quantified_events": 1,
            "global_gap_codes": [], "event_gaps": []}
    searches = [{"best_build": deepcopy(g["seeds"][0]), "best_damage": 200.0, "evaluated_candidates": 1, "cache_hits": 0,
        "unquantified_candidates": 0, "neighborhood_complete": False, "elapsed_ms": 1, "stop_reason": "evaluation_budget",
        "neighborhood": "single_property_and_top4_comparable_cross_actor_pairs_v1"} for g in request["seeds"]]
    equipment = []
    for actor, choice in zip(request["current"]["build"]["characters"], request["seeds"][0]["seeds"][0]):
        items = deepcopy(actor["equipment"])
        for item, piece in zip(items, choice["pieces"]):
            item.pop("uid_slot")
            item.pop("uid_serial")
            item["theoretical_piece_id"] = piece["piece_id"]
            item["level"] = 20
            item["stats"] = ([{"stat_group": "main", "property_id": piece["main_property_id"], "value": .3, "is_percent": True}]
                             if item["kind"] == "core" else []) + [
                {"stat_group": "sub", "property_id": key, "value": .05, "is_percent": True} for key in piece["sub_property_ids"]]
        equipment.append({"character_id": actor["character_id"], "equipment": items})
    target = request["target"]
    result = {"schema_version": RESPONSE_SCHEMA, "engine_version": "0.3.8", "dataset_version": "fixture", "batch_kind": BATCH_KIND,
        "version": 2, "operation": request["operation"], "request_id": request["request_id"], "request_sha256": request_hash,
        "source_input_digest": request["source_input_digest"], "target_sha256": request["target_sha256"],
        "model_id": MODEL_ID, "critical_policy": CRITICAL_POLICY, "unit": DAMAGE_UNIT,
        "status": "axis_feasibility_unverified", "current_ratio_percent": None,
        "axis_snapshot": snapshot,
        "current": {"source_sha256": request["current"]["source_sha256"], "input_sha256": "f"*64,
            "cultivation_sha256": "a"*64, "cultivation_validation": "static_cultivation_validated_v1",
            "axis_evaluation": axis(140), "axis_feasibility": "unverified",
            "target_qualification": {"policy_id": "team_axis_comparison_qualification_v1",
                "scope": "cultivation_and_world_caps_v1", "exact_domain_membership": "not_evaluated",
                "target_sha256": request["target_sha256"], "current_source_sha256": request["current"]["source_sha256"],
                "status": "within_target_conditions", "compatible_preset_ids": [p["preset_id"] for p in target["presets"]],
                "presets": [{"preset_id": p["preset_id"], "compatible": True, "issues": [], "issues_omitted": 0} for p in target["presets"]]}},
        "theory": {"target_id": target["target_id"], "team_id": target["team_id"], "target_revision": target["revision"],
            "target_sha256": request["target_sha256"], "scoring_domain_sha256": domain,
            "search_scope": "target_presets_bounded_v1", "budget_allocation_policy": "equal_remaining_preset_share_v1",
            "optimality": "bounded_best", "best": {"target": {"target_id": target["target_id"], "team_id": target["team_id"],
                "revision": target["revision"], "sha256": request["target_sha256"], "preset_id": request["seeds"][0]["preset_id"],
                "preset_count": 2}, "search_scope": "selected_preset_only", "search": searches[0], "axis_evaluation": axis(200),
                "unquantified_sample": None, "equipment": equipment, "layout_sha256": "a"*64, "cultivation_sha256": "b"*64,
                "cultivation_validation": "static_cultivation_validated_v1", "axis_feasibility": "unverified"},
            "presets": [{"preset_id": p["preset_id"], "allocated_evaluations": 1, "allocated_millis": 30000,
                "search": search, "unquantified_sample": None} for p, search in zip(request["seeds"], searches)],
            "evaluated_candidates": 2, "unquantified_candidates": 0, "cache_hits": 0, "evaluation_budget": 2,
            "time_budget_ms": 60000, "elapsed_ms": 2, "visited_presets": 2, "all_presets_visited": True,
            "neighborhood_complete": False, "stop_reason": "evaluation_budget", "axis_feasibility": "unverified"}}
    from src.domain.teams.graduation.axis_graduation_reference import seal_benchmark_reference
    theory = result["theory"]
    best = theory["best"]
    payload = {"account_id": request["current"]["account_id"], "component": request["component_provenance"],
        "scope": {key: request["battle_request"].get(key) for key in ("battle_record_id", "start_us", "end_us", "detail_scope")},
        "anchor_world": request["current"]["world_bonus"], "target_sha256": request["target_sha256"], "rules_sha256": "b"*64,
        "preset_id": best["target"]["preset_id"], "build": best["search"]["best_build"], "scoring_domain_sha256": domain,
        "replay_input_sha256": "c"*64, "layout_sha256": best["layout_sha256"], "cultivation_sha256": best["cultivation_sha256"],
        "axis_snapshot_sha256": snapshot["snapshot_sha256"], "origin_request_sha256": request_hash, "reference_damage": best["search"]["best_damage"], "engine_version": "0.3.8",
        "model_id": MODEL_ID, "critical_policy": CRITICAL_POLICY, "unit": DAMAGE_UNIT, "axis_feasibility": "unverified",
        "search": {key: theory[key] for key in ("search_scope", "optimality", "stop_reason", "evaluated_candidates",
            "unquantified_candidates", "evaluation_budget", "time_budget_ms", "elapsed_ms", "visited_presets", "neighborhood_complete")}}
    payload["search"]["total_presets"] = len(theory["presets"])
    result["benchmark_reference"] = seal_benchmark_reference(payload).to_payload()
    result["reference_verification_replays"] = 1
    return result


class Client:
    dataset_version = "fixture"
    engine_version = "0.3.8"
    component_provenance = {"component_executable_sha256": "1"*64, "component_manifest_sha256": "2"*64}
    supports_team_axis_graduation_current = True
    supports_team_axis_graduation_benchmark = True
    evaluate_team_axis_graduation = NteAnalysisCoreClient.evaluate_team_axis_graduation

    def __init__(self, mutate=None):
        self.mutate = mutate
        self.calls = 0
        self.verified = 0

    def _checkpoint(self, checkpoint):
        if checkpoint:
            checkpoint()

    def _verify_component(self, checkpoint):
        self._checkpoint(checkpoint)
        self.verified += 1

    def _run(self, payload, *, checkpoint):
        self.calls += 1
        response = response_fixture(json.loads(payload)["request"], hashlib.sha256(payload).hexdigest())
        if self.mutate:
            self.mutate(response)
        return json.dumps(response, allow_nan=False).encode("utf8")


def test_formal_transport_keeps_actual_and_theory_separate_without_publishing_percentage():
    client = Client()
    result = client.evaluate_team_axis_graduation(wire_fixture())
    assert result["current"]["axis_evaluation"]["damage"] == 140
    assert result["theory"]["best"]["axis_evaluation"]["damage"] == 200
    assert result["current_ratio_percent"] is None
    assert client.calls == client.verified == 1


@pytest.mark.parametrize("key,value", [("request_id", "wrong"), ("request_sha256", "0"*64), ("source_input_digest", "0"*64),
    ("target_sha256", "0"*64), ("current_ratio_percent", 70), ("status", "ready"), ("critical_policy", "observed"), ("version", True)])
def test_response_identity_or_false_readiness_is_rejected(key, value):
    with pytest.raises(NativeAnalysisError):
        Client(lambda r: r.update({key: value})).evaluate_team_axis_graduation(wire_fixture())


@pytest.mark.parametrize("change", [
    lambda r: r["current"].update(source_sha256="0"*64),
    lambda r: r["current"]["axis_evaluation"].update(scoring_domain_sha256="0"*64),
    lambda r: r["current"]["axis_evaluation"].update(damage=None),
    lambda r: r["current"]["axis_evaluation"].update(quantified_events=True),
    lambda r: r["theory"].update(evaluated_candidates=3),
    lambda r: r["theory"].update(optimality="global_optimal"),
    lambda r: r["theory"].update(target_revision=True),
    lambda r: r["theory"]["best"]["target"].update(revision=True),
    lambda r: r["theory"]["best"]["equipment"][0]["equipment"][0].update(level=0),
    lambda r: r["theory"]["best"]["search"]["best_build"][0]["pieces"][0].update(sub_property_ids=[]),
    lambda r: r["theory"]["best"]["equipment"][0]["equipment"][0].update(uid_serial=1),
])
def test_response_domain_counts_search_or_theoretical_uid_corruption_is_rejected(change):
    with pytest.raises(NativeAnalysisError):
        Client(change).evaluate_team_axis_graduation(wire_fixture())


def test_missing_capability_and_cancellation_do_not_dispatch_or_use_old_axis_model():
    client = Client()
    client.supports_team_axis_graduation_benchmark = False
    with pytest.raises(NativeAnalysisError):
        client.evaluate_team_axis_graduation(wire_fixture())
    assert client.calls == 0
    client.supports_team_axis_graduation_benchmark = True
    def cancel():
        raise CancelledError
    with pytest.raises(CancelledError):
        client.evaluate_team_axis_graduation(wire_fixture(), checkpoint=cancel)
    assert client.calls == 0


def test_wire_bounds_duplicate_fields_and_unknown_operation_fail_before_dispatch():
    for raw in [b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}', b' '*(MAX_WIRE_BYTES+1)]:
        with pytest.raises(NativeAnalysisError):
            _decode(raw)
    client = Client()
    request = wire_fixture()
    request["operation"] = "recommendation_evaluation"
    with pytest.raises(NativeAnalysisError):
        client.evaluate_team_axis_graduation(request)
    assert client.calls == 0


def test_dispatch_snapshot_does_not_follow_later_caller_mutation():
    request = wire_fixture()
    client = Client(lambda _result: request.update(request_id="changed-after-dispatch"))
    result = client.evaluate_team_axis_graduation(request)
    assert result["request_id"] == "synthetic-request" and request["request_id"] != result["request_id"]


def test_native_client_capability_is_explicit_and_old_manifest_stays_disabled(tmp_path):
    exe = tmp_path / "nte-analysis-core.exe"
    exe.write_bytes(b"synthetic fixture not executable")
    for caps, expected in [(None, False), (frozenset({"battle_axis_evaluate_v1"}), False),
                            (frozenset({"team_axis_graduation_benchmark_v2"}), True)]:
        client = NteAnalysisCoreClient(exe, "fixture", capabilities=caps)
        assert client.supports_team_axis_graduation_benchmark is expected


def test_controlled_rust_formal_router_response_passes_calc_validator():
    path = os.environ.get("NTE_TEAM_AXIS_GRADUATION_WIRE_RESULT")
    if not path:
        pytest.skip("explicit synthetic Rust router fixture required")
    value = json.loads(Path(path).read_text("utf8"))
    assert value["fixture_kind"] == "synthetic_graduation_wire_v1"
    raw = value["request_bytes"].encode("utf8")
    envelope = _decode(raw)
    validate_request(envelope["request"], envelope["dataset_version"])
    result = validate_response(value["response"], envelope["request"], dataset_version=envelope["dataset_version"],
                               engine_version="0.3.8", request_sha256=hashlib.sha256(raw).hexdigest())
    assert result["current"]["axis_evaluation"]["quantified_events"] == 3 and result["current_ratio_percent"] is None

@pytest.mark.parametrize("change", [
    lambda r: r["current_provenance"].update(source_revision=True),
    lambda r: r["current_provenance"]["members"][0]["field_sources"].update(character_level="template"),
    lambda r: r["current_provenance"]["members"][0].update(skill_level_sources={}),
    lambda r: r["current_provenance"].update(source_mode="slots"),
])
def test_provenance_unknown_template_and_stale_slot_metadata_stop_before_dispatch(change):
    request = wire_fixture()
    change(request)
    client = Client()
    with pytest.raises(NativeAnalysisError):
        client.evaluate_team_axis_graduation(request)
    assert client.calls == 0
