# 将 v30 正式副本掉落闭包投影为公共体力计算档位。
"""Read-only progression farming-stage queries for the static database."""

from __future__ import annotations

from typing import Any

from src.domain.progression_stamina import FarmingStage, MaterialYield

from .protocols import StaticDataDaoMixinHost


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"正式养成档位字段 {field} 不是整数")
    return value


class StaticGameDataProgressionQueriesMixin(StaticDataDaoMixinHost):
    """Return only deterministic positive yields from the formal v30 closure."""

    def list_progression_farming_stages(self) -> tuple[FarmingStage, ...]:
        rows = self._rows(
            """
            SELECT activity.clone_id, activity.name_zh,
                   difficulty.difficulty_ordinal,
                   difficulty.difficulty_level,
                   difficulty.team_level,
                   difficulty.stamina_cost,
                   item.item_id, item.quantity
            FROM clone_activity AS activity
            JOIN clone_activity_difficulty AS difficulty
              ON difficulty.clone_id = activity.clone_id
            JOIN clone_drop_projection AS projection
              ON projection.drop_id = difficulty.drop_id
            LEFT JOIN clone_drop_projection_item AS item
              ON item.drop_id = projection.drop_id
            WHERE difficulty.stamina_cost > 0
              AND projection.status IN ('complete', 'partial')
              AND NOT EXISTS (
                  SELECT 1 FROM clone_drop_projection_gap AS gap
                  WHERE gap.drop_id = projection.drop_id
                    AND gap.reason_code <> 'name_missing'
              )
            ORDER BY activity.clone_id, difficulty.difficulty_ordinal,
                     item.item_id
            """
        )
        grouped: dict[tuple[str, int], list[dict[str, object]]] = {}
        for row in rows:
            key = (
                str(row["clone_id"]),
                _integer(row["difficulty_ordinal"], "difficulty_ordinal"),
            )
            grouped.setdefault(key, []).append(row)

        stages: list[FarmingStage] = []
        for (clone_id, difficulty_ordinal), stage_rows in grouped.items():
            first = stage_rows[0]
            yield_items: list[MaterialYield] = []
            for row in stage_rows:
                if row.get("item_id") is None:
                    continue
                quantity = _integer(row.get("quantity"), "quantity")
                if quantity > 0:
                    yield_items.append(MaterialYield(str(row["item_id"]), quantity))
            yields = tuple(yield_items)
            if not yields:
                continue
            identification_level = _integer(
                first["difficulty_level"],
                "difficulty_level",
            )
            stages.append(FarmingStage(
                stage_id=f"{clone_id}:{difficulty_ordinal}",
                label=f"{first['name_zh']} · 鉴别 {identification_level}",
                minimum_hunter_level=_integer(first["team_level"], "team_level"),
                minimum_identification_level=identification_level,
                stamina_cost=_integer(first["stamina_cost"], "stamina_cost"),
                yields=yields,
                source="official_static_drop_projection_v30",
            ))
        return tuple(stages)

    def list_fork_exp_materials(self) -> list[dict[str, Any]]:
        """Return normalized fork EXP materials with official item presentation."""

        if not self._one(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='fork_exp_material'"
        ):
            return []
        materials = self._rows(
            """
            SELECT material.item_id, material.experience_value,
                   item.name_zh, item.quality, item.icon_path,
                   material.source_row_id,
                   source.row_key, source.content_sha256,
                   source.payload_json IS NOT NULL AS payload_preserved,
                   file.relative_path,
                   file.sha256 AS source_file_sha256
            FROM fork_exp_material AS material
            JOIN progression_item AS item ON item.item_id = material.item_id
            JOIN source_row AS source ON source.source_row_id = material.source_row_id
            JOIN source_file AS file ON file.source_file_id = source.source_file_id
            ORDER BY material.experience_value DESC, material.item_id
            """
        )
        costs = self._rows(
            """
            SELECT item_id, cost_item_id, quantity
            FROM fork_exp_material_cost
            ORDER BY item_id, cost_item_id
            """
        )
        by_material: dict[str, list[dict[str, Any]]] = {}
        for cost in costs:
            by_material.setdefault(str(cost["item_id"]), []).append(cost)
        for material in materials:
            material["costs"] = tuple(
                by_material.get(str(material["item_id"]), ())
            )
        return materials

    def list_progression_items(
        self,
        item_ids: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        """Resolve a bounded set of progression item names, quality and icons."""

        normalized = tuple(dict.fromkeys(str(item) for item in item_ids if str(item)))
        if not normalized:
            return []
        placeholders = ",".join("?" for _ in normalized)
        return self._rows(
            f"""
            SELECT item_id, name_zh, quality, icon_path, source_kind
            FROM progression_item
            WHERE item_id IN ({placeholders})
            ORDER BY item_id
            """,
            normalized,
        )
