# 验证不可变基准的双端黄金摘要、浮点规范、冻结副本和严格边界；内容摘要不冒充数学证明。
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.domain.teams.graduation.axis_graduation_reference import (
    FrozenBenchmarkReference,
    MAX_REFERENCE_BYTES,
    freeze_benchmark_reference,
    seal_benchmark_reference,
)


NTE_TEST_TIER = "core"
GOLDEN = Path(__file__).resolve().parents[2] / "fixtures/team_axis_graduation_reference_v2.json"
DIGEST = "67581be6665ea68dccee749cef86f579f7d187836e98325e0d6d9d85b307f762"


def reference():
    return FrozenBenchmarkReference(GOLDEN.read_text("utf8"))


def test_golden_reference_uses_ieee_float_bits_and_unicode_not_json_number_spelling():
    r = reference()
    assert r.sha256 == DIGEST
    assert seal_benchmark_reference(r.to_payload()["payload"]).sha256 == DIGEST
    assert FrozenBenchmarkReference(GOLDEN.read_text("utf8").replace("1.25e-07", "0.000000125")).sha256 == DIGEST
    assert "generation" not in r.to_payload()["payload"]


def test_frozen_reference_returns_detached_body_and_content_change_requires_new_identity():
    r = reference()
    with pytest.raises(FrozenInstanceError):
        r.body_json = "changed"
    body = r.to_payload()
    body["payload"]["reference_damage"] *= 2
    assert r.sha256 == DIGEST
    with pytest.raises(ValueError):
        freeze_benchmark_reference(body)
    changed = seal_benchmark_reference(body["payload"])
    assert changed.sha256 != r.sha256 and changed.benchmark_id != r.benchmark_id


@pytest.mark.parametrize("change", [
    lambda p: p.update(reference_damage=0), lambda p: p.update(reference_damage=float("inf")),
    lambda p: p["scope"].update(battle_record_id=True),
    lambda p: p["component"].update(component_manifest_sha256=None),
    lambda p: p["search"].update(optimality="global_optimal"),
    lambda p: p["search"].update(evaluation_budget=True),
    lambda p: p["anchor_world"].update(yaodao_attack_add=True),
    lambda p: p.update(generation=1),
])
def test_unknown_nonfinite_boolean_or_false_proof_never_forms_a_reference(change):
    payload = deepcopy(reference().to_payload()["payload"])
    change(payload)
    with pytest.raises((ValueError, TypeError)):
        seal_benchmark_reference(payload)


def test_duplicate_fields_and_oversize_body_are_rejected_before_use():
    raw = GOLDEN.read_text("utf8")
    with pytest.raises(ValueError):
        FrozenBenchmarkReference(raw.replace('"reference_damage":', '"reference_damage":1,"reference_damage":', 1))
    with pytest.raises(ValueError):
        FrozenBenchmarkReference(" " * (MAX_REFERENCE_BYTES + 1))


def test_equivalent_negative_zero_anchor_has_identical_frozen_body():
    payload = reference().to_payload()["payload"]
    payload["anchor_world"]["yaodao_attack_add"] = 0.0
    a = seal_benchmark_reference(payload)
    payload["anchor_world"]["yaodao_attack_add"] = -0.0
    b = seal_benchmark_reference(payload)
    assert a.sha256 == b.sha256 and a.body_json == b.body_json
