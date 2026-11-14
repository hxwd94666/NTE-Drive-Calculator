# 读取静态库中可确认的套装无条件常驻属性。
"""Suit permanent-property projection from the official static catalog."""

from __future__ import annotations

import json
import math
from typing import Any


class StaticGameDataSuitQueriesMixin:
    def list_suit_permanent_reviews(self) -> dict[str, dict[str, str]] | None:
        if not self._rows("SELECT 1 FROM sqlite_master WHERE name='equipment_suit_effect_review'"):
            return None
        result: dict[str, dict[str, str]] = {}
        for row in self._rows("SELECT suit_id,required_count,classification FROM equipment_suit_effect_review"):
            result.setdefault(str(row['suit_id']), {})[str(row['required_count'])] = row['classification']
        return result

    def list_suit_permanent_properties(self) -> dict[str, list[dict[str, Any]]]:
        """Return only unconditional additive suit properties with closed curves."""
        result: dict[str, list[dict[str, Any]]] = {}
        if self._rows("SELECT 1 FROM sqlite_master WHERE name='equipment_suit_permanent_property'"):
            for row in self._rows("SELECT p.*,r.source_effect_id FROM equipment_suit_permanent_property p "
                    "JOIN equipment_suit_effect_review r USING(suit_id,required_count) ORDER BY suit_id,required_count,property_id"):
                result.setdefault(str(row['suit_id']), []).append(row)
            return result
        effects = self._rows(
            "SELECT suit_id, required_count, modify_pack_id, buff_object_path "
            "FROM equipment_suit_effect ORDER BY suit_id, required_count"
        )
        for effect in effects:
            suit_id = str(effect["suit_id"])
            count = int(effect["required_count"])
            rows: list[dict[str, Any]] = []
            if effect["modify_pack_id"] and effect["modify_pack_id"] != "None":
                pack = self._rows("SELECT conditions_json FROM equipment_modify_pack WHERE modify_pack_id=?",
                                  (effect["modify_pack_id"],))
                if len(pack) != 1 or json.loads(pack[0]["conditions_json"]) != []:
                    continue
                for value in self._rows(
                    "SELECT property_id, value, operation FROM equipment_modify_value "
                    "WHERE modify_pack_id=? ORDER BY ordinal", (effect["modify_pack_id"],)
                ):
                    if value["operation"] == "MODIFY_MODOP_ADDITIVE" and math.isfinite(float(value["value"])):
                        rows.append({"property_id": str(value["property_id"]), "value": float(value["value"]),
                                     "unit": "ratio", "source_effect_id": f"equipment_suit:{suit_id}:{count}"})
            elif effect["buff_object_path"]:
                definition_id = f"equipment_suit:{suit_id}:{count}"
                roots = self._rows(
                    "SELECT target_asset_path FROM combat_effect_buff_link "
                    "WHERE effect_definition_id=? AND link_kind='buff_object' AND target_available=1", (definition_id,)
                )
                if len(roots) == 1:
                    root = str(roots[0]["target_asset_path"])
                    prefix = root + "_"
                    modifiers = self._rows(
                        "SELECT asset_path, property_id, ordinal, modifier_operation, magnitude_json, application_requirement_asset_path, "
                        "source_require_tags_json, source_ignore_tags_json, target_require_tags_json, target_ignore_tags_json "
                        "FROM buff_modifier WHERE asset_path=? OR substr(asset_path,1,?)=? ORDER BY asset_path, ordinal",
                        (root, len(prefix), prefix),
                    )
                    roots_known = self._rows(
                        "SELECT duration_policy, stack_limit_count FROM buff_definition WHERE asset_path=?", (root,))
                    if len(roots_known) != 1 or roots_known[0]["duration_policy"] != "EGameplayEffectDurationType::Infinite" \
                            or roots_known[0]["stack_limit_count"] != 1:
                        continue
                    for modifier in modifiers:
                        if modifier["modifier_operation"] != "EGameplayModOp::Additive":
                            continue
                        # Infinite 子 Buff 仍可能是条件触发；只接纳根自身或装备开始时无条件自增益。
                        if modifier["asset_path"] != root:
                            starts = self._rows(
                                "SELECT 1 FROM buff_trigger_effect WHERE asset_path=? AND target_effect_asset_path=? "
                                "AND event_type='EBuffEventType::BUFF_EVENT_BEGIN' AND effect_type='EBuffEffectType::BUFF_ADD' "
                                "AND by_self=1 AND target_trigger=0 AND application_requirement_asset_path IS NULL",
                                (root, modifier["asset_path"]))
                            if len(starts) != 1:
                                continue
                        if modifier["application_requirement_asset_path"] or any(
                            json.loads(modifier[field]) != [] for field in (
                                "source_require_tags_json", "source_ignore_tags_json",
                                "target_require_tags_json", "target_ignore_tags_json",
                            )
                        ):
                            continue
                        definitions = self._rows(
                            "SELECT duration_policy, stack_limit_count FROM buff_definition WHERE asset_path=?",
                            (modifier["asset_path"],),
                        )
                        if len(definitions) != 1 or definitions[0]["duration_policy"] != "EGameplayEffectDurationType::Infinite" \
                                or int(definitions[0]["stack_limit_count"] or 0) != 1:
                            continue
                        try:
                            magnitude = json.loads(modifier["magnitude_json"])
                            if magnitude.get("MagnitudeCalculationType") != "EGameplayEffectMagnitudeCalculation::ScalableFloat":
                                continue
                            scalable = magnitude["ScalableFloatMagnitude"]
                            curve = scalable["curve"]
                            if (curve.get("CurveTable") or {}).get("ObjectPath") != "/Game/DataTable/Equipment/CT_Equipmentbuff.0":
                                continue
                            points = self._rows("SELECT value FROM equipment_buff_curve_point WHERE curve_id=?", (curve["RowName"],))
                            values = {float(point["value"]) for point in points}
                            if len(values) != 1:
                                continue
                            value = float(scalable["Value"]) * next(iter(values))
                            if math.isfinite(value):
                                rows.append({"property_id": str(modifier["property_id"]), "value": value,
                                             "unit": "ratio", "source_effect_id": definition_id,
                                             "source_asset_path": modifier["asset_path"], "source_ordinal": modifier["ordinal"]})
                        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                            continue
            if rows:
                bucket = result.setdefault(suit_id, [])
                seen = {(row["required_count"], row["property_id"], row["value"]) for row in bucket}
                for row in rows:
                    projected = {**row, "required_count": count}
                    key = (count, projected["property_id"], projected["value"])
                    if key not in seen:
                        bucket.append(projected)
                        seen.add(key)
        return result
