# 校验内核给出的固定观测轴数学比例和单位；不计算伤害，不将可执行性未知包装为实战保证。
import math
from src.integrations.battle_analysis.graduation.team_axis_graduation import FIXED_AXIS_RATIO_POLICY, _keys, _require, _number


def _equal(value, expected):
    _require(type(value) in (int, float) and math.isfinite(value)
             and math.isclose(value, expected, rel_tol=1e-10, abs_tol=1e-10))


def validate_fixed_axis_ratio(result):
    value = _keys(result["fixed_axis_graduation"], "policy_id assumption axis_feasibility status ratio_percent damage_gap relative_gain_to_reference above_reference benchmark_id current_input_sha256 scoring_domain_sha256")
    current = result["current"]
    axis = current["axis_evaluation"]
    reference = result["benchmark_reference"]
    _require(value["policy_id"] == FIXED_AXIS_RATIO_POLICY and value["assumption"] == "fixed_observed_actions_and_timing_v1"
             and value["axis_feasibility"] == "unverified" and value["current_input_sha256"] == current["input_sha256"]
             and value["scoring_domain_sha256"] == axis["scoring_domain_sha256"]
             and value["benchmark_id"] == (reference["benchmark_id"] if reference else None))
    a, b = axis["damage"], reference["payload"]["reference_damage"] if reference else None
    if a is not None:
        _number(a)
    if b is not None:
        _number(b)
        _require(b > 0)
    if result["status"] == "outside_target":
        status = "outside_target_conditions"
    elif b is None:
        status = "reference_unavailable"
    elif a is None:
        status = "current_unquantified"
    else:
        status = "comparable" if math.isfinite(a/b*100) else "numeric_unavailable"
    _require(value["status"] == status)
    if status != "comparable":
        _require(all(value[k] is None for k in ("ratio_percent", "damage_gap", "relative_gain_to_reference", "above_reference")))
        return value
    _equal(value["ratio_percent"], a/b*100)
    _equal(value["damage_gap"], b-a)
    _require(type(value["above_reference"]) is bool and value["above_reference"] == (a > b))
    gain = b/a-1 if a else None
    if gain is None or not math.isfinite(gain):
        _require(value["relative_gain_to_reference"] is None)
    else:
        _equal(value["relative_gain_to_reference"], gain)
    return value
