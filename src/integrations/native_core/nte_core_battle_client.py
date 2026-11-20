# nte-core 战斗记录与逐击轴查询辅助函数（无 Mixin 继承耦合）。
"""Battle record and hit-axis RPC query helpers for the nte-core client."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from src.integrations.native_core.nte_core_protocol import JsonObject

RpcCaller = Callable[[str, Mapping[str, Any] | None], JsonObject]


def query_battle_record(
    call_rpc: RpcCaller,
    *,
    battle_record_id: str | None = None,
    subtract_time_stop: bool = True,
) -> JsonObject | None:
    """Query one battle summary record from nte-core via explicit RPC delegation."""

    return call_rpc(
        "battle.get_record",
        {
            "battle_record_id": battle_record_id,
            "subtract_time_stop": subtract_time_stop,
        },
    )


def query_battle_axis(
    call_rpc: RpcCaller,
    *,
    battle_record_id: str,
    cursor: str | None = None,
    limit: int = 500,
) -> JsonObject | None:
    """Query one paginated battle hit-axis slice from nte-core via explicit RPC delegation."""

    if (
        not isinstance(limit, int)
        or isinstance(limit, bool)
        or not 1 <= limit <= 500
    ):
        raise ValueError("battle axis limit must be between 1 and 500")
    normalized_record_id = str(battle_record_id).strip()
    if not normalized_record_id:
        raise ValueError("battle_record_id must not be empty")
    normalized_cursor = None if cursor is None else str(cursor).strip()
    if normalized_cursor == "":
        raise ValueError("battle axis cursor must not be empty")
    return call_rpc(
        "battle.get_axis",
        {
            "battle_record_id": normalized_record_id,
            "cursor": normalized_cursor,
            "limit": limit,
        },
    )
