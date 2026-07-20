# 将全量手柄扫描的视觉识别结果写入 SQLite 背包快照，供计算和自动装配兜底使用。
"""Persist visual full-scan inventory as a non-native SQLite snapshot."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from src.utils.set_name import normalize_set_display_name


class VisionInventorySnapshotError(RuntimeError):
    """A visual-scan item cannot be represented by the supported solver contract."""


_QUALITY = {"gold": "orange", "purple": "purple", "blue": "blue"}
_GEOMETRY = {
    "H_2": "Hen2", "H_3": "Hen3", "H_4": "Hen4",
    "V_2": "Shu2", "V_3": "Shu3", "V_4": "Shu4",
    "Trap_4_H": "Z3", "Trap_4_V": "Z4",
    "L_3_BL": "ZhiJiao1", "L_3_TL": "ZhiJiao2",
    "L_3_TR": "ZhiJiao3", "L_3_BR": "ZhiJiao4",
}
_PROPERTY_IDS = {
    "攻击力": "AtkAdd", "攻击力%": "AtkUp", "暴击率": "CritBase", "暴击率%": "CritBase",
    "暴击伤害": "CritDamageBase", "暴击伤害%": "CritDamageBase", "防御力": "DefAdd", "防御力%": "DefUp",
    "生命值": "HPMaxAdd", "生命值%": "HPMaxUp", "治疗加成": "HealUp", "环合强度": "MagBase",
    "倾陷强度": "UnbalIntensityBase", "伤害增加%": "DamageUpGeneralBase",
    "光属性异能伤害增强": "DamageUpCosmosBase", "光属性异能伤害增强%": "DamageUpCosmosBase",
    "暗属性异能伤害增强": "DamageUpChaosBase", "暗属性异能伤害增强%": "DamageUpChaosBase",
    "咒属性异能伤害增强": "DamageUpIncantationBase", "咒属性异能伤害增强%": "DamageUpIncantationBase",
    "相属性异能伤害增强": "DamageUpLakshanaBase", "相属性异能伤害增强%": "DamageUpLakshanaBase",
    "灵属性异能伤害增强": "DamageUpNatureBase", "灵属性异能伤害增强%": "DamageUpNatureBase",
    "魂属性异能伤害增强": "DamageUpPsycheBase", "魂属性异能伤害增强%": "DamageUpPsycheBase",
    "心灵伤害增强": "DamageUpPsychicallyBase", "心灵伤害增强%": "DamageUpPsychicallyBase",
}
_STAT_LABEL_ALIASES = {
    "爆伤%": "暴击伤害%", "爆伤": "暴击伤害%", "暴击伤害": "暴击伤害%",
    "爆击%": "暴击率%", "爆击": "暴击率%", "暴击率": "暴击率%",
    "伤害增加": "伤害增加%", "伤害%": "伤害增加%", "伤害": "伤害增加%",
    "大攻击": "攻击力%", "大防御": "防御力%", "大生命": "生命值%",
    "小攻击": "攻击力", "小防御": "防御力", "小生命": "生命值",
    "心灵伤害增强": "心灵伤害增强%", "光属性伤害": "光属性异能伤害增强%",
    "暗属性伤害": "暗属性异能伤害增强%", "灵属性伤害": "灵属性异能伤害增强%",
    "咒属性伤害": "咒属性异能伤害增强%", "魂属性伤害": "魂属性异能伤害增强%",
    "相属性伤害": "相属性异能伤害增强%",
}
_PERCENT_PROPERTY_IDS = frozenset(
    value for key, value in _PROPERTY_IDS.items() if key.endswith("%") or key in {"暴击率", "暴击伤害"}
)


def _compact_set_name(value: Any) -> str:
    return "".join(
        char for char in normalize_set_display_name(value)
        if char not in {" ", ":", "：", "·"}
    )


def _stat(label: Any, value: Any) -> dict[str, Any]:
    name = str(label or "").strip().replace("百分比", "%")
    name = _STAT_LABEL_ALIASES.get(name, name)
    property_id = _PROPERTY_IDS.get(name)
    if property_id is None:
        raise VisionInventorySnapshotError(f"视觉扫描包含未支持的词条：{name or '<empty>'}")
    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as exc:
        raise VisionInventorySnapshotError(f"视觉扫描词条 {name} 的数值无效：{value!r}") from exc
    return {
        "property_id": property_id,
        "value": numeric_value / 100.0 if property_id in _PERCENT_PROPERTY_IDS else numeric_value,
        "percent": property_id in _PERCENT_PROPERTY_IDS,
        "names": {"zh-CN": name},
    }


def _stats(value: Any, *, core: bool = False) -> list[dict[str, Any]]:
    if core:
        return [_stat(value, 1.0)]
    if not isinstance(value, Mapping):
        raise VisionInventorySnapshotError("视觉扫描驱动缺少词条列表")
    return [_stat(label, amount) for label, amount in value.items()]


def build_vision_snapshot(items: Iterable[Mapping[str, Any]], static_dao: StaticGameDataDao) -> dict[str, Any]:
    """Convert visual parser items to the SQLite snapshot contract.

    The generated UID pair is local to this visual snapshot and is deliberately
    never eligible for nte-core's native-UID equipment RPC.
    """
    suits = {_compact_set_name(row.get("name_zh")): str(row["suit_id"]) for row in static_dao.list_suits()}
    normalized: list[dict[str, Any]] = []
    for ordinal, source in enumerate(items, start=1):
        item = dict(source)
        item_type = str(item.get("item_type") or "").strip()
        kind = "module" if item_type == "drive" else "core" if item_type == "tape" else ""
        if not kind:
            raise VisionInventorySnapshotError(f"视觉扫描第 {ordinal} 项类型无效：{item_type!r}")
        quality = _QUALITY.get(str(item.get("quality") or "").strip().casefold())
        if quality is None:
            raise VisionInventorySnapshotError(f"视觉扫描第 {ordinal} 项品质无效：{item.get('quality')!r}")
        row: dict[str, Any] = {
            "uid": {"serial": ordinal, "slot": 1},
            "kind": kind,
            "item_id": f"vision_{kind}_{ordinal}",
            "suit_id": None,
            "geometry": None,
            "grid": int(item.get("area") or (15 if kind == "core" else 0)),
            "quality": quality,
            "level": 0,
            "max_level": 0,
            # The visual scan cannot read these state fields.  Persist neutral
            # placeholders because the SQLite contract is non-nullable; the
            # `gamepad` snapshot source tells consumers that they are unknown.
            "locked": False,
            "discarded": False,
            "equipped": False,
            "equipped_character_uid": None,
            "equipped_character_id": None,
            "names": {"zh-CN": str(item.get("uid") or f"vision_{ordinal}")},
            "suit_names": {},
            "sub_stats": _stats(item.get("sub_stats")),
        }
        if kind == "module":
            shape_id = str(item.get("shape_id") or "").strip()
            geometry = _GEOMETRY.get(shape_id)
            if geometry is None:
                raise VisionInventorySnapshotError(f"视觉扫描第 {ordinal} 项形状无效：{shape_id!r}")
            row["geometry"] = geometry
            row["main_stats"] = _stats(item.get("main_stats"))
        else:
            set_name = str(item.get("set_name") or "").strip()
            suit_id = suits.get(_compact_set_name(set_name))
            if suit_id is None:
                raise VisionInventorySnapshotError(f"视觉扫描第 {ordinal} 张卡带套装无法匹配官方静态库：{set_name!r}")
            row["suit_id"] = suit_id
            row["geometry"] = "Core"
            row["grid"] = 15
            row["suit_names"] = {"zh-CN": set_name}
            row["main_stats"] = _stats(item.get("main_stats"), core=True)
        normalized.append(row)
    return {"complete": True, "item_count": len(normalized), "items": normalized}


def import_vision_inventory(
    database_path: str | Path,
    items: Iterable[Mapping[str, Any]],
) -> int:
    """Persist a completed full visual scan as the `gamepad` fallback source."""
    with UserDataDao(database_path) as user_dao, StaticGameDataDao() as static_dao:
        snapshot = build_vision_snapshot(items, static_dao)
        return user_dao.import_inventory_snapshot(snapshot, source="gamepad")
