# 验证新轴伤比较的冻结身份、同口径覆盖和数值边界，不以个人均值或观测触发替代理论基准。
from dataclasses import FrozenInstanceError, replace
import json

import pytest

from src.domain.teams.graduation.axis_graduation import (
    CRITICAL_POLICY,
    MODEL_ID,
    AxisGraduationContext,
    AxisScoringCoverage,
    BenchmarkSummary,
    GraduationEvaluation,
    GraduationStatus,
    SearchEvidence,
    decode_graduation_evaluation,
    encode_graduation_evaluation,
)
from src.domain.teams.report_matching import ReportRange


NTE_TEST_TIER = "core"


def context():
    return AxisGraduationContext(
        team_id="fixture-team", character_ids=(1, 2, 3, 4), axis=ReportRange(7, "first"),
        axis_sha256="a" * 64, environment_sha256="b" * 64,
        dataset_id="fixture-static", static_schema=42, static_sha256="c" * 64,
        rules_sha256="d" * 64, scoring_domain_sha256="e" * 64,
        engine_version="fixture-engine", component_sha256="f" * 64,
        component_manifest_sha256="0" * 64, model_id=MODEL_ID, critical_policy=CRITICAL_POLICY,
    )


def benchmark(*, coverage=None, search=None):
    return BenchmarkSummary(
        benchmark_id="benchmark-one", target_id="target-one", target_revision=1,
        target_sha256="1" * 64, context=context(), reference_damage=2_000_000,
        theoretical_build_sha256="2" * 64,
        coverage=coverage or AxisScoringCoverage("e" * 64, 10, 10, 0, 0),
        search=search or SearchEvidence(
            stop_reason="neighborhood_complete", evaluated_candidates=20, evaluation_budget=100,
            time_budget_ms=5000, elapsed_ms=500, seed=7, legal_domain_sha256="3" * 64, neighborhood_complete=True,
        ),
    )


def evaluation(reference=None, **changes):
    reference = reference or benchmark()
    value = GraduationEvaluation(
        benchmark=reference, current_build_sha256="4" * 64, current_context=reference.context,
        current_coverage=reference.coverage, current_damage=1_400_000,
        current_ratio_percent=70, remaining_damage=600_000,
        relative_gain_to_reference=3 / 7, remaining_percentage_points=30,
    )
    return replace(value, **changes) if changes else value


def test_one_axis_ratio_uses_theoretical_reference_and_distinct_gain_units():
    result = evaluation()
    assert result.current_ratio_percent == 70
    assert result.relative_gain_to_reference == pytest.approx(.428571428571)
    assert result.remaining_percentage_points == 30
    assert result.status == GraduationStatus.READY
    assert result.benchmark.reference_damage == 2_000_000
    assert result.benchmark.search.optimality == "bounded_best"


def test_preset_share_truncation_is_limited_even_when_global_budget_has_balance():
    search = replace(benchmark().search, stop_reason="preset_budget", neighborhood_complete=False)
    assert search.evaluated_candidates < search.evaluation_budget
    assert search.limited
    assert evaluation(benchmark(search=search)).status == GraduationStatus.SEARCH_LIMITED
    with pytest.raises(ValueError):
        replace(search, neighborhood_complete=True)


def test_confirmed_zero_current_is_zero_percent_not_unknown_or_infinite_gain():
    result = evaluation(current_damage=0, current_ratio_percent=0, remaining_damage=2_000_000,
                        relative_gain_to_reference=None, remaining_percentage_points=100)
    assert result.current_ratio_percent == 0 and result.relative_gain_to_reference is None
    with pytest.raises(ValueError):
        replace(result, relative_gain_to_reference=0)


@pytest.mark.parametrize("value", [None, True, 0, -1, float("nan"), float("inf"), float("-inf"), 10 ** 1000])
def test_invalid_reference_never_forms_a_percentage(value):
    with pytest.raises(ValueError):
        replace(benchmark(), reference_damage=value)


@pytest.mark.parametrize("changes", [
    {"current_damage": None}, {"current_damage": -1}, {"current_damage": True},
    {"current_ratio_percent": float("nan")}, {"current_ratio_percent": 71},
    {"relative_gain_to_reference": .3}, {"remaining_percentage_points": 42.9},
    {"remaining_damage": 0},
])
def test_inconsistent_unknown_or_nonfinite_current_values_are_rejected(changes):
    with pytest.raises(ValueError):
        evaluation(**changes)


def test_above_reference_is_not_clamped_and_requires_a_diagnostic():
    changes = dict(current_damage=2_200_000, current_ratio_percent=110,
                   remaining_damage=-200_000, relative_gain_to_reference=-1 / 11,
                   remaining_percentage_points=-10)
    with pytest.raises(ValueError, match="诊断"):
        evaluation(**changes)
    result = evaluation(**changes, comparison_note="current_exceeds_selected_reference")
    assert result.current_ratio_percent == 110
    assert result.status == GraduationStatus.ABOVE_REFERENCE


@pytest.mark.parametrize("changes", [
    {"axis": ReportRange(7, "second")}, {"axis_sha256": "9" * 64},
    {"environment_sha256": "9" * 64}, {"static_sha256": "9" * 64},
    {"rules_sha256": "9" * 64}, {"scoring_domain_sha256": "9" * 64},
    {"engine_version": "another-engine"}, {"component_sha256": "9" * 64},
    {"character_ids": (1, 2, 3, 5)}, {"team_id": "another-team"},
])
def test_context_changes_require_a_new_benchmark(changes):
    with pytest.raises(ValueError, match="上下文"):
        evaluation(current_context=replace(context(), **changes))


def test_legacy_critical_policy_cannot_masquerade_as_candidate_state_propagation():
    with pytest.raises(ValueError, match="模型|暴击"):
        replace(context(), critical_policy="expected_with_frozen_observed_triggers")
    with pytest.raises(ValueError, match="模型|暴击"):
        replace(context(), model_id="frozen_axis_expected_v1")


def test_partial_coverage_is_explicit_and_candidates_cannot_drop_unknown_events():
    coverage = AxisScoringCoverage("e" * 64, 10, 6, 2, 2)
    result = evaluation(benchmark(coverage=coverage))
    assert result.status == GraduationStatus.PARTIAL
    with pytest.raises(ValueError, match="覆盖"):
        replace(result, current_coverage=AxisScoringCoverage("e" * 64, 9, 6, 2, 1))
    with pytest.raises(ValueError, match="关键|计分"):
        benchmark(coverage=replace(coverage, critical_gaps=("team_buff_unknown",)))
    with pytest.raises(ValueError):
        benchmark(coverage=AxisScoringCoverage("e" * 64, 10, 0, 0, 10))


def test_budget_best_is_usable_but_not_a_global_optimality_claim():
    search = replace(benchmark().search, stop_reason="evaluation_budget", evaluated_candidates=100,
                     neighborhood_complete=False)
    result = evaluation(benchmark(search=search))
    assert result.status == GraduationStatus.SEARCH_LIMITED
    assert result.benchmark.search.optimality == "bounded_best"
    with pytest.raises(ValueError):
        replace(search, evaluated_candidates=101)
    for reason in ("cancelled", "failed", "input_invalid"):
        with pytest.raises(ValueError):
            replace(search, stop_reason=reason)


def test_global_optimality_claims_wait_for_an_actual_proof_verifier():
    search = benchmark().search
    with pytest.raises(ValueError, match="证明"):
        replace(search, optimality="proven_optimal")
    with pytest.raises(ValueError):
        replace(search, stop_reason="proven_optimal")
    value = json.loads(encode_graduation_evaluation(evaluation()))
    value["evaluation"]["benchmark"]["search"].update(upper_bound=2_000_000, proof_sha256="5" * 64)
    with pytest.raises(ValueError):
        decode_graduation_evaluation(json.dumps(value))


def test_context_is_immutable_and_digest_covers_semantic_inputs_not_current_build():
    frozen = context()
    with pytest.raises(FrozenInstanceError):
        frozen.engine_version = "changed"
    assert len(frozen.sha256) == 64
    assert frozen.sha256 != replace(frozen, environment_sha256="8" * 64).sha256
    result = evaluation()
    changed = replace(result, current_build_sha256="9" * 64)
    assert changed.benchmark == result.benchmark
    assert changed.benchmark.context.sha256 == frozen.sha256


def test_reference_catalog_and_mutable_roster_cannot_change_the_frozen_context():
    for scope in ("reference", "role_page"):
        with pytest.raises(ValueError, match="图鉴"):
            replace(context(), catalog_scope=scope)
    ids = [1, 2, 3, 4]
    frozen = replace(context(), character_ids=ids)
    digest = frozen.sha256
    ids[0] = 999
    assert frozen.character_ids == (1, 2, 3, 4) and frozen.sha256 == digest


@pytest.mark.parametrize("changes", [
    {"character_ids": (1, 1, 2, 3)}, {"character_ids": (True, 2, 3, 4)},
    {"character_ids": (1, 2, 3)}, {"static_schema": True},
    {"axis_sha256": "a" * 63}, {"engine_version": ""},
    {"component_manifest_sha256": "A" * 64},
])
def test_malformed_context_is_rejected_before_serialization(changes):
    with pytest.raises(ValueError):
        replace(context(), **changes)


@pytest.mark.parametrize("changes", [
    {"stop_reason": []}, {"evaluated_candidates": 0}, {"elapsed_ms": -1},
    {"neighborhood_complete": False}, {"seed": True},
    {"optimality": "proven_optimal"}, {"time_budget_ms": 0},
    {"stop_reason": "time_budget", "elapsed_ms": 1},
])
def test_unfinished_or_malformed_search_evidence_is_not_a_benchmark(changes):
    with pytest.raises(ValueError):
        replace(benchmark().search, **changes)


def test_versioned_payload_roundtrip_revalidates_all_frozen_comparison_fields():
    original = evaluation()
    encoded = encode_graduation_evaluation(original)
    assert decode_graduation_evaluation(encoded) == original
    assert "inventory" not in json.loads(encoded)
    changed = json.loads(encoded)
    changed["evaluation"]["current_context"]["environment_sha256"] = "9" * 64
    with pytest.raises(ValueError, match="上下文"):
        decode_graduation_evaluation(json.dumps(changed))


@pytest.mark.parametrize("failure", ["version", "bool_version", "extra", "missing", "nan", "duplicate", "nested", "oversized"])
def test_payload_parser_rejects_unknown_format_nonfinite_and_ambiguous_fields(failure):
    value = json.loads(encode_graduation_evaluation(evaluation()))
    if failure == "version":
        value["version"] = 2
    elif failure == "bool_version":
        value["version"] = True
    elif failure == "extra":
        value["evaluation"]["inventory"] = []
    elif failure == "missing":
        del value["evaluation"]["benchmark"]["search"]
    elif failure == "nan":
        value["evaluation"]["current_ratio_percent"] = float("nan")
    elif failure == "nested":
        value["evaluation"]["current_context"]["axis"] = []
    raw = json.dumps(value)
    if failure == "duplicate":
        raw = '{"version":1,' + raw[1:]
    elif failure == "oversized":
        raw += " " * (128 * 1024)
    with pytest.raises(ValueError):
        decode_graduation_evaluation(raw)


def test_reported_weight_objective_is_not_the_new_calculated_damage_unit():
    with pytest.raises(ValueError, match="单位"):
        replace(context(), damage_unit="reported_weight_expected_damage")


def test_time_budget_result_keeps_its_limits_and_is_not_reported_as_complete_search():
    evidence = replace(benchmark().search, stop_reason="time_budget", elapsed_ms=5000,
                       neighborhood_complete=False)
    result = evaluation(benchmark(search=evidence))
    assert result.status == GraduationStatus.SEARCH_LIMITED
    assert evidence.elapsed_ms == evidence.time_budget_ms


def test_unknown_candidates_limit_search_instead_of_proving_local_convergence():
    source = benchmark().search
    with pytest.raises(ValueError, match="量化受限"):
        replace(source, unquantified_candidates=1)
    limited = replace(source, stop_reason="quantification_limited", neighborhood_complete=False,
                      unquantified_candidates=1)
    assert evaluation(benchmark(search=limited)).status == GraduationStatus.SEARCH_LIMITED
    with pytest.raises(ValueError):
        replace(limited, unquantified_candidates=source.evaluated_candidates + 1)
