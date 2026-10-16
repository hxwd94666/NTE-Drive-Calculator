# 维护伤害策略的选择提示、冻结元数据与保存归属；计算统一由完整 Rust 作业执行。
from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from src.integrations.allocation_postprocess import OPTIMIZATION_FIELDS
from src.services.allocation.postprocess_facts import allocation_inputs_digest, locked_allocation_owners

MODEL_ID = "role_panel_standard_direct_v1"
OBJECTIVE = "standard_direct_v1"


def direct_strategy_selection_error(strategy: str, selected_roles: Sequence[str]) -> str | None:
    if strategy == "direct_priority" and not selected_roles:
        return "直伤优先需要明确选择目标角色及优先组。"
    return None



def optimization_payload(plan: Mapping[str, Any]) -> dict[str, Any]:
    value = plan.get("damage_optimization")
    if not isinstance(value, Mapping):
        return {}
    from src.integrations.axis_allocation_postprocess import OPTIMIZATION_FIELDS as AXIS_FIELDS
    allowed = OPTIMIZATION_FIELDS | AXIS_FIELDS | {"status", "input_identity", "source_snapshot_id", "dataset_id",
                                     "character_id", "owners_sha256", "engine_version",
                                     "component_executable_sha256", "component_manifest_sha256", "equipment_value_mode", "allocation_character_ids", "ownership_excluded_character_ids",
                                     "source_input_digest", "fixed_team_build_sha256", "fixed_team_members", "frozen_identity", "scope", "battle_record_id", "anchor_mode", "critical_policy",
                                     "neighborhood", "candidate_visits", "group_index", "group_members", "group_objective_value",
                                     "relative_gain", "save_dependencies", "exchange_links", "single_complete", "pair_complete", "exchange_complete", "overall_termination", "shared_value",
                                     "team_event_scope", "selection_policy", "groups", "role", "blueprint_complete", "elapsed_millis", "budget_policy", "projection_policy", "job_policy", "scoring_policy", "job_elapsed_millis", "job_budget", "blueprint_usage", "shared_build_dependencies", "shared_build_dependencies_sha256"}
    return {"damage_optimization": {key: deepcopy(item) for key, item in value.items() if key in allowed}}


def verify_direct_postprocess_ownership(dao, rows, *, dataset_id=None) -> None:
    for row in rows:
        metadata = (row.get("payload") or {}).get("damage_optimization") or {}
        if metadata and (metadata.get("objective") != OBJECTIVE or metadata.get("model_id") != MODEL_ID
                         or metadata.get("source_snapshot_id") != row.get("snapshot_id")
                         or metadata.get("character_id") != row.get("character_id")
                         or (dataset_id is not None and metadata.get("dataset_id") != dataset_id)):
            raise RuntimeError("直伤优化目标与冻结方案身份不一致，请重新计算。")
        if metadata and metadata.get("status") != "completed":
            raise RuntimeError("直伤优化未完成，请按角色优先重新计算后保存基础方案。")
        expected = metadata.get("owners_sha256")
        for key in ("component_executable_sha256", "component_manifest_sha256"):
            source = metadata.get(key)
            if source is not None and (not isinstance(source, str) or len(source) != 64
                                      or any(char not in "0123456789abcdef" for char in source)):
                raise RuntimeError("优化组件来源与冻结预览不一致，请重新计算。")
        if expected and allocation_inputs_digest(locked_allocation_owners(dao)) != expected:
            raise RuntimeError("直伤优化期间锁定配装已改变，请重新计算。")
