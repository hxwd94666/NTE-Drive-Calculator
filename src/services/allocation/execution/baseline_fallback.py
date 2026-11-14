# 为已验证的本次基础配装签发独立保存凭证，优化失败不要求重算也不伪造伤害收益。
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from src.services.allocation.results.postprocess_facts import (
    allocation_inputs_digest,
    allocation_row_save_projection,
    allocation_save_projection,
    locked_allocation_owners,
)


@dataclass(frozen=True, slots=True)
class BaselinePreviewSource:
    database_path: Path
    dataset_id: str
    snapshot_id: int
    character_id: int
    owners_sha256: str
    metadata_sha256: str
    save_projection_sha256: str


def attach_baseline_fallbacks(plans, baseline_plans, frozen, *, dataset_id, input_identity, database_path, job_facts=None):
    """只签发与本作业B0完全相同的合法方案；不接纳失败响应中的改变或共享轴收益。"""
    identities = dict(frozen.role_character_ids)
    for role, plan in plans.items():
        metadata = plan.get("damage_optimization") or {}
        baseline = baseline_plans.get(role) or {}
        cid = identities.get(role)
        if (not metadata or metadata.get("status") == "completed" or not plan.get("valid")
                or not baseline.get("valid") or plan.get("assigned_tape") is None
                or type(cid) is not int or type(frozen.snapshot_id) is not int):
            continue
        projection = allocation_save_projection(role, plan, frozen.snapshot_id, cid)
        if projection != allocation_save_projection(role, baseline, frozen.snapshot_id, cid):
            continue
        # 基础方案只记录尝试目标与失败原因，绝不沿用别的组的全队收益或交换依赖。
        kept = {key: deepcopy(metadata[key]) for key in (
            "objective", "model_id", "reason", "engine_version", "component_executable_sha256",
            "component_manifest_sha256", "battle_record_id", "anchor_mode", "coverage", "baseline_priority",
        ) if key in metadata}
        kept.update(deepcopy(job_facts or {}))
        kept.update(status="baseline_only", result_kind="baseline", fallback_status=metadata.get("status"),
                    dataset_id=dataset_id, source_snapshot_id=frozen.snapshot_id, character_id=cid,
                    owners_sha256=frozen.owners_sha256, input_identity=deepcopy(input_identity),
                    neighborhood_complete=False)
        plan["damage_optimization"] = kept
        plan["baseline_preview_source"] = BaselinePreviewSource(
            Path(database_path).resolve(), dataset_id, frozen.snapshot_id, cid, frozen.owners_sha256,
            allocation_inputs_digest(kept), allocation_inputs_digest(projection),
        )
    return plans


def baseline_save_available(plan):
    """界面只开放持有本次冻结凭证的B0；保存服务还会重新校验实际内容。"""
    return (isinstance(plan.get("baseline_preview_source"), BaselinePreviewSource)
            and (plan.get("damage_optimization") or {}).get("result_kind") == "baseline")


def verify_baseline_save(dao, rows, *, dataset_id, database_path):
    for row in rows:
        proof = row.get("baseline_preview_source")
        metadata = (row.get("payload") or {}).get("damage_optimization") or {}
        if (not isinstance(proof, BaselinePreviewSource) or proof.dataset_id != dataset_id
                or proof.database_path != Path(database_path).resolve()
                or proof.snapshot_id != row.get("snapshot_id") or proof.character_id != row.get("character_id")
                or metadata.get("status") != "baseline_only" or metadata.get("result_kind") != "baseline"
                or allocation_inputs_digest(metadata) != proof.metadata_sha256
                or allocation_inputs_digest(allocation_row_save_projection(row)) != proof.save_projection_sha256):
            raise RuntimeError("基础配装与本次冻结预览不一致，请重新计算。")
        if allocation_inputs_digest(locked_allocation_owners(dao)) != proof.owners_sha256:
            raise RuntimeError("基础配装生成后锁定方案已改变，请重新计算。")
