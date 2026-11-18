# 以真实Rust合成响应核对装备建议的合法词条预算、独立联合重放、未知语义及旧组件隔离。
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

import pytest

from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError, NteAnalysisCoreClient
from src.integrations.battle_analysis.graduation.team_axis_graduation import validate_request, validate_response
from tests.teams.graduation.test_team_axis_graduation_wire import Client
from tools.release.app_bundle_validation import validate_analysis_component

NTE_TEST_TIER = "core"


@pytest.fixture(scope="module")
def exported():
    path = os.environ.get("NTE_TEAM_AXIS_GRADUATION_EQUIPMENT_ADVICE_RESULT")
    if not path:
        pytest.skip("explicit synthetic Rust equipment advice fixture required")
    value = json.loads(Path(path).read_text("utf8"))
    assert value["fixture_kind"] == "synthetic_equipment_advice_v1"
    return value


def validate(exported, result=None):
    raw = exported["request_bytes"].encode("utf8")
    root = json.loads(raw)
    validate_request(root["request"], root["dataset_version"])
    return validate_response(result if result is not None else deepcopy(exported["response"]), root["request"],
        dataset_version=root["dataset_version"], engine_version="0.3.8", request_sha256=hashlib.sha256(raw).hexdigest())


def test_actual_native_equipment_advice_is_legal_reference_bound_and_not_inventory_or_target_growth(exported):
    result = validate(exported)
    root = json.loads(exported["request_bytes"])
    advice = result["equipment_recommendations"]
    assert advice["evaluated_candidates"] == advice["attempted_candidates"] == 37
    assert result["benchmark_reference"] == root["request"]["reference"]
    assert result["axis_snapshot"] == root["request"]["axis_snapshot"]
    assert result["reference_verification"]["search_evaluations"] == 0 and result["current_ratio_percent"] is None
    assert {i["kind"] for i in advice["items"]} == {"combined_equipment", "member_equipment", "piece_equipment"}
    assert all("uid_serial" not in i and "uid_slot" not in i for a in advice["target_equipment"] for i in a["equipment"])
    assert all(i["impact"]["graduation_percentage_points"] is None for i in advice["items"])
    joint, = (i for i in advice["items"] if i["kind"] == "combined_equipment")
    assert joint["evaluation"]["damage"] != result["benchmark_reference"]["payload"]["reference_damage"]


@pytest.mark.parametrize("mutate", [
    lambda a: a.update(scope="global_optimal_gear"),
    lambda a: a.update(preset_id="other-preset"),
    lambda a: a.update(candidate_limit=True),
    lambda a: a.update(generated_candidates=38),
    lambda a: a.update(evaluated_candidates=0),
    lambda a: a.update(piece_steps_unavailable_members=[True]),
    lambda a: a["target_equipment"][0]["equipment"][0].update(uid_serial=1),
    lambda a: a["target_equipment"][0]["equipment"][0].update(level=0),
    lambda a: a["target_equipment"][0]["equipment"][0].update(geometry="wrong"),
    lambda a: a["target_equipment"][0]["equipment"][1].update(target_row=5),
    lambda a: a["target_equipment"][0]["equipment"][0]["stats"][0].update(value=99999),
    lambda a: a["target_equipment"][0]["equipment"][0]["stats"][0].update(is_percent=1),
    lambda a: a["target_equipment"][0]["equipment"][0]["stats"].append(deepcopy(a["target_equipment"][0]["equipment"][0]["stats"][0])),
    lambda a: a["items"][0].update(kind="real_inventory_equip"),
    lambda a: a["items"][0]["changes"][0].update(character_id=999999),
    lambda a: a["items"][0]["changes"][0].update(mode="equip_uid"),
    lambda a: a["items"][0]["changes"][0]["piece_ids"].append("unknown-piece"),
    lambda a: a["items"][0]["changes"].append(deepcopy(a["items"][0]["changes"][0])),
    lambda a: a["items"][0]["evaluation"].update(scoring_domain_sha256="0"*64),
    lambda a: a["items"][0]["impact"].update(damage_delta=9999),
    lambda a: a["items"][0]["impact"].update(graduation_percentage_points=1),
    lambda a: a["items"].reverse(),
])
def test_invalid_budget_source_identity_piece_fields_values_and_gain_rejected(exported, mutate):
    result = deepcopy(exported["response"])
    mutate(result["equipment_recommendations"])
    with pytest.raises((NativeAnalysisError, ValueError)):
        validate(exported, result)


def test_unknown_and_preparation_failures_are_not_zero_gain(exported):
    for preparation in (False, True):
        result = deepcopy(exported["response"])
        advice = result["equipment_recommendations"]
        item = advice["items"].pop(0)
        item["impact"] = None
        if preparation:
            item.update(status="preparation_unavailable", evaluation=None, error_code="graduation_candidate_rule_dependencies_changed")
            advice["evaluated_candidates"] -= 1
            advice["preparation_unavailable_candidates"] += 1
        else:
            item["status"] = "unquantified"
            item["evaluation"].update(status="unavailable", damage=None, global_gap_codes=["model_missing"])
        advice["items"].append(item)
        known = [i for i in advice["items"] if i["impact"] is not None]
        unknown = sorted((i for i in advice["items"] if i["impact"] is None), key=lambda i: i["candidate_id"])
        advice["items"] = known+unknown
        assert validate(exported, result)["equipment_recommendations"]["items"][-1]["impact"] is None


def test_transport_requires_own_capability_and_validates_exact_request_bytes(exported):
    root = json.loads(exported["request_bytes"])
    class EquipmentClient(Client):
        dataset_version = root["dataset_version"]
        supports_team_axis_graduation_equipment_advice = True
        def _run(self, payload, *, checkpoint):
            self.calls += 1
            response = deepcopy(exported["response"])
            response["request_sha256"] = hashlib.sha256(payload).hexdigest()
            return json.dumps(response).encode("utf8")
    client = EquipmentClient()
    assert client.evaluate_team_axis_graduation(root["request"])["equipment_recommendations"]["items"]
    client.supports_team_axis_graduation_equipment_advice = False
    with pytest.raises(NativeAnalysisError):
        client.evaluate_team_axis_graduation(root["request"])
    assert client.calls == 1
    root["request"]["budget"] = {"max_evaluations": 1}
    with pytest.raises(NativeAnalysisError):
        validate_request(root["request"], root["dataset_version"])


def test_new_capability_does_not_become_an_existing_packaging_requirement(tmp_path):
    exe = tmp_path / "nte-analysis-core.exe"
    exe.write_bytes(b"not executed")
    for caps, expected in [(None, False), (["battle_page_v1"], False),
                           (["team_axis_graduation_cultivation_advice_v2"], False),
                           (["team_axis_graduation_equipment_advice_v2"], True)]:
        client = NteAnalysisCoreClient(exe, "fixture", capabilities=caps)
        assert client.supports_team_axis_graduation_equipment_advice is expected
    root = Path(__file__).resolve().parents[3]
    binary = root / "third_party/analysis-core/bin/nte-analysis-core.exe"
    manifest = root / "third_party/analysis-core/component.json"
    validate_analysis_component(binary, manifest)
    meta = json.loads(manifest.read_text("utf8"))
    client = NteAnalysisCoreClient(binary, "fixture", capabilities=meta["capabilities"])
    assert client.supports_battle_page
    assert client.supports_team_axis_graduation_equipment_advice == (
        "team_axis_graduation_equipment_advice_v2" in meta["capabilities"])
    # 发行验收允许缺少新建议能力的既有协议组件；不把本机尚未晋升当作产品不变量。
    # 基线能力随发行门禁扩充（worker、直伤、分段耗时），仍不含新的毕业装备建议能力。
    legacy_caps = ["battle_page_v1", "main_static_catalog_v1", "allocation_v2", "allocation_blueprints_v1",
                   "damage_allocation_v1", "equipment_scoring_v1", "battle_axis_team_v1", "battle_axis_fixed_team_v1",
                   "analysis_worker_v1", "direct_damage_v1", "analysis_stage_timings_v1"]
    legacy = {**meta, "sha256": hashlib.sha256(exe.read_bytes()).hexdigest(), "capabilities": legacy_caps}
    legacy_manifest = tmp_path / "component.json"
    legacy_manifest.write_text(json.dumps(legacy), encoding="utf8")
    validate_analysis_component(exe, legacy_manifest)
