# 从已发行静态证据复核人工套装审查，不从说明文本猜数值或把触发Buff提升为常驻。
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from typing import Any


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def semantic_digest(evidence: dict) -> str:
    # Buff的source_file_id是一次构建的局部外键，排序增项即可漂移；资产路径、全部字段和来源行哈希仍逐字核对。
    from copy import deepcopy
    normalized = deepcopy(evidence)
    for row in normalized["buffs"]:
        row.pop("source_file_id", None)
    return digest(normalized)


def _rows(connection, sql, args=()):
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in cursor.execute(sql, args)]
    finally:
        cursor.close()


def effect_evidence(connection: sqlite3.Connection, suit_id: str, required_count: int) -> dict:
    """冻结本条效果、修改包或Buff族及曲线；真实源行哈希保留在证据内。"""
    effects = _rows(connection, "SELECT e.*, s.name_zh FROM equipment_suit_effect e "
                    "JOIN equipment_suit s USING(suit_id) WHERE e.suit_id=? AND e.required_count=?",
                    (suit_id, required_count))
    if len(effects) != 1:
        raise ValueError("套装效果身份缺失或重复")
    effect = effects[0]
    effect_id = f"equipment_suit:{suit_id}:{required_count}"
    definitions = _rows(connection, "SELECT * FROM combat_effect_definition WHERE effect_definition_id=?", (effect_id,))
    if len(definitions) != 1 or definitions[0]["owner_kind"] != "equipment_suit":
        raise ValueError("套装正式效果来源缺失")
    evidence = {"effect": effect, "definition": definitions[0], "pack": [], "values": [],
                "links": [], "buffs": [], "modifiers": [], "triggers": [], "curves": {}, "source_rows": []}
    source_ids = {effect["source_row_id"], definitions[0]["source_row_id"]}
    pack = effect["modify_pack_id"]
    if pack and pack != "None":
        evidence["pack"] = _rows(connection, "SELECT * FROM equipment_modify_pack WHERE modify_pack_id=?", (pack,))
        evidence["values"] = _rows(connection, "SELECT * FROM equipment_modify_value WHERE modify_pack_id=? ORDER BY ordinal", (pack,))
        source_ids.update(row["source_row_id"] for row in evidence["pack"])
    if effect["buff_object_path"]:
        evidence["links"] = _rows(connection, "SELECT * FROM combat_effect_buff_link WHERE effect_definition_id=? ORDER BY ordinal", (effect_id,))
        roots = [row["target_asset_path"] for row in evidence["links"]
                 if row["link_kind"] == "buff_object" and row["target_available"]]
        if len(roots) != 1:
            raise ValueError("套装Buff根关联未闭合")
        root = roots[0]
        prefix = root + "_"
        for table, key, order in [("buff_definition", "buffs", "asset_path"),
                                  ("buff_modifier", "modifiers", "asset_path,ordinal"),
                                  ("buff_trigger_effect", "triggers", "asset_path,ordinal")]:
            evidence[key] = _rows(connection, f"SELECT * FROM {table} WHERE asset_path=? "
                                  f"OR substr(asset_path,1,?)=? ORDER BY {order}", (root, len(prefix), prefix))
        for modifier in evidence["modifiers"]:
            magnitude = json.loads(modifier["magnitude_json"])
            scalable = magnitude.get("ScalableFloatMagnitude") or {}
            curve = (scalable.get("curve") or {}).get("RowName")
            if not curve or curve == "None" or curve in evidence["curves"]:
                continue
            curves = _rows(connection, "SELECT * FROM equipment_buff_curve WHERE curve_id=?", (curve,))
            points = _rows(connection, "SELECT * FROM equipment_buff_curve_point WHERE curve_id=? ORDER BY ordinal", (curve,))
            evidence["curves"][curve] = {"definition": curves, "points": points}
            source_ids.update(row["source_row_id"] for row in curves)
    for source_id in sorted(source_ids):
        rows = _rows(connection, "SELECT source_row_id,source_file_id,row_key,content_sha256 "
                     "FROM source_row WHERE source_row_id=?", (source_id,))
        if len(rows) != 1:
            raise ValueError("套装来源行缺失")
        evidence["source_rows"].append(rows[0])
    return evidence


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("常驻属性数值无效")
    return float(value)


def _buff_property(evidence, selection):
    roots = [row["target_asset_path"] for row in evidence["links"]
             if row["link_kind"] == "buff_object" and row["target_available"]]
    if len(roots) != 1:
        raise ValueError("Buff根身份缺失")
    root = roots[0]
    selected = root + selection["asset_suffix"]
    buffs = {row["asset_path"]: row for row in evidence["buffs"]}
    for asset in {root, selected}:
        if asset not in buffs or buffs[asset]["duration_policy"] != "EGameplayEffectDurationType::Infinite":
            raise ValueError("有限持续时间或缺失Buff不作为常驻")
        if buffs[asset]["stack_limit_count"] != 1:
            raise ValueError("需叠层的Buff不作为固定常驻")
    if selected != root:
        initial = [row for row in evidence["triggers"] if row["asset_path"] == root
                   and row["target_effect_asset_path"] == selected
                   and row["event_type"] == "EBuffEventType::BUFF_EVENT_BEGIN"
                   and row["effect_type"] == "EBuffEffectType::BUFF_ADD"
                   and row["by_self"] == 1 and row["target_trigger"] == 0
                   and not row["application_requirement_asset_path"]]
        if len(initial) != 1:
            raise ValueError("子Buff缺少无条件装备开始关联")
    modifiers = [row for row in evidence["modifiers"] if row["asset_path"] == selected
                 and row["ordinal"] == selection["modifier_ordinal"]]
    if len(modifiers) != 1:
        raise ValueError("指定常驻Modifier缺失或重复")
    modifier = modifiers[0]
    if modifier["modifier_operation"] != "EGameplayModOp::Additive" or modifier["application_requirement_asset_path"]:
        raise ValueError("条件或非加法Modifier未闭合")
    for field in ["source_require_tags_json", "source_ignore_tags_json", "target_require_tags_json", "target_ignore_tags_json"]:
        if json.loads(modifier[field]) != []:
            raise ValueError("有标签要求的Modifier不作为无条件常驻")
    magnitude = json.loads(modifier["magnitude_json"])
    if magnitude["MagnitudeCalculationType"] != "EGameplayEffectMagnitudeCalculation::ScalableFloat":
        raise ValueError("常驻数值不是已支持的直接曲线")
    scalable = magnitude["ScalableFloatMagnitude"]
    curve = scalable["curve"]
    curve_id = curve["RowName"]
    table = (curve.get("CurveTable") or {}).get("ObjectPath")
    if table != "/Game/DataTable/Equipment/CT_Equipmentbuff.0":
        raise ValueError("常驻曲线来源不匹配")
    evidence_curve = evidence["curves"].get(curve_id) or {}
    points = evidence_curve.get("points") or []
    values = {_number(row["value"]) for row in points}
    if len(evidence_curve.get("definition") or []) != 1 or len(values) != 1:
        raise ValueError("常驻曲线缺失或随输入变化，需进一步建模")
    if modifier["property_id"] != selection["property_id"]:
        raise ValueError("审查属性身份改变")
    return {"property_id": modifier["property_id"], "value": _number(scalable["Value"]) * values.pop(),
            "operation": "additive", "unit": "ratio", "source_kind": "buff_modifier",
            "source_key": selected, "source_ordinal": modifier["ordinal"], "curve_id": curve_id}


def project_review(evidence: dict, review: dict) -> tuple[dict, list[dict]]:
    """仅采用本次人工确认且未变化的证据；更新时必须重新审查，不能自动重签新来源。"""
    effect = evidence["effect"]
    if (review["suit_id"], review["required_count"]) != (effect["suit_id"], effect["required_count"]):
        raise ValueError("审查套装身份不匹配")
    if (digest(evidence) != review["evidence_sha256"]
            and semantic_digest(evidence) != review.get("evidence_semantic_sha256")):
        raise ValueError("套装来源已经变化，需重新审查")
    classification = review["classification"]
    if classification not in {"permanent_only", "mixed", "conditional_only"}:
        raise ValueError("套装审查状态无效")
    properties = []
    for selection in review["properties"]:
        if selection["kind"] == "modify_pack":
            if len(evidence["pack"]) != 1 or json.loads(evidence["pack"][0]["conditions_json"]) != []:
                raise ValueError("修改包来源缺失或带条件")
            matches = [row for row in evidence["values"] if row["ordinal"] == selection["ordinal"]]
            if len(matches) != 1 or matches[0]["operation"] != "MODIFY_MODOP_ADDITIVE":
                raise ValueError("修改包属性缺失或操作未支持")
            row = matches[0]
            if row["property_id"] != selection["property_id"]:
                raise ValueError("修改包审查属性身份改变")
            properties.append({"property_id": row["property_id"], "value": _number(row["value"]),
                               "operation": "additive", "unit": "ratio", "source_kind": "modify_pack",
                               "source_key": row["modify_pack_id"], "source_ordinal": row["ordinal"], "curve_id": None})
        elif selection["kind"] == "buff_modifier":
            properties.append(_buff_property(evidence, selection))
        else:
            raise ValueError("未审查的常驻来源类型")
    if bool(properties) != (classification != "conditional_only"):
        raise ValueError("套装审查状态与常驻属性不一致")
    if len({row["property_id"] for row in properties}) != len(properties):
        raise ValueError("重复常驻属性来源需明确合并语义")
    return {**review, "name_zh": effect["name_zh"], "source_effect_definition_id": evidence["definition"]["effect_definition_id"],
            "source_effect_row_id": effect["source_row_id"], "permanent_count": len(properties),
            "has_conditional": classification != "permanent_only", "evidence": evidence}, properties
