# 查询角色输入、技能效果和动画时间的静态 Blueprint 证据。
"""Read-only combat Blueprint projections for services and inference models."""

from __future__ import annotations

import json
from typing import Any

from .protocols import StaticDataDaoMixinHost


class StaticGameDataCombatBlueprintQueriesMixin(StaticDataDaoMixinHost):
    def list_gameplay_effect_tags(
        self,
        gameplay_effect_ids: tuple[str, ...],
    ) -> dict[str, tuple[str, ...]]:
        """Return imported GameplayTags for the requested formal GE classes."""

        effect_ids = tuple(dict.fromkeys(
            str(value).strip() for value in gameplay_effect_ids if str(value).strip()
        ))
        if not effect_ids:
            return {}
        placeholders = ", ".join("?" for _value in effect_ids)
        rows = self._rows(
            f"""
            SELECT effect.gameplay_effect_id, tag.tag_name
            FROM gameplay_effect_catalog AS effect
            JOIN combat_blueprint_tag AS tag
              ON tag.source_asset_path = substr(
                   effect.class_path,
                   1,
                   instr(effect.class_path, '.') - 1
                 )
            WHERE effect.gameplay_effect_id IN ({placeholders})
            ORDER BY effect.gameplay_effect_id, tag.tag_name
            """,
            effect_ids,
        )
        grouped: dict[str, list[str]] = {}
        for row in rows:
            grouped.setdefault(str(row["gameplay_effect_id"]), []).append(
                str(row["tag_name"])
            )
        return {key: tuple(values) for key, values in grouped.items()}

    def gameplay_effect_has_tag(
        self,
        gameplay_effect_id: str,
        tag_name: str,
    ) -> bool:
        """Return whether the formal GE class owns one imported GameplayTag."""

        effect_id = str(gameplay_effect_id).strip()
        normalized_tag = str(tag_name).strip()
        if not effect_id or not normalized_tag:
            raise ValueError("gameplay_effect_id 和 tag_name 不能为空")
        row = self._one(
            """
            SELECT 1 AS matched
            FROM gameplay_effect_catalog AS effect
            JOIN combat_blueprint_tag AS tag
              ON tag.source_asset_path = substr(
                   effect.class_path,
                   1,
                   instr(effect.class_path, '.') - 1
                 )
            WHERE effect.gameplay_effect_id = ? AND tag.tag_name = ?
            LIMIT 1
            """,
            (effect_id, normalized_tag),
        )
        return row is not None

    def list_character_combat_bindings(self, character_id: int) -> list[dict[str, Any]]:
        """Return official active/passive ability bindings for one character."""

        return self._rows(
            """
            SELECT character_id, binding_kind, ordinal, input_id,
                   ability_id, ability_asset_path
            FROM character_combat_ability_binding
            WHERE character_id = ?
            ORDER BY CASE binding_kind
                         WHEN 'active' THEN 0
                         WHEN 'passive' THEN 1
                         ELSE 2
                     END,
                     ordinal
            """,
            (int(character_id),),
        )

    def get_combat_blueprint_asset(self, asset_path: str) -> dict[str, Any] | None:
        """Return one asset with its references, tags and selected semantic fields."""

        normalized = str(asset_path).strip()
        if not normalized:
            raise ValueError("asset_path 不能为空")
        asset = self._one(
            """
            SELECT asset_path, asset_name, asset_type, asset_kind,
                   character_id, source_file_id
            FROM combat_blueprint_asset
            WHERE asset_path = ?
            """,
            (normalized,),
        )
        if asset is None:
            return None
        asset["references"] = self._rows(
            """
            SELECT property_path, ordinal, relation_kind, target_asset_path,
                   target_object_path, target_object_name, target_available
            FROM combat_blueprint_reference
            WHERE source_asset_path = ?
            ORDER BY property_path, ordinal
            """,
            (normalized,),
        )
        for reference in asset["references"]:
            reference["target_available"] = bool(reference["target_available"])
        asset["tags"] = self._rows(
            """
            SELECT property_path, ordinal, tag_name
            FROM combat_blueprint_tag
            WHERE source_asset_path = ?
            ORDER BY property_path, ordinal
            """,
            (normalized,),
        )
        asset["semantic_properties"] = self._rows(
            """
            SELECT property_path, ordinal, property_name, value_json
            FROM combat_blueprint_semantic_property
            WHERE source_asset_path = ?
            ORDER BY property_path, ordinal
            """,
            (normalized,),
        )
        for item in asset["semantic_properties"]:
            item["value"] = json.loads(item.pop("value_json"))
        return asset

    def get_combat_ability_graph(self, ability_asset_path: str) -> dict[str, Any] | None:
        """Return action selectors and event-to-effect bindings for one GA asset."""

        asset = self.get_combat_blueprint_asset(ability_asset_path)
        if asset is None or asset["asset_kind"] != "ability":
            return None
        asset["montages"] = self._rows(
            """
            SELECT ordinal, selector_key, montage_asset_path, montage_object_path
            FROM combat_ability_montage_binding
            WHERE ability_asset_path = ?
            ORDER BY ordinal
            """,
            (ability_asset_path,),
        )
        asset["effects"] = self._rows(
            """
            SELECT event_tag, ordinal, effect_asset_path, effect_id,
                   target_type_asset_path
            FROM combat_ability_effect_binding
            WHERE ability_asset_path = ?
            ORDER BY event_tag, ordinal
            """,
            (ability_asset_path,),
        )
        return asset

    def get_combat_montage(self, asset_path: str) -> dict[str, Any] | None:
        """Return duration, sections and notifies for one animation montage."""

        montage = self._one(
            """
            SELECT asset_path, duration_seconds, blend_in_seconds,
                   blend_out_seconds, frame_rate_numerator,
                   frame_rate_denominator
            FROM combat_montage
            WHERE asset_path = ?
            """,
            (str(asset_path),),
        )
        if montage is None:
            return None
        montage["sections"] = self._rows(
            """
            SELECT ordinal, section_name, next_section_name,
                   start_seconds, end_seconds, linked_animation_asset_path
            FROM combat_montage_section
            WHERE asset_path = ?
            ORDER BY ordinal
            """,
            (str(asset_path),),
        )
        montage["notifies"] = self._rows(
            """
            SELECT ordinal, notify_name, notify_object_path,
                   start_seconds, end_seconds, event_tag, track_index
            FROM combat_montage_notify
            WHERE asset_path = ?
            ORDER BY start_seconds, ordinal
            """,
            (str(asset_path),),
        )
        return montage
