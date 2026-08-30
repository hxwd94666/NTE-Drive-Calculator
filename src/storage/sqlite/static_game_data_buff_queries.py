# 查询规范化 Buff、GameplayEffect 与 RogueLike 属性修正。
"""Read-only projections for combat effect inference services."""

from __future__ import annotations

import json
from typing import Any

from .protocols import StaticDataDaoMixinHost


class StaticGameDataBuffQueriesMixin(StaticDataDaoMixinHost):
    def get_combat_curve(
        self,
        curve_table_asset_path: str,
        curve_id: str,
    ) -> dict[str, Any] | None:
        """按曲线表资产和行名返回通用战斗曲线。"""

        table_path = str(curve_table_asset_path).strip().removesuffix(".0")
        normalized_id = str(curve_id).strip()
        if not table_path or not normalized_id:
            raise ValueError("curve_table_asset_path 和 curve_id 不能为空")
        curve = self._one(
            """
            SELECT curve_table_asset_path, curve_id, interpolation_mode,
                   default_value, pre_infinity_extrapolation,
                   post_infinity_extrapolation, source_row_id
            FROM combat_curve
            WHERE curve_table_asset_path = ? AND curve_id = ?
            """,
            (table_path, normalized_id),
        )
        if curve is None:
            return None
        curve["points"] = self._rows(
            """
            SELECT ordinal, source_time, value
            FROM combat_curve_point
            WHERE curve_table_asset_path = ? AND curve_id = ?
            ORDER BY ordinal
            """,
            (table_path, normalized_id),
        )
        return curve

    def get_equipment_modify_pack(
        self,
        modify_pack_id: str,
    ) -> dict[str, Any] | None:
        """返回空幕套装常驻属性包，供冻结配装与战报推算复用。"""

        normalized = str(modify_pack_id).strip()
        if not normalized:
            raise ValueError("modify_pack_id 不能为空")
        pack = self._one(
            """
            SELECT modify_pack_id, conditions_json, source_row_id
            FROM equipment_modify_pack WHERE modify_pack_id = ?
            """,
            (normalized,),
        )
        if pack is None:
            return None
        pack["conditions"] = json.loads(pack.pop("conditions_json") or "[]")
        pack["modifiers"] = self._rows(
            """
            SELECT ordinal, property_id, value, operation, sort_key
            FROM equipment_modify_value
            WHERE modify_pack_id = ? ORDER BY ordinal
            """,
            (normalized,),
        )
        return pack

    def get_equipment_buff_curve(self, curve_id: str) -> dict[str, Any] | None:
        """返回空幕 Buff 曲线；当前正式曲线通常是单点常量。"""

        normalized = str(curve_id).strip()
        if not normalized:
            raise ValueError("curve_id 不能为空")
        curve = self._one(
            """
            SELECT curve_id, interpolation_mode, default_value,
                   pre_infinity_extrapolation, post_infinity_extrapolation,
                   source_row_id
            FROM equipment_buff_curve WHERE curve_id = ?
            """,
            (normalized,),
        )
        if curve is None:
            return None
        curve["points"] = self._rows(
            """
            SELECT ordinal, source_time, value
            FROM equipment_buff_curve_point
            WHERE curve_id = ? ORDER BY ordinal
            """,
            (normalized,),
        )
        return curve

    def list_character_bound_modifier_effects(
        self,
        character_id: int,
    ) -> list[dict[str, Any]]:
        """返回技能事件直接施加、且确实修改属性的 Buff/GE。"""

        return self._rows(
            """
            SELECT b.binding_kind, b.input_id, b.ability_id,
                   b.ability_asset_path, e.event_tag,
                   e.effect_asset_path, e.effect_id,
                   e.target_type_asset_path
            FROM character_combat_ability_binding AS b
            JOIN combat_ability_effect_binding AS e
              ON e.ability_asset_path = b.ability_asset_path
            WHERE b.character_id = ?
              AND EXISTS (
                  SELECT 1 FROM buff_modifier AS m
                  WHERE m.asset_path = e.effect_asset_path
              )
            UNION ALL
            SELECT b.binding_kind, b.input_id, b.ability_id,
                   b.ability_asset_path, NULL,
                   b.ability_asset_path, b.ability_id, NULL
            FROM character_combat_ability_binding AS b
            WHERE b.character_id = ? AND b.binding_kind = 'passive_buff'
              AND EXISTS (
                  SELECT 1 FROM buff_modifier AS m
                  WHERE m.asset_path = b.ability_asset_path
              )
            ORDER BY binding_kind, ability_id, event_tag, effect_id
            """,
            (int(character_id), int(character_id)),
        )

    def get_buff_definition(self, asset_path: str) -> dict[str, Any] | None:
        """返回一个 Buff/GE 的持续、叠层、属性修正和触发关系。"""

        normalized = str(asset_path).strip()
        if not normalized:
            raise ValueError("asset_path 不能为空")
        definition = self._one(
            """
            SELECT asset_path, definition_id, definition_kind,
                   owner_character_id, duration_policy,
                   duration_magnitude_json, period_json, stacking_type,
                   stack_limit_count, source_file_id
            FROM buff_definition WHERE asset_path = ?
            """,
            (normalized,),
        )
        if definition is None:
            return None
        for key in ("duration_magnitude_json", "period_json"):
            raw = definition.pop(key)
            definition[key.removesuffix("_json")] = (
                None if raw is None else json.loads(raw)
            )
        definition["modifiers"] = self._rows(
            """
            SELECT ordinal, property_id, modifier_operation,
                   magnitude_kind, magnitude_value, calculation_asset_path,
                   magnitude_json, source_property_path,
                   modifier_group_ordinal,
                   application_requirement_asset_path,
                   source_require_tags_json, source_ignore_tags_json,
                   target_require_tags_json, target_ignore_tags_json
            FROM buff_modifier WHERE asset_path = ? ORDER BY ordinal
            """,
            (normalized,),
        )
        for modifier in definition["modifiers"]:
            modifier["magnitude"] = json.loads(modifier.pop("magnitude_json"))
            for key in (
                "source_require_tags_json",
                "source_ignore_tags_json",
                "target_require_tags_json",
                "target_ignore_tags_json",
            ):
                modifier[key.removesuffix("_json")] = tuple(
                    json.loads(modifier.pop(key))
                )
        definition["triggers"] = self._rows(
            """
            SELECT ordinal, event_type, effect_type,
                   target_effect_asset_path, stack_count, by_self,
                   target_trigger, modify_duration_json,
                   application_requirement_asset_path
            FROM buff_trigger_effect WHERE asset_path = ? ORDER BY ordinal
            """,
            (normalized,),
        )
        for trigger in definition["triggers"]:
            trigger["by_self"] = bool(trigger["by_self"])
            trigger["target_trigger"] = bool(trigger["target_trigger"])
            raw = trigger.pop("modify_duration_json")
            trigger["modify_duration"] = None if raw is None else json.loads(raw)
        return definition

    def list_combat_effect_buff_links(
        self,
        effect_definition_id: str,
    ) -> list[dict[str, Any]]:
        """返回装备套装、弧盘精炼或觉醒绑定的运行时 Buff/GE。"""

        rows = self._rows(
            """
            SELECT effect_definition_id, ordinal, link_kind,
                   target_asset_path, target_available
            FROM combat_effect_buff_link
            WHERE effect_definition_id = ? ORDER BY ordinal
            """,
            (str(effect_definition_id).strip(),),
        )
        for row in rows:
            row["target_available"] = bool(row["target_available"])
        return rows

    def get_roguelike_modifier(
        self,
        modifier_id: str,
    ) -> dict[str, Any] | None:
        """返回一个 RogueLike 属性包修正及其生效条件。"""

        profile = self._one(
            """
            SELECT modifier_id, conditions_json, source_row_id
            FROM roguelike_modifier_profile WHERE modifier_id = ?
            """,
            (str(modifier_id).strip(),),
        )
        if profile is None:
            return None
        profile["conditions"] = json.loads(profile.pop("conditions_json"))
        profile["properties"] = self._rows(
            """
            SELECT ordinal, property_id, modifier_operation,
                   property_value, sort_key
            FROM roguelike_modifier_property
            WHERE modifier_id = ? ORDER BY ordinal
            """,
            (profile["modifier_id"],),
        )
        return profile
