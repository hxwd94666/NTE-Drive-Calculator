# 从同一冻结账号副本准备前序决定与评分，保留真正未决定项供智能处理。
from dataclasses import dataclass
from concurrent.futures import CancelledError

from src.domain.equipment.management_settings import MatchingScope, normalize_warehouse_config
from src.domain.equipment.warehouse_decisions import DecisionStatus, manual_decision, scoring_decision
from src.services.identification.scoring_scope import prepare_management_scoring
from src.services.allocation.inventory import SqliteAllocationInventory
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.models.equipment import Drive, Tape


@dataclass(frozen=True)
class PredecessorBatch:
    snapshot_id: int
    items: tuple
    raw_by_uid: dict
    decisions: dict
    scorer: object = None


def evaluate_predecessors(
    database, static_database, config_dir, config, *, user_targets=None, cancel_check=lambda: False
):
    def checkpoint():
        if cancel_check():
            raise CancelledError()

    checkpoint()
    config = normalize_warehouse_config(config)
    with UserDataDao(database) as dao, dao.read_consistent_state(), StaticGameDataDao(static_database) as static:
        snapshot = dao.current_inventory_snapshot_id()
        if snapshot is None:
            raise ValueError("尚无稳定库存")
        projected = SqliteAllocationInventory(dao, static).build(snapshot)
        rows = dao.list_inventory_items_with_runtime_state(snapshot)
        locked = dao.list_allocation_locked_equipment_owners()
    raw = {
        f"nte-{'module' if row['kind'] == 'module' else 'core'}-{row['uid_slot']}-{row['uid_serial']}": row
        for row in rows
    }
    protected = {
        f"nte-{'module' if row['kind'] == 'module' else 'core'}-{row['uid_slot']}-{row['uid_serial']}" for row in locked
    }
    items = tuple(Drive(**row) if row["item_type"] == "drive" else Tape(**row) for row in projected.items)
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
    if remaining and any(config[name]["enabled"] for name in ("lock", "discard")):
        scorer, context, roles, incomplete = prepare_management_scoring(
            config,
            items,
            config_dir=config_dir,
            database=database,
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
    return PredecessorBatch(snapshot, items, raw, decisions, scorer)
