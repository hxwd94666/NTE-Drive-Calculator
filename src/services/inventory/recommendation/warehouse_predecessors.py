# 从同一不可变仓库请求准备前序决定与评分，保留真正未决定项供智能处理。
from src.observability.stage_timings import timed, timed_call
from dataclasses import dataclass
from concurrent.futures import CancelledError

from src.domain.equipment.eligibility.management_settings import MatchingScope, normalize_warehouse_config
from src.domain.equipment.warehouse.warehouse_decisions import DecisionStatus, manual_decision, scoring_decision
from src.services.identification.scoring_scope import prepare_management_scoring
from src.models.equipment import Drive, Tape


@dataclass(frozen=True)
class PredecessorBatch:
    snapshot_id: int
    items: tuple
    raw_by_uid: dict
    decisions: dict
    scorer: object = None


@timed("predecessors.total")
def evaluate_predecessors(
    inputs, static_database, config_dir, config, *, user_targets=None, cancel_check=lambda: False, timings=None
):
    def checkpoint():
        if cancel_check():
            raise CancelledError()

    checkpoint()
    config = normalize_warehouse_config(config)
    data = timed_call(timings, "predecessors.inventory", inputs.values)
    snapshot, rows, locked = inputs.snapshot_id, data['originals'], data['locked_owners']
    raw = {
        f"nte-{'module' if row['kind'] == 'module' else 'core'}-{row['uid_slot']}-{row['uid_serial']}": row
        for row in rows
    }
    protected = {
        f"nte-{'module' if row['kind'] == 'module' else 'core'}-{row['uid_slot']}-{row['uid_serial']}" for row in locked
    }
    items = tuple(Drive(**row) if row["item_type"] == "drive" else Tape(**row) for row in data['projected'])
    if len(raw) != len(items) or {item.uid for item in items} != set(raw):
        raise ValueError("冻结库存身份不一致")
    user_targets = dict(user_targets or {})
    if set(user_targets) - set(raw):
        raise ValueError("手工目标已不在冻结库存")
    decisions = {}
    for item in items:
        checkpoint()
        decisions[item.uid] = manual_decision(
            item, raw[item.uid], config, user_target=user_targets.get(item.uid), protected=item.uid in protected
        )
    remaining = [item for item in items if decisions[item.uid].status == DecisionStatus.UNDECIDED]
    scorer = None
    # 只跳过前序评分裁决；手动／状态保护已冻结，后续智能算法仍独立执行公共评分与配装校验。
    if not config["skip_scoring"] and remaining and any(config[name]["enabled"] for name in ("lock", "discard")):
        scorer, context, roles, incomplete = timed_call(timings, "predecessors.scoring_prepare", prepare_management_scoring,
            config,
            items,
            config_dir=config_dir,
            database=None, frozen_inputs=inputs,
            static_database=static_database,
            scope=MatchingScope.WAREHOUSE,
            cancel_check=cancel_check,
        )
        for item in remaining:
            checkpoint()
            decisions[item.uid] = scoring_decision(
                item,
                raw[item.uid],
                config,
                scorer,
                context,
                roles,
                incomplete_scopes=incomplete,
                protected=item.uid in protected,
            )
    if timings is not None:
        timings.note("predecessors", item_count=len(items), undecided_count=len(remaining),
                     scoring_enabled=scorer is not None)
    return PredecessorBatch(snapshot, items, raw, decisions, scorer)
