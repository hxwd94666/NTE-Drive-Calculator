# 将已冻结的四人实际来源转成正式基准请求；不读库存、不补模板、不启动求解，派发仍须owner复核来源。
from dataclasses import asdict
import json

from src.domain.teams.graduation.axis_graduation_inputs import CurrentBuild
from src.domain.teams.graduation.axis_graduation_rules import TheoryEquipmentRules
from src.domain.teams.graduation.axis_graduation_target import GraduationTarget
from src.integrations.battle_analysis.graduation.team_axis_graduation import PROFILE_KEYS, validate_request


def actual_component_input(frozen):
    if not isinstance(frozen, CurrentBuild) or frozen.has_gaps or len(frozen.members) != 4:
        raise ValueError("当前实际养成或配装来源仍有缺项，请补齐后建立轴伤基准")
    actors = []
    for member in frozen.members:
        effective = member.profile_evidence["effective"]
        if not isinstance(effective, dict):
            raise ValueError("当前实际养成未形成确认投影")
        profile = {key: effective[key] for key in PROFILE_KEYS if key in effective}
        if profile.get("awakening_level") == 0:
            # 已知零觉醒逻辑上没有普通觉醒可选，不因旧配置的未初始化标志制造未知或模板加级。
            if profile.get("selected_awaken_effect_ids") not in (None, []):
                raise ValueError("零觉醒与实际效果选择冲突")
            profile.update(selected_awaken_effect_ids=[], awakening_selection_initialized=True)
        if profile.get("fork_id", "unknown") is None:
            for key in ("fork_level", "fork_breakthrough_stage", "fork_refinement_level"):
                if profile.get(key) is not None:
                    raise ValueError("明确未装备弧盘仍残留成长数值")
                profile[key] = None
        if set(profile) != PROFILE_KEYS:
            raise ValueError("当前实际养成缺少正式字段，不采用默认毕业值")
        actors.append({"character_id": member.character_id, "profile": profile,
                       "equipment": member.equipment, "equipment_complete": True})
    return {"account_id": frozen.account_id, "generation": frozen.generation,
        "team_id": frozen.team.team_id, "character_ids": list(frozen.team.character_ids),
        "dataset_id": frozen.dataset_id, "static_schema": frozen.static_schema,
        "static_sha256": frozen.static_sha256, "source_sha256": frozen.sha256,
        "world_bonus": frozen.world_bonus, "build": {"characters": actors}}


def _provenance(frozen):
    members = []
    for member in frozen.members:
        profile = member.profile_evidence["effective"]
        sources = {key: value for key, value in profile.get("field_sources", {}).items() if key in PROFILE_KEYS}
        if profile.get("awakening_level") == 0:
            sources.update(selected_awaken_effect_ids="derived_zero_awakening",
                           awakening_selection_initialized="derived_zero_awakening")
        if profile.get("fork_id", "unknown") is None:
            sources.update({key: "derived_unequipped_fork" for key in (
                "fork_level", "fork_breakthrough_stage", "fork_refinement_level")})
        members.append({"character_id": member.character_id, "slot_id": member.slot_id, "plan_id": member.plan_id,
            "snapshot_id": member.snapshot_id, "source_fingerprint": member.source_fingerprint,
            "field_sources": sources, "skill_level_sources": profile.get("skill_level_sources", {})})
    return {"team_revision": frozen.team.revision, "source_mode": frozen.source.mode.value,
        "source_revision": frozen.source.revision, "bindings": [list(p) for p in frozen.source.bindings],
        "world_source": "account", "members": members}


def build_benchmark_request(*, frozen, target, rules, battle_selector, source_input_digest,
                            seeds, request_id, max_evaluations, max_millis, component_provenance):
    if not isinstance(target, GraduationTarget) or not isinstance(rules, TheoryEquipmentRules):
        raise ValueError("轴伤基准需要正式冻结的目标和词条规则")
    target.verify_rules(rules)
    request = {"component_provenance": component_provenance, "version": 2, "operation": "theoretical_benchmark", "request_id": request_id,
        "source_input_digest": source_input_digest, "target_sha256": target.sha256,
        "battle_request": battle_selector, "target": target.to_payload(), "rules": asdict(rules),
        "current": actual_component_input(frozen), "current_provenance": _provenance(frozen), "seeds": seeds,
        "budget": {"max_evaluations": max_evaluations, "max_millis": max_millis}}
    # 正文及输出与调用方可变字典脱离；账号/generation和主静态身份由正式协议再次核对。
    request = json.loads(json.dumps(request, ensure_ascii=False, allow_nan=False))
    validate_request(request, rules.dataset_id)
    return request


def build_current_request(*, frozen, target, rules, battle_selector, source_input_digest,
                          reference, axis_snapshot, request_id, component_provenance):
    from src.domain.teams.graduation.axis_graduation_reference import FrozenBenchmarkReference
    from src.domain.teams.graduation.axis_graduation_snapshot import FrozenAxisContextSnapshot
    if not isinstance(reference, FrozenBenchmarkReference) or not isinstance(axis_snapshot, FrozenAxisContextSnapshot):
        raise ValueError("更新当前评价需要不可变基准引用，不重新搜索或替换旧分母")
    if not isinstance(target, GraduationTarget) or not isinstance(rules, TheoryEquipmentRules):
        raise ValueError("更新当前评价缺少冻结目标和规则")
    target.verify_rules(rules)
    request = {"component_provenance": component_provenance, "version": 2, "operation": "current_evaluation",
        "request_id": request_id, "source_input_digest": source_input_digest, "target_sha256": target.sha256,
        "battle_request": battle_selector, "target": target.to_payload(), "rules": asdict(rules),
        "current": actual_component_input(frozen), "current_provenance": _provenance(frozen), "reference": reference.to_payload(),
        "axis_snapshot": axis_snapshot.to_payload()}
    request = json.loads(json.dumps(request, ensure_ascii=False, allow_nan=False))
    validate_request(request, rules.dataset_id)
    return request


def build_advice_request(**values):
    """建议只引用已有分母和实际冻结来源；不读取全背包、不携带搜索预算。"""
    request = build_current_request(**values)
    request["operation"] = "recommendation_evaluation"
    validate_request(request, values["rules"].dataset_id)
    return request


def build_equipment_advice_request(**values):
    """装备建议固定旧参照和实际养成；不携带库存候选、搜索预算或真实装配意图。"""
    request = build_current_request(**values)
    request["operation"] = "equipment_advice_evaluation"
    validate_request(request, values["rules"].dataset_id)
    return request
