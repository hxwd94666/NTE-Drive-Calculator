# 提供静态游戏数据库中的装备词条、物品与属性曲线查询。
"""Read-only equipment catalog queries for the static database DAO."""

from __future__ import annotations

from typing import Any


class StaticGameDataEquipmentQueriesMixin:
    """按正式装备及属性身份读取静态资料。"""

    def list_equipment_attributes(self) -> list[dict[str, Any]]:
        """返回可用于装备词条、核心筛选和官方蓝图的属性 ID。"""

        rows = self._rows(
            """
            SELECT attribute_id, display_name_zh, filter_name_zh,
                   random_attribute_name_zh, attribute_type, show_percent,
                   show_outside, show_inside, score, icon_path, source_row_id
            FROM equipment_attribute
            ORDER BY attribute_id
            """
        )
        for row in rows:
            for field in ("show_percent", "show_outside", "show_inside"):
                row[field] = bool(row[field])
        return rows

    def get_equipment_attribute(self, attribute_id: str) -> dict[str, Any] | None:
        """按官方属性 ID 查询装备属性定义。"""

        raw_attribute_id = str(attribute_id).strip()
        if not raw_attribute_id:
            raise ValueError("attribute_id 不能为空")
        return next(
            (
                attribute
                for attribute in self.list_equipment_attributes()
                if attribute["attribute_id"] == raw_attribute_id
            ),
            None,
        )

    def list_equipment_items(self, kind: str | None = None) -> list[dict[str, Any]]:
        if kind not in (None, "module", "core"):
            raise ValueError("equipment kind must be 'module', 'core', or None")
        where = "" if kind is None else "WHERE kind = ?"
        parameters = () if kind is None else (kind,)
        rows = self._rows(
            f"""
            SELECT item_id, kind, quality, name_zh, name_text_table, name_text_key,
                   geometry_id, geometry_enum, grid_count, suit_id, suit_type_enum,
                   max_level, random_base_attribute_pool_id,
                   random_base_attribute_count, random_sub_attribute_pool_id,
                   random_sub_attribute_count, random_sub_attribute_max_count,
                   strength_pack_id, icon_path, plan_icon_path, is_guide_item,
                   source_row_id
            FROM equipment_item
            {where}
            ORDER BY item_id
            """,
            parameters,
        )
        for row in rows:
            row["is_guide_item"] = bool(row["is_guide_item"])
        return rows

    def get_equipment_item(self, item_id: str) -> dict[str, Any] | None:
        """按游戏官方物品 ID 返回一条装备模板。"""

        raw_item_id = str(item_id).strip()
        if not raw_item_id:
            raise ValueError("item_id 不能为空")
        return next(
            (
                item
                for item in self.list_equipment_items()
                if item["item_id"] == raw_item_id
            ),
            None,
        )

    def evaluate_equipment_base_attribute_curve(
        self,
        curve_id: str,
        level: float,
    ) -> float | None:
        """按官方插值模式读取装备主属性在指定等级的数值。"""

        curve = self._one(
            """
            SELECT interpolation_mode, default_value
            FROM equipment_base_attribute_curve
            WHERE curve_id = ?
            """,
            (str(curve_id),),
        )
        if curve is None:
            return None
        points = self._rows(
            """
            SELECT level, value
            FROM equipment_base_attribute_point
            WHERE curve_id = ?
            ORDER BY level
            """,
            (str(curve_id),),
        )
        if not points:
            default_value = curve.get("default_value")
            return None if default_value is None else float(default_value)

        target = float(level)
        if target <= float(points[0]["level"]):
            return float(points[0]["value"])
        if target >= float(points[-1]["level"]):
            return float(points[-1]["value"])

        previous = points[0]
        for current in points[1:]:
            current_level = float(current["level"])
            if target > current_level:
                previous = current
                continue
            if str(curve.get("interpolation_mode") or "") == "RCIM_Constant":
                return float(previous["value"])
            previous_level = float(previous["level"])
            span = current_level - previous_level
            if span <= 0:
                return float(current["value"])
            ratio = (target - previous_level) / span
            return float(previous["value"]) + (
                float(current["value"]) - float(previous["value"])
            ) * ratio
        return float(points[-1]["value"])
