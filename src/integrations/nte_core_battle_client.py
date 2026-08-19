# nte-core 战斗记录与逐击轴查询接口。
"""Battle record and hit-axis queries for the nte-core client."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.integrations.nte_core_protocol import JsonObject


class NteCoreBattleQueryMixin:
    """Keep battle-specific RPC validation outside the process client module."""

    def call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
    ) -> JsonObject:
        raise NotImplementedError

    def get_battle_record(
        self,
        *,
        battle_record_id: str | None = None,
        subtract_time_stop: bool = True,
    ) -> JsonObject | None:
        return self.call(
            "battle.get_record",
            {
                "battle_record_id": battle_record_id,
                "subtract_time_stop": subtract_time_stop,
            },
        )

    def get_battle_axis(
        self,
        *,
        battle_record_id: str,
        cursor: str | None = None,
        limit: int = 500,
    ) -> JsonObject | None:
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
        return self.call(
            "battle.get_axis",
            {
                "battle_record_id": normalized_record_id,
                "cursor": normalized_cursor,
                "limit": limit,
            },
        )
