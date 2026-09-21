# 校验装备指令的正式身份、网格位置和有界状态批次。
from collections.abc import Mapping, Sequence

from src.integrations.nte_core_protocol import JsonObject

STATE_BATCH_CAPABILITY = "native_equipment_state_batch_v1"
MAX_STATE_OPERATIONS = 16


def equipment_uid(uid: object, field: str) -> JsonObject:
    if not isinstance(uid, Mapping):
        raise ValueError(f"{field} must be an item UID object")
    slot, serial = uid.get("slot"), uid.get("serial")
    for component, value in (("slot", slot), ("serial", serial)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 < value < (1 << 32) - 1:
            raise ValueError(f"{field}.{component} must be an integer in 1..4294967294")
    return {"slot": slot, "serial": serial}


def equipment_grid_position(row: object, column: object) -> tuple[int, int]:
    if (isinstance(row, bool) or not isinstance(row, int)
            or isinstance(column, bool) or not isinstance(column, int)
            or not 1 <= row <= 5 or not 1 <= column <= 5):
        raise ValueError("row and column must be integers in 1..5")
    return row, column


def equipment_state(value: bool, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} must be a boolean")
    return value


def state_batch_params(operations: Sequence[Mapping]) -> JsonObject:
    if not 1 <= len(operations) <= MAX_STATE_OPERATIONS:
        raise ValueError("状态批次必须包含 1 至 16 条指令")
    result = []
    for operation in operations:
        if set(operation) != {"equipment", "field", "value"}:
            raise ValueError("状态指令字段不完整或包含未知字段")
        field = operation["field"]
        if field not in ("locked", "discarded"):
            raise ValueError("状态指令仅支持锁定或弃置")
        result.append({"equipment": equipment_uid(operation["equipment"], "equipment"),
                       "field": field, "value": equipment_state(operation["value"], field)})
    return {"operations": result}
