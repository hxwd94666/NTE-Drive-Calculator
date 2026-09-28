# 将角色推荐权重查询从静态库主 DAO 中拆出，并提供批量读取入口。
"""Read-only recommended-weight queries for the static database."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.domain.recommended_weights import workshop_weight_source_ids

from .protocols import StaticDataDaoMixinHost


class StaticGameDataWeightQueriesMixin(StaticDataDaoMixinHost):
    """Expose workshop recommended weights and their batch projections."""

    def get_character_recommended_weights(self, character_id: int) -> dict[str, Any] | None:
        """精确工坊记录优先；主角另一形态的工坊记录优先于通用发行兜底。"""

        fallback = None
        for source_id in workshop_weight_source_ids(character_id):
            row = self._get_character_recommended_weights(source_id)
            if source_id == int(character_id):
                fallback = row
            if row and row.get("properties") and row.get("source_kind") != "default":
                return {**row, "character_id": int(character_id)}
        return fallback

    def _get_character_recommended_weights(self, character_id: int) -> dict[str, Any] | None:
        """读取开发期写入静态库的推荐权重；运行时不会调用外部 API。"""

        recommendation = self._one(
            """SELECT character_id, source_kind, source_item_id, source_name,
                      source_updated_at_utc
               FROM character_weight_recommendation WHERE character_id = ?""",
            (int(character_id),),
        )
        if recommendation is None:
            return None
        properties = self._rows(
            """SELECT property_id, weight, main_weight, ordinal
               FROM character_weight_recommendation_property
               WHERE character_id = ? ORDER BY ordinal""",
            (int(character_id),),
        )
        recommendation["properties"] = properties
        recommendation["property_weights"] = {
            row["property_id"]: float(row["weight"])
            for row in properties if float(row["weight"]) > 0
        }
        recommendation["main_property_weights"] = {
            row["property_id"]: float(row["main_weight"])
            for row in properties if float(row["main_weight"]) > 0
        }
        return recommendation

    def list_character_recommended_weights(self) -> list[dict[str, Any]]:
        return [
            recommendation
            for row in self._rows(
                "SELECT character_id FROM character_weight_recommendation ORDER BY character_id"
            )
            if (recommendation := self.get_character_recommended_weights(int(row["character_id"])))
            is not None
        ]

    def map_character_recommended_weights(
        self,
        character_ids: Iterable[int],
    ) -> dict[int, dict[str, Any] | None]:
        """批量返回每个角色的推荐权重，语义与逐角色读取一致（含主角双形态回退）。"""

        records = self._recommended_weight_records()
        resolved: dict[int, dict[str, Any] | None] = {}
        for raw_id in character_ids:
            character_id = int(raw_id)
            fallback: dict[str, Any] | None = None
            chosen: dict[str, Any] | None = None
            for source_id in workshop_weight_source_ids(character_id):
                row = records.get(int(source_id))
                if source_id == character_id:
                    fallback = row
                if row and row.get("properties") and row.get("source_kind") != "default":
                    chosen = {**row, "character_id": character_id}
                    break
            resolved[character_id] = chosen if chosen is not None else fallback
        return resolved

    def _recommended_weight_records(self) -> dict[int, dict[str, Any]]:
        """一次读回全部推荐权重行与属性，供批量映射复用。"""

        properties: dict[int, list[dict[str, Any]]] = {}
        for row in self._rows(
            """
            SELECT character_id, property_id, weight, main_weight, ordinal
            FROM character_weight_recommendation_property
            ORDER BY character_id, ordinal
            """
        ):
            properties.setdefault(int(row.pop("character_id")), []).append(row)
        records: dict[int, dict[str, Any]] = {}
        for row in self._rows(
            """
            SELECT character_id, source_kind, source_item_id, source_name,
                   source_updated_at_utc
            FROM character_weight_recommendation
            """
        ):
            character_id = int(row["character_id"])
            rows = properties.get(character_id, [])
            row["properties"] = rows
            row["property_weights"] = {
                str(item["property_id"]): float(item["weight"])
                for item in rows
                if float(item["weight"]) > 0
            }
            row["main_property_weights"] = {
                str(item["property_id"]): float(item["main_weight"])
                for item in rows
                if float(item["main_weight"]) > 0
            }
            records[character_id] = row
        return records
