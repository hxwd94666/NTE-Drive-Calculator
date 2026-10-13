# 统一适配养成历史的冻结保存快照与只读恢复种子，不写入当前角色配置。
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

from src.domain.cultivation_history import (
    HistoryPayload, HistoryRecord, PAYLOAD_VERSION, freeze_configuration,
)
from src.domain.progression_material_conversion import allocate_owned
from src.domain.progression_stamina import ProgressionStaminaResult
from src.services.progression.batch_planner import CultivationBatchPlan
from src.services.progression.models import (
    CultivationForkSeed, CultivationPlan, CultivationSeed, CultivationStaminaPlan,
)
from src.services.progression.planner_service import CultivationPlannerService

# 计算/聚合语义变化时单独升版；与 JSON 格式版本不是同一身份。
CULTIVATION_ALGORITHM_VERSION = "cultivation-v1"


def _stamina(result: ProgressionStaminaResult | None) -> dict[str, object]:
    if result is None:
        return {"status": "unavailable", "known_stamina": 0, "total_stamina": None, "runs": []}
    return {
        "status": str(result.status), "known_stamina": result.known_stamina,
        "total_stamina": result.total_stamina,
        "runs": [{"stage_id": run.stage_id, "label": run.label, "runs": run.runs,
                  "stamina_per_run": run.stamina_cost_per_run,
                  "total_stamina": run.total_stamina, "source": run.source} for run in result.runs],
    }


def _base(dataset: Mapping[str, object]) -> dict[str, object]:
    return {"version": PAYLOAD_VERSION, "dataset": dict(dataset),
            "algorithm_version": CULTIVATION_ALGORITHM_VERSION}


def single_history_payload(
    configuration_json: str, plan: CultivationPlan, stamina: CultivationStaminaPlan | None,
    dataset: Mapping[str, object],
    *, stamina_item_ids: frozenset[str] | None = None,
) -> HistoryPayload:
    configuration = json.loads(configuration_json)
    target = configuration["targets"][0]
    owned = {item["item_id"]: item["quantity"] for item in configuration["owned_materials"]}
    allocated = allocate_owned({item.item_id: item.quantity for item in plan.totals}, owned)
    eligible = stamina.stamina_item_ids if stamina is not None else (stamina_item_ids or frozenset())
    total = stamina.total if stamina is not None else None
    gaps = [{"line_id": target["line_id"], "reason_code": gap.reason_code, "item_id": gap.item_id}
            for gap in plan.gaps]
    if total is not None:
        gaps.extend({"line_id": None, "reason_code": reason, "item_id": None} for reason in total.gaps)
    else:
        gaps.append({"line_id": None, "reason_code": "stamina_unavailable", "item_id": None})
    snapshot = _base(dataset)
    snapshot.update({
        "materials": [{"item_id": item.item_id, "name": item.name, "required": item.quantity,
                       "allocated_equivalent": allocated.get(item.item_id, 0),
                       "remaining": item.quantity - allocated.get(item.item_id, 0),
                       "stamina_eligible": item.item_id in eligible} for item in plan.totals],
        "target_summaries": [{"line_id": target["line_id"], "character_id": target["character_id"],
                              "status": str(plan.status),
                              "known_stamina": total.known_stamina if total is not None else 0,
                              "total_stamina": total.total_stamina if total is not None else None}],
        "stamina": _stamina(total), "gaps": gaps,
    })
    return HistoryPayload.create(configuration, snapshot)


def batch_history_payload(
    configuration_json: str, plan: CultivationBatchPlan, dataset: Mapping[str, object],
) -> HistoryPayload:
    configuration = json.loads(configuration_json)
    remaining = {item.item_id: item.quantity for item in plan.remaining_totals}
    snapshot = _base(dataset)
    snapshot.update({
        "materials": [{"item_id": item.item_id, "name": item.name, "required": item.quantity,
                       "allocated_equivalent": item.quantity - remaining.get(item.item_id, 0),
                       "remaining": remaining.get(item.item_id, 0),
                       "stamina_eligible": item.item_id in plan.stamina_item_ids}
                      for item in plan.merged_totals],
        "target_summaries": [{"line_id": target.line_id, "character_id": target.character_id,
                              "status": str(target.plan.status),
                              "known_stamina": target.stamina.total.known_stamina,
                              "total_stamina": target.stamina.total.total_stamina}
                             for target in plan.target_plans],
        "stamina": _stamina(plan.combined_stamina),
        "gaps": [{"line_id": gap.line_id, "reason_code": gap.reason_code, "item_id": gap.item_id}
                 for gap in plan.gaps],
    })
    return HistoryPayload.create(configuration, snapshot)


@dataclass(frozen=True, slots=True)
class PreparedHistoryRestore:
    mode: str
    configuration_json: str
    seeds: tuple[CultivationSeed, ...]


def prepare_history_restore(
    service: CultivationPlannerService, record: HistoryRecord,
) -> PreparedHistoryRestore:
    configuration = json.loads(record.payload.configuration_json)
    service.validate_history_configuration(configuration)
    seeds = []
    for target in configuration["targets"]:
        catalog_seed = service.load_seed(target["character_id"])
        levels = {skill["skill_id"]: skill["current_level"] for skill in target["skills"]}
        fork = target["fork"]
        fork_seed = None
        if fork is not None:
            catalog_fork = service.load_fork_seed(fork["fork_id"])
            fork_seed = CultivationForkSeed(
                fork["fork_id"], catalog_fork.fork_name, fork["current_level"], fork["current_stage"],
            )
        seeds.append(replace(
            catalog_seed, current_level=target["current_level"], current_breakthrough_stage=target["current_stage"],
            skills=tuple(replace(skill, current_level=levels[skill.skill_id]) for skill in catalog_seed.skills),
            fork=fork_seed,
        ))
    return PreparedHistoryRestore(record.payload.mode, freeze_configuration(configuration), tuple(seeds))
