# 验证三类显式操作的冻结、组件/来源门禁和发布前复核；伪组件只测试编排，不冒称真实EXE验收。
from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path

import pytest

from src.domain.teams.report_matching import ReportRange
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisCancelled, NativeAnalysisError, REQUEST_SCHEMA
from src.integrations.battle_analysis.graduation.team_axis_graduation import BATCH_KIND
from src.services.teams.axis_graduation.service import (
    AxisGraduationPreview, FrozenAxisGraduationRequest, TeamAxisGraduationService,
)
from tests.teams.graduation.test_team_axis_graduation_current import current_response
from tests.teams.graduation.test_team_axis_graduation_requests import prepared
from tests.teams.graduation.test_team_axis_graduation_wire import response_fixture


NTE_TEST_TIER = "core"


class Inputs:
    def __init__(self, current):
        self.current = current
        self.static_database_path = Path("F:/synthetic/static.sqlite3")
        self.database_path = Path("F:/synthetic/user.sqlite3")
        self.active = True
        self.freezes = self.verifications = 0

    def checkpoint(self, cancelled=lambda: False):
        if not self.active or cancelled():
            raise CancelledError

    def freeze_current(self, team, *, cancelled=lambda: False):
        self.checkpoint(cancelled)
        self.freezes += 1
        assert team == self.current.team
        return self.current

    def verify_current(self, value, *, cancelled=lambda: False):
        self.checkpoint(cancelled)
        self.verifications += 1
        if (value.account_id, value.generation) != (self.current.account_id, self.current.generation):
            raise CancelledError
        if value.sha256 != self.current.sha256:
            raise ValueError("actual source changed")


class Client:
    dataset_version = "fixture"
    engine_version = "0.3.8"
    supports_battle_page = supports_battle_page_identity = True
    supports_team_axis_graduation_benchmark = True
    supports_team_axis_graduation_current = supports_team_axis_graduation_advice = True

    def __init__(self, source):
        self.source = source
        self.component_provenance = {"component_executable_sha256": "1"*64, "component_manifest_sha256": "2"*64}
        self.identity_calls = []
        self.operations = []
        self.on_identity = self.on_evaluate = None
        self.old = None

    def load_battle_page(self, selector, *, checkpoint):
        checkpoint()
        self.identity_calls.append(deepcopy(selector))
        if self.on_identity:
            self.on_identity()
        return {"input_digest": self.source}

    def evaluate_team_axis_graduation(self, body, *, checkpoint):
        checkpoint()
        self.operations.append(deepcopy(body))
        envelope = {"schema_version": REQUEST_SCHEMA, "dataset_version": self.dataset_version,
                    "batch_kind": BATCH_KIND, "request": body}
        digest = hashlib.sha256(json.dumps(envelope, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf8")).hexdigest()
        if body["operation"] == "theoretical_benchmark":
            result = response_fixture(body, digest)
            self.old = deepcopy(result)
        else:
            result = current_response(body, self.old, digest)
            if body["operation"] == "recommendation_evaluation":
                result["recommendations"] = {"scope": "cultivation_to_compatible_preset_v1",
                    "preset_id": body["reference"]["payload"]["preset_id"], "stop_reason": "declared_candidates_complete",
                    "candidate_limit": 64, "generated_candidates": 0, "attempted_candidates": 0,
                    "evaluated_candidates": 0, "preparation_unavailable_candidates": 0, "omitted_candidates": 0, "items": []}
        if self.on_evaluate:
            self.on_evaluate(result)
        checkpoint()
        return result


def fixture():
    frozen, args = prepared()
    # 本例只测试编排，显式设为目标养成和家具，故养成建议为空；装备差距不属于该scope。
    frozen = replace(frozen, world_bonus_json=json.dumps(args["target"].world_bonus.to_payload()))
    inputs = Inputs(frozen)
    client = Client(args["source_input_digest"])
    factories = []
    def factory(**options):
        assert not options["cancelled"]()
        factories.append(options)
        return client
    service = TeamAxisGraduationService(current_inputs=inputs, semantics_path="F:/synthetic/semantics.json", client_factory=factory)
    values = {key: args[key] for key in ("target", "rules", "seeds", "request_id", "max_evaluations", "max_millis")}
    return service, inputs, client, values, factories


def solve(service, inputs, values, axis=ReportRange(1)):
    task = service.prepare_benchmark(inputs.current.team, axis, **values)
    return service.solve_benchmark(task)


def test_three_explicit_operations_share_immutable_reference_without_search_in_updates():
    service, inputs, client, values, _ = fixture()
    baseline = solve(service, inputs, values)
    reference = baseline.reference
    args = {key: values[key] for key in ("target", "rules", "request_id")}
    for prepare, execute, operation in [(service.prepare_current, service.evaluate_current, "current_evaluation"),
                                        (service.prepare_advice, service.evaluate_advice, "recommendation_evaluation")]:
        task = prepare(inputs.current.team, ReportRange(1), reference=reference, axis_snapshot=baseline.axis_snapshot, **args)
        result = execute(task)
        assert result.response["operation"] == operation and result.reference == reference
        assert "seeds" not in task.payload and "budget" not in task.payload
        assert result.response["current_ratio_percent"] is None
        service.verify_preview(result)
    assert len(client.operations) == 3 and inputs.freezes == 3
    assert baseline.reference == reference


@pytest.mark.parametrize("axis", [ReportRange(1), ReportRange(1, "first"), ReportRange(1, "second"), ReportRange(1, "range", 10, 20)])
def test_identity_reads_keep_exact_selected_range_before_and_after_analysis(axis):
    service, inputs, client, values, _ = fixture()
    result = solve(service, inputs, values, axis)
    assert len(client.identity_calls) == 2
    for selector in client.identity_calls:
        assert all(selector[k] == v for k, v in axis.selector_fields().items())
        assert selector["detail_level"] == "identity"
    assert result.request.payload["battle_request"]["detail_level"] == "hit"


def test_draft_mutation_during_read_cannot_change_seed_and_nested_payloads_are_copies():
    service, inputs, client, values, _ = fixture()
    old = deepcopy(values["seeds"])
    client.on_identity = lambda: values["seeds"].clear()
    task = service.prepare_benchmark(inputs.current.team, ReportRange(1), **values)
    assert task.payload["seeds"] == old
    task.payload["current"]["account_id"] = "different"
    assert task.payload["current"]["account_id"] == inputs.current.account_id
    result = service.solve_benchmark(task)
    result.response["benchmark_reference"]["payload"]["reference_damage"] = 1
    assert result.reference.to_payload()["payload"]["reference_damage"] == 200
    with pytest.raises(FrozenInstanceError):
        task.engine_version = "changed"


def test_component_not_ready_stops_before_freezing_or_starting_identity_reads():
    service, inputs, client, values, _ = fixture()
    client.supports_team_axis_graduation_benchmark = False
    with pytest.raises(NativeAnalysisError):
        service.prepare_benchmark(inputs.current.team, ReportRange(1), **values)
    assert inputs.freezes == 0 and not client.operations and not client.identity_calls


@pytest.mark.parametrize("change", ["source", "account", "generation", "component", "engine", "dataset"])
def test_changed_frozen_identity_stops_before_numeric_dispatch(change):
    service, inputs, client, values, _ = fixture()
    task = service.prepare_benchmark(inputs.current.team, ReportRange(1), **values)
    if change == "source":
        inputs.current = replace(inputs.current, source=replace(inputs.current.source, revision=2))
    elif change in {"account", "generation"}:
        inputs.current = replace(inputs.current, **({"account_id": "other"} if change == "account" else {"generation": 7}))
    elif change == "component":
        client.component_provenance["component_manifest_sha256"] = "0"*64
    elif change == "engine":
        client.engine_version = "0.3.7"
    else:
        client.dataset_version = "different"
    with pytest.raises((ValueError, CancelledError)):
        service.solve_benchmark(task)
    assert not client.operations


@pytest.mark.parametrize("change", ["source", "axis", "account", "cancel"])
def test_changes_during_analysis_discard_result_without_mutating_old_reference(change):
    service, inputs, client, values, _ = fixture()
    old = solve(service, inputs, values)
    saved = old.response_json
    def mutate(_result):
        if change == "source":
            inputs.current = replace(inputs.current, source=replace(inputs.current.source, revision=2))
        elif change == "axis":
            client.source = "f"*64
        elif change == "account":
            inputs.active = False
        else:
            raise NativeAnalysisCancelled
    client.on_evaluate = mutate
    with pytest.raises((ValueError, CancelledError)):
        service.solve_benchmark(old.request)
    assert old.response_json == saved


def test_current_source_change_during_last_identity_probe_is_also_rechecked():
    service, inputs, client, values, _ = fixture()
    task = service.prepare_benchmark(inputs.current.team, ReportRange(1), **values)
    client.on_identity = lambda: setattr(inputs, "current", replace(inputs.current, source=replace(inputs.current.source, revision=2)))
    with pytest.raises(ValueError):
        service.solve_benchmark(task)


def test_invalid_operation_and_forged_or_mismatched_preview_are_rejected():
    service, inputs, client, values, _ = fixture()
    result = solve(service, inputs, values)
    with pytest.raises(ValueError):
        service.evaluate_current(result.request)
    response = result.response
    response["current_ratio_percent"] = 70
    with pytest.raises(NativeAnalysisError):
        AxisGraduationPreview(result.request, json.dumps(response))
    body = result.request.payload
    body["current"]["source_sha256"] = "0"*64
    with pytest.raises(ValueError):
        FrozenAxisGraduationRequest(inputs.current, client.engine_version, json.dumps(body))


def test_preview_guard_uses_frozen_axis_but_rejects_wrong_object():
    service, inputs, client, values, _ = fixture()
    preview = solve(service, inputs, values)
    service.verify_preview(preview)
    client.source = "f"*64
    service.verify_preview(preview)
    with pytest.raises(ValueError):
        service.verify_preview({})


def test_cancelled_context_never_starts_component_factory_or_reads():
    service, inputs, client, values, factories = fixture()
    with pytest.raises(CancelledError):
        service.prepare_benchmark(inputs.current.team, ReportRange(1), cancelled=lambda: True, **values)
    inputs.active = False
    with pytest.raises(CancelledError):
        service.prepare_benchmark(inputs.current.team, ReportRange(1), **values)
    assert not factories and inputs.freezes == 0 and not client.identity_calls


@pytest.mark.parametrize("change", ["gap", "static"])
def test_incomplete_actual_input_stops_before_axis_probe(change):
    service, inputs, client, values, _ = fixture()
    inputs.current = replace(inputs.current, **({"gaps": ("missing_profile",)} if change == "gap" else {"static_sha256": "0"*64}))
    with pytest.raises(ValueError):
        service.prepare_benchmark(inputs.current.team, ReportRange(1), **values)
    assert not client.identity_calls and not client.operations


@pytest.mark.parametrize("identity", [True, "A"*64])
def test_malformed_axis_identity_never_reaches_numeric_dispatch(identity):
    service, inputs, client, values, _ = fixture()
    client.source = identity
    with pytest.raises(NativeAnalysisError):
        service.prepare_benchmark(inputs.current.team, ReportRange(1), **values)
    assert not client.operations
