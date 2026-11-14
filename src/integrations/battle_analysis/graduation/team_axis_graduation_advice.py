# 校验养成建议的完整变化与数学收益；只复核组件结果，不复制伤害公式或生成装备/养成假事实。
from copy import deepcopy
import math

from src.integrations.battle_analysis.graduation.team_axis_graduation import (
    PROFILE_KEYS, _keys, _require, _integer, _text, _list, validate_common_request, validate_current_result,
)
from src.integrations.battle_analysis.graduation.team_axis_graduation_current import reference_context, validate_current_response
from src.integrations.battle_analysis.graduation.team_axis_graduation_qualification import validate_target_qualification


KINDS = {"growth", "skill_with_growth", "awakening", "fork", "likeability", "world", "combined_cultivation"}
KIND_FIELDS = {"growth": {"character_level", "breakthrough_stage"},
    "skill_with_growth": {"character_level", "breakthrough_stage", "skill_levels"},
    "awakening": {"awakening_level", "selected_awaken_effect_ids", "awakening_selection_initialized"},
    "fork": {"fork_id", "fork_level", "fork_breakthrough_stage", "fork_refinement_level"},
    "likeability": {"likeability_level_10_enabled"}, "world": {"yaodao_attack_add", "quantao_crit_damage"}}


def validate_advice_request(request, dataset_version):
    target = validate_common_request(request, dataset_version, operation="recommendation_evaluation", extras={"reference", "axis_snapshot"})
    reference_context(request["reference"], request, target, axis_snapshot=request["axis_snapshot"])
    return target


def _profile(member):
    value = member.to_payload()
    fork = value.pop("fork")
    value.pop("character_id")
    value["skill_levels"] = {s["skill_id"]: s["level"] for s in value["skill_levels"]}
    value["awakening_selection_initialized"] = True
    value.update(fork_id=fork["fork_id"] if fork else None, fork_level=fork["level"] if fork else None,
        fork_breakthrough_stage=fork["breakthrough_stage"] if fork else None,
        fork_refinement_level=fork["refinement_level"] if fork else None)
    return value


def _changes(item, request, preset, target):
    hypothesis = deepcopy(request)
    actors = {a["character_id"]: a for a in hypothesis["current"]["build"]["characters"]}
    goals = {m.character_id: _profile(m) for m in preset.members}
    changes = _list(item["changes"], 512, 1)
    seen = set()
    owners = set()
    for change in changes:
        _keys(change, "character_id field entry_id value")
        actor, field, entry, value = (change[k] for k in ("character_id", "field", "entry_id", "value"))
        _require(isinstance(field, str))
        if item["kind"] != "combined_cultivation":
            _require(field in KIND_FIELDS[item["kind"]])
        if actor is None:
            _require(field in KIND_FIELDS["world"] and entry is None)
            expected = target.world_bonus.to_payload()[field]
            _require(type(value) in (int, float) and math.isfinite(value) and value == expected)
            hypothesis["current"]["world_bonus"][field] = value
        else:
            _integer(actor, 1)
            _require(actor in actors and field in PROFILE_KEYS)
            if field == "skill_levels":
                _text(entry)
                _require(entry in goals[actor][field] and type(value) is int and value == goals[actor][field][entry])
                actors[actor]["profile"][field][entry] = value
            else:
                _require(entry is None and type(value) is type(goals[actor][field]) and value == goals[actor][field])
                actors[actor]["profile"][field] = value
        key = (actor, field, entry)
        _require(key not in seen)
        seen.add(key)
        owners.add(actor)
    if item["kind"] != "combined_cultivation":
        _require(len(owners) == 1 and (None in owners) == (item["kind"] == "world"))
    if item["kind"] == "skill_with_growth":
        actor, = owners
        for field in KIND_FIELDS["growth"]:
            if request["current"]["build"]["characters"][list(actors).index(actor)]["profile"][field] != goals[actor][field]:
                _require((actor, field, None) in seen)
    return hypothesis


def _signed(value):
    _require(type(value) in (int, float) and math.isfinite(value))


def _impact(value, current, candidate, reference):
    _keys(value, "damage_delta relative_gain reference_fraction_delta graduation_percentage_points")
    _require(current is not None and candidate is not None and value["graduation_percentage_points"] is None)
    expected = {"damage_delta": candidate-current, "reference_fraction_delta": (candidate-current)/reference}
    for key, number in expected.items():
        _signed(value[key])
        _require(math.isclose(value[key], number, rel_tol=1e-10, abs_tol=1e-8))
    if current == 0:
        _require(value["relative_gain"] is None)
    else:
        _signed(value["relative_gain"])
        _require(math.isclose(value["relative_gain"], (candidate-current)/current, rel_tol=1e-10, abs_tol=1e-10))


def validate_advice_response(result, request, *, dataset_version, engine_version, request_sha256):
    validate_current_response(result, request, dataset_version=dataset_version, engine_version=engine_version,
        request_sha256=request_sha256, extra_fields={"recommendations"})
    target = validate_advice_request(request, dataset_version)
    advice = _keys(result["recommendations"], "scope preset_id stop_reason generated_candidates attempted_candidates evaluated_candidates preparation_unavailable_candidates omitted_candidates candidate_limit items")
    _require(advice["scope"] == "cultivation_to_compatible_preset_v1")
    _integer(advice["candidate_limit"], 64, 64)
    for key in ("generated_candidates", "attempted_candidates", "evaluated_candidates", "preparation_unavailable_candidates", "omitted_candidates"):
        _integer(advice[key], 0, 512)
    items = _list(advice["items"], 64)
    count = advice["generated_candidates"]
    _require(advice["attempted_candidates"] == len(items) == min(count, 64) and advice["omitted_candidates"] == max(0, count-64))
    compatible = result["current"]["target_qualification"]["compatible_preset_ids"]
    if not compatible:
        _require(advice["preset_id"] is None and count == 0 and advice["evaluated_candidates"] == 0 and advice["preparation_unavailable_candidates"] == 0 and advice["stop_reason"] == "current_outside_target_conditions")
        return result
    preferred = request["reference"]["payload"]["preset_id"]
    selected = preferred if preferred in compatible else compatible[0]
    _require(advice["preset_id"] == selected and advice["stop_reason"] == ("candidate_limit" if count > 64 else "declared_candidates_complete"))
    preset, = (p for p in target.presets if p.preset_id == selected)
    ids = set()
    order = []
    for item in items:
        _keys(item, "candidate_id kind changes status error_code evaluation impact")
        cid = item["candidate_id"]
        _require(isinstance(cid, str) and 0 < len(cid.encode("utf8")) <= 512 and cid.strip() == cid
                 and not any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in cid) and cid not in ids)
        ids.add(cid)
        _require(isinstance(item["kind"], str) and item["kind"] in KINDS)
        hypothesis = _changes(item, request, preset, target)
        evaluation = item["evaluation"]
        if evaluation is None:
            _require(item["status"] == "preparation_unavailable" and item["impact"] is None)
            _text(item["error_code"])
        else:
            _require(item["error_code"] is None)
            domain = validate_current_result(evaluation, hypothesis)
            _require(domain == result["current"]["axis_evaluation"]["scoring_domain_sha256"])
            qualification = validate_target_qualification(evaluation["target_qualification"], hypothesis, target)
            if item["impact"] is None:
                _require(item["status"] == "unquantified")
            else:
                _require(qualification != "outside_target" and item["status"] == "mathematical_only")
                _impact(item["impact"], result["current"]["axis_evaluation"]["damage"], evaluation["axis_evaluation"]["damage"],
                        request["reference"]["payload"]["reference_damage"])
        delta = item["impact"]["damage_delta"] if item["impact"] is not None else None
        order.append((delta is None, -delta if delta is not None else 0, cid))
    evaluated = sum(item["evaluation"] is not None for item in items)
    _require(advice["evaluated_candidates"] == evaluated
             and advice["preparation_unavailable_candidates"] == len(items)-evaluated)
    _require(order == sorted(order))
    return result
