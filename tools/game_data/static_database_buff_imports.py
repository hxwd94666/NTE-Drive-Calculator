# 从 combat_blueprint_* 规范化 Buff/GE 的持续、叠层、属性修正和触发关系。
"""Build-time normalized BuffDefinition importer."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from tools.game_data.static_database_build_support import (
    StaticDatabaseError,
    canonical_json,
)


def _normalized_asset_path(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw.startswith("/Game/"):
        return None
    head, separator, _tail = raw.rpartition(".")
    return head if separator else raw


def _object_asset_path(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    return _normalized_asset_path(value.get("ObjectPath"))


def _walk_mappings(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        yield value
        for nested in value.values():
            yield from _walk_mappings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_mappings(nested)


def _first_object_path(value: Any) -> str | None:
    for row in _walk_mappings(value):
        path = _object_asset_path(row)
        if path is not None:
            return path
    return None


def _attribute_id(modifier: Mapping[str, Any]) -> str | None:
    attribute = modifier.get("Attribute")
    if isinstance(attribute, Mapping):
        name = str(attribute.get("AttributeName") or "").strip()
        if name:
            return name
        nested = attribute.get("Attribute")
        if isinstance(nested, Mapping):
            path = nested.get("Path")
            if isinstance(path, list) and path:
                return str(path[-1]).strip() or None
    return None


def _tags(value: Any, key: str) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ()
    tags = value.get(key)
    if not isinstance(tags, list):
        return ()
    return tuple(dict.fromkeys(
        str(tag).strip() for tag in tags if str(tag).strip()
    ))


def _magnitude_parts(
    modifier: Mapping[str, Any],
) -> tuple[str | None, float | None, str | None, dict[str, Any]]:
    magnitude = modifier.get("ModifierMagnitude")
    payload = dict(magnitude) if isinstance(magnitude, Mapping) else {}
    kind = str(payload.get("MagnitudeCalculationType") or "").strip() or None
    value: float | None = None
    scalable = payload.get("ScalableFloatMagnitude")
    if isinstance(scalable, Mapping) and isinstance(
        scalable.get("Value"), (int, float)
    ):
        value = float(scalable["Value"])
    calculation_path = None
    custom = payload.get("CustomMagnitude")
    if isinstance(custom, Mapping):
        calculation_path = _first_object_path(
            custom.get("CalculationClassMagnitude")
        )
    return kind, value, calculation_path, payload


def _semantic_rows(connection, asset_path: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT property_path, property_name, value_json
        FROM combat_blueprint_semantic_property
        WHERE source_asset_path = ?
        ORDER BY length(property_path), property_path, ordinal
        """,
        (asset_path,),
    )
    return [
        {
            "property_path": str(row[0]),
            "property_name": str(row[1]),
            "value": json.loads(str(row[2])),
        }
        for row in rows
    ]


def _first_semantic(
    rows: list[dict[str, Any]],
    property_name: str,
) -> Any:
    row = next(
        (item for item in rows if item["property_name"] == property_name),
        None,
    )
    return None if row is None else row["value"]


class BuffImportMixin:
    connection: Any

    def _import_buff_definitions(self) -> None:
        available = {
            str(row[0]).casefold()
            for row in self.connection.execute(
                """
                SELECT asset_path FROM combat_blueprint_asset
                WHERE asset_kind IN ('buff', 'gameplay_effect')
                """
            )
        }
        assets = self.connection.execute(
            """
            SELECT asset_path, asset_name, asset_kind, character_id, source_file_id
            FROM combat_blueprint_asset
            WHERE asset_kind IN ('buff', 'gameplay_effect')
            ORDER BY asset_path
            """
        ).fetchall()
        for asset in assets:
            asset_path = str(asset[0])
            semantic_rows = _semantic_rows(self.connection, asset_path)
            duration_policy = _first_semantic(semantic_rows, "DurationPolicy")
            duration_magnitude = _first_semantic(
                semantic_rows, "DurationMagnitude"
            )
            period = _first_semantic(semantic_rows, "Period")
            stacking_type = _first_semantic(semantic_rows, "StackingType")
            stack_limit = _first_semantic(semantic_rows, "StackLimitCount")
            self.connection.execute(
                """
                INSERT INTO buff_definition(
                    asset_path, definition_id, definition_kind,
                    owner_character_id, duration_policy,
                    duration_magnitude_json, period_json, stacking_type,
                    stack_limit_count, source_file_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_path,
                    str(asset[1]),
                    str(asset[2]),
                    asset[3],
                    None if duration_policy is None else str(duration_policy),
                    None if duration_magnitude is None else canonical_json(duration_magnitude),
                    None if period is None else canonical_json(period),
                    None if stacking_type is None else str(stacking_type),
                    int(stack_limit) if isinstance(stack_limit, (int, float)) else None,
                    int(asset[4]),
                ),
            )
            self._import_buff_modifiers(asset_path, semantic_rows)
            self._import_buff_triggers(asset_path, semantic_rows)
        self._import_combat_effect_buff_links(available)

    def _import_buff_modifiers(
        self,
        asset_path: str,
        semantic_rows: list[dict[str, Any]],
    ) -> None:
        ordinal = 0
        for semantic in semantic_rows:
            property_name = semantic["property_name"]
            if property_name not in {"Modifiers", "CustomApplicationModifierInfos"}:
                continue
            groups = semantic["value"]
            if not isinstance(groups, list):
                raise StaticDatabaseError(
                    f"Buff {property_name} 不是数组：{asset_path}"
                )
            normalized_groups = (
                ((0, None, groups),)
                if property_name == "Modifiers"
                else tuple(
                    (
                        group_ordinal,
                        _first_object_path(group.get("ApplicationRequirement")),
                        group.get("GameplayModifierInfos") or (),
                    )
                    for group_ordinal, group in enumerate(groups)
                    if isinstance(group, Mapping)
                )
            )
            for group_ordinal, requirement_path, modifiers in normalized_groups:
                if not isinstance(modifiers, (list, tuple)):
                    raise StaticDatabaseError(
                        f"Buff Modifier 组不是数组：{asset_path}/{group_ordinal}"
                    )
                for modifier in modifiers:
                    if not isinstance(modifier, Mapping):
                        raise StaticDatabaseError(
                            f"Buff Modifier 不是对象：{asset_path}/{ordinal}"
                        )
                    self._insert_buff_modifier(
                        asset_path=asset_path,
                        ordinal=ordinal,
                        group_ordinal=group_ordinal,
                        requirement_path=requirement_path,
                        modifier=modifier,
                        source_property_path=semantic["property_path"],
                    )
                    ordinal += 1

    def _insert_buff_modifier(
        self,
        *,
        asset_path: str,
        ordinal: int,
        group_ordinal: int,
        requirement_path: str | None,
        modifier: Mapping[str, Any],
        source_property_path: str,
    ) -> None:
        kind, value, calculation_path, magnitude = _magnitude_parts(modifier)
        self.connection.execute(
            """
            INSERT INTO buff_modifier(
                asset_path, ordinal, property_id, modifier_operation,
                magnitude_kind, magnitude_value, calculation_asset_path,
                magnitude_json, source_property_path,
                modifier_group_ordinal,
                application_requirement_asset_path,
                source_require_tags_json, source_ignore_tags_json,
                target_require_tags_json, target_ignore_tags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_path,
                ordinal,
                _attribute_id(modifier),
                str(modifier.get("ModifierOp") or "") or None,
                kind,
                value,
                calculation_path,
                canonical_json(magnitude),
                source_property_path,
                group_ordinal,
                requirement_path,
                canonical_json(_tags(modifier.get("SourceTags"), "RequireTags")),
                canonical_json(_tags(modifier.get("SourceTags"), "IgnoreTags")),
                canonical_json(_tags(modifier.get("TargetTags"), "RequireTags")),
                canonical_json(_tags(modifier.get("TargetTags"), "IgnoreTags")),
            ),
        )

    def _import_buff_triggers(
        self,
        asset_path: str,
        semantic_rows: list[dict[str, Any]],
    ) -> None:
        ordinal = 0
        for semantic in semantic_rows:
            if semantic["property_name"] != "BuffEventEffectData":
                continue
            events = semantic["value"]
            if not isinstance(events, list):
                raise StaticDatabaseError(
                    f"BuffEventEffectData 不是数组：{asset_path}"
                )
            for event in events:
                if not isinstance(event, Mapping):
                    continue
                for effect in event.get("ExecGEs") or ():
                    if not isinstance(effect, Mapping):
                        continue
                    target_path = _object_asset_path(effect.get("GEClass"))
                    if target_path is None:
                        continue
                    requirement_path = _first_object_path(
                        event.get("ApplicationRequirement")
                    )
                    duration = effect.get("ModifyDuration")
                    self.connection.execute(
                        """
                        INSERT INTO buff_trigger_effect
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            asset_path,
                            ordinal,
                            str(event.get("EventType") or "unknown"),
                            str(event.get("EffectType") or "unknown"),
                            target_path,
                            int(effect["StackCount"])
                            if isinstance(effect.get("StackCount"), int)
                            else None,
                            int(bool(event.get("BySelf"))),
                            int(bool(event.get("TargetTrigger"))),
                            canonical_json(duration)
                            if duration is not None
                            else None,
                            requirement_path,
                        ),
                    )
                    ordinal += 1

    def _import_combat_effect_buff_links(
        self,
        available: set[str],
    ) -> None:
        for suit_id, count, raw_path in self.connection.execute(
            """
            SELECT suit_id, required_count, buff_object_path
            FROM equipment_suit_effect
            WHERE buff_object_path IS NOT NULL
            ORDER BY suit_id, required_count
            """
        ):
            target = _normalized_asset_path(raw_path)
            if target is not None:
                self._insert_effect_link(
                    f"equipment_suit:{suit_id}:{count}",
                    0,
                    "buff_object",
                    target,
                    available,
                )
        for pack_id, star_level, raw_json in self.connection.execute(
            """
            SELECT star_pack_id, star_level, buffs_json
            FROM fork_star_level ORDER BY star_pack_id, star_level
            """
        ):
            for ordinal, buff in enumerate(json.loads(str(raw_json))):
                raw_object = buff.get("BuffObject") if isinstance(buff, Mapping) else None
                target = _normalized_asset_path(
                    raw_object.get("AssetPathName")
                    if isinstance(raw_object, Mapping)
                    else None
                )
                if target is not None:
                    self._insert_effect_link(
                        f"fork_star:{pack_id}:{star_level}",
                        ordinal,
                        "fork_buff",
                        target,
                        available,
                    )
        ge_paths = {
            str(row[0]): _normalized_asset_path(row[1])
            for row in self.connection.execute(
                """
                SELECT gameplay_effect_id, class_path
                FROM gameplay_effect_catalog
                """
            )
        }
        for character_id, effect_id, raw_ge_json, raw_modify_json in self.connection.execute(
            """
            SELECT character_id, effect_id, gameplay_effect_ids_json,
                   modify_data_json
            FROM character_awaken_effect ORDER BY character_id, ordinal
            """
        ):
            definition_id = f"character_awaken:{character_id}:{effect_id}"
            ordinal = 0
            for ge_id in json.loads(str(raw_ge_json)):
                target = ge_paths.get(str(ge_id))
                if target is not None:
                    self._insert_effect_link(
                        definition_id,
                        ordinal,
                        "gameplay_effect",
                        target,
                        available,
                    )
                    ordinal += 1
            for modify_row in json.loads(str(raw_modify_json)):
                if not isinstance(modify_row, Mapping):
                    continue
                buff = modify_row.get("Buff")
                target = _normalized_asset_path(
                    buff.get("AssetPathName")
                    if isinstance(buff, Mapping)
                    else None
                )
                if target is None:
                    continue
                self._insert_effect_link(
                    definition_id,
                    ordinal,
                    "buff_object",
                    target,
                    available,
                )
                ordinal += 1

    def _insert_effect_link(
        self,
        definition_id: str,
        ordinal: int,
        link_kind: str,
        target: str,
        available: set[str],
    ) -> None:
        self.connection.execute(
            "INSERT INTO combat_effect_buff_link VALUES (?, ?, ?, ?, ?)",
            (
                definition_id,
                ordinal,
                link_kind,
                target,
                int(target.casefold() in available),
            ),
        )
