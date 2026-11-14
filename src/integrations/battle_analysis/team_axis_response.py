# 验收两份独立战报的冻结计分域和等权归一收益，绝不把综合指数显示成实际伤害。
from copy import deepcopy
import math

from src.integrations.battle_analysis.battle_axis_evaluation import decode_axis_response_facts, validate_axis_selector
from src.integrations.battle_analysis.axis_fixed_team import validate_preflight_proof
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError


def invalid():
    return NativeAnalysisError("双队战报的来源、计分范围或归一化收益不一致")


def validate_team_axes(request):
    specs = request.get("team_axes")
    if not isinstance(specs, list) or len(specs) != 2:
        raise invalid()
    used = set()
    first = specs[0]["battle_request"]
    for spec in specs:
        if set(spec) != {"character_ids", "battle_request", "anchor_mode", "team_preflight"}:
            raise invalid()
        ids = spec["character_ids"]
        if (not isinstance(ids, list) or not 1 <= len(ids) <= 4 or len(set(ids)) != len(ids)
                or any(type(key) is not int or key <= 0 for key in ids) or used.intersection(ids)):
            raise invalid()
        used.update(ids)
        validate_axis_selector(spec["battle_request"])
        validate_preflight_proof(spec["team_preflight"])
        if spec["anchor_mode"] not in {"original", "effective"}:
            raise invalid()
        if any(spec["battle_request"][key] != first[key] for key in (
                "account_id", "generation", "user_database_path", "static_database_path", "semantics_path")):
            raise invalid()
    if used != set(request["role_character_ids"].values()):
        raise invalid()
    if (request["battle_request"] != first or request["anchor_mode"] != specs[0]["anchor_mode"]
            or request["team_preflight"] != specs[0]["team_preflight"]):
        raise invalid()


def decode_team_facts(result, request):
    reports = result.get("team_reports")
    selected = result.get("selected_result")
    if selected is None:
        if result["optimization"]["termination"] != "not_ready" or result["plans"] != request["baseline_plans"]:
            raise invalid()
        # 未建立完整双队基准时只保留 B0；不能产生归一收益或改变后的方案。
        return {key: deepcopy(result.get(key)) for key in ("source_input_digest", "frozen_identity", "scope", "baseline")} | {"candidates": []}, []
    if not isinstance(reports, list) or len(reports) != 2:
        raise invalid()
    decoded = []
    normalized = 0.0
    for report, spec in zip(reports, request["team_axes"]):
        local = {**request, "battle_request": spec["battle_request"], "anchor_mode": spec["anchor_mode"],
                 "role_character_ids": {name: key for name, key in request["role_character_ids"].items() if key in spec["character_ids"]}}
        facts = decode_axis_response_facts({**report, "candidates": [report["selected_result"]]}, local, ("selected",))
        if facts["source_input_digest"] != spec["team_preflight"]["source_input_digest"]:
            raise invalid()
        base = facts["baseline"]["candidate_value"]
        candidate = facts["candidates"][0]["candidate_value"]
        if (type(base) not in (int, float) or base <= 0 or not math.isfinite(base)
                or type(candidate) not in (int, float) or candidate < 0 or not math.isfinite(candidate)):
            raise invalid()
        normalized += candidate / base / 2
        decoded.append({**{key: facts[key] for key in ("source_input_digest", "frozen_identity", "scope", "baseline")},
                        "selected_result": facts["candidates"][0], "character_ids": list(spec["character_ids"]),
                        "battle_record_id": spec["battle_request"]["battle_record_id"], "relative_gain": candidate / base - 1})
    if (not math.isfinite(normalized) or not math.isclose(result["baseline"]["candidate_value"], 1.0)
            or not math.isclose(selected["candidate_value"], normalized, rel_tol=1e-9, abs_tol=1e-9)
            or result["source_input_digest"] != decoded[0]["source_input_digest"]
            or result["frozen_identity"] != decoded[0]["frozen_identity"]):
        raise invalid()
    facts = {key: deepcopy(result[key]) for key in ("source_input_digest", "frozen_identity", "scope")}
    facts.update(baseline={"candidate_value": 1.0, "coverage": None},
                 candidates=[{"candidate_value": normalized, "delta_from_baseline": normalized - 1, "coverage": None}])
    return facts, decoded
