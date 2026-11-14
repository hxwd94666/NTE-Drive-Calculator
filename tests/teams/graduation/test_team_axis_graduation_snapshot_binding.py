# 验证正式快照绑定、旧引用拒绝和脱离源战报的应用编排；不在Calc解释或修改真实逐击。
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path

import pytest

from src.domain.teams.graduation.axis_graduation_reference import FrozenBenchmarkReference
from src.domain.teams.graduation.axis_graduation_snapshot import freeze_axis_context_snapshot
from src.domain.teams.report_matching import ReportRange
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
from src.integrations.battle_analysis.graduation.team_axis_graduation import validate_request, validate_response
from tests.teams.graduation.test_team_axis_graduation_current import CurrentClient
from tests.teams.graduation.test_team_axis_graduation_current import current_fixture
from tests.teams.graduation.test_team_axis_graduation_service import fixture
from tests.teams.graduation.test_team_axis_graduation_service import solve

NTE_TEST_TIER = "core"


def test_old_unbound_reference_has_no_implicit_snapshot_identity():
    raw = (Path(__file__).resolve().parents[2] / "fixtures/team_axis_graduation_reference_v1.json").read_text("utf8")
    with pytest.raises(ValueError):
        FrozenBenchmarkReference(raw)


@pytest.mark.parametrize("field", ["account_id", "scope", "source_input_digest", "dataset_id", "static_schema", "static_sha256", "engine_version"])
def test_snapshot_metadata_mismatch_stops_before_dispatch(field):
    request, _ = current_fixture()
    request["axis_snapshot"][field] = "changed"
    client = CurrentClient()
    with pytest.raises(NativeAnalysisError):
        client.evaluate_team_axis_graduation(request)
    assert client.calls == 0


def test_resigned_equivalent_snapshot_cannot_replace_the_bound_byte_identity():
    request, _ = current_fixture()
    snapshot = request["axis_snapshot"]
    snapshot["payload_json"] += " "
    snapshot["payload_sha256"] = hashlib.sha256(snapshot["payload_json"].encode("utf8")).hexdigest()
    metadata = {k: v for k, v in snapshot.items() if k not in {"payload_json", "snapshot_sha256"}}
    snapshot["snapshot_sha256"] = hashlib.sha256(json.dumps(metadata, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf8")).hexdigest()
    freeze_axis_context_snapshot(snapshot)
    client = CurrentClient()
    with pytest.raises(NativeAnalysisError):
        client.evaluate_team_axis_graduation(request)
    assert client.calls == 0


def test_current_and_advice_do_not_probe_deleted_original_and_keep_actual_source_guards():
    service, inputs, client, values, _ = fixture()
    baseline = solve(service, inputs, values)
    calls = len(client.identity_calls)
    def missing():
        raise AssertionError("original report must not be read")
    client.on_identity = missing
    client.supports_battle_page = client.supports_battle_page_identity = False
    args = {k: values[k] for k in ("target", "rules", "request_id")}
    for prepare, execute in [(service.prepare_current, service.evaluate_current),
                             (service.prepare_advice, service.evaluate_advice)]:
        task = prepare(inputs.current.team, ReportRange(1), reference=baseline.reference,
            axis_snapshot=baseline.axis_snapshot, **args)
        preview = execute(task)
        service.verify_preview(preview)
        assert preview.axis_snapshot == baseline.axis_snapshot
        assert preview.reference == baseline.reference
        assert task.payload["source_input_digest"] == baseline.axis_snapshot.to_payload()["source_input_digest"]
    assert len(client.identity_calls) == calls
    inputs.current = replace(inputs.current, source=replace(inputs.current.source, revision=2))
    with pytest.raises(ValueError):
        service.verify_preview(preview)


def test_native_formal_deletion_results_pass_both_calc_protocols_without_live_paths():
    path = os.environ.get("NTE_TEAM_AXIS_GRADUATION_SNAPSHOT_CASE")
    if not path:
        pytest.skip("explicit synthetic formal snapshot binding export required")
    case_path = Path(path)
    case = json.loads(case_path.read_text("utf8"))
    assert not Path(case["source_path"]).exists()
    value = json.loads((case_path.parent / "synthetic-formal-snapshot-binding-v1.json").read_text("utf8"))
    assert value["fixture_kind"] == "synthetic_formal_snapshot_binding_v1" and value["source_deleted"] is True
    for operation in ("current", "advice"):
        raw = value[operation + "_request_bytes"].encode("utf8")
        envelope = json.loads(raw)
        response = validate_response(value[operation + "_response"], envelope["request"],
            dataset_version=envelope["dataset_version"], engine_version="0.3.8",
            request_sha256=hashlib.sha256(raw).hexdigest())
        assert response["axis_snapshot"] == value["benchmark_response"]["axis_snapshot"]
        assert response["benchmark_reference"] == value["benchmark_response"]["benchmark_reference"]
        assert response["reference_verification"]["search_evaluations"] == 0
        assert response["current_ratio_percent"] is None
    swapped = json.loads(value["substituted_request_bytes"])
    freeze_axis_context_snapshot(swapped["request"]["axis_snapshot"])
    with pytest.raises(NativeAnalysisError):
        validate_request(swapped["request"], swapped["dataset_version"])
