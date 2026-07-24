# 将官方 SQLite 背包快照投影为现有求解器的运行时输入，不生成中间 JSON。
"""SQLite 背包到当前配装求解器装备契约的内存适配层。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


class AllocationInventoryProjectionError(RuntimeError):
    """稳定背包包含当前求解器尚不能识别的数据。"""


_QUALITY_NAMES = {
    "orange": "Gold",
    "purple": "Purple",
    "blue": "Blue",
}

_SHAPE_IDS = {
    "hen2": "H_2",
    "hen3": "H_3",
    "hen4": "H_4",
    # 旧 OCR/模板曾把 Hen 误转写为 Heng；统一投影为既有 H_* 名称。
    "heng2": "H_2",
    "heng3": "H_3",
    "heng4": "H_4",
    "shu2": "V_2",
    "shu3": "V_3",
    "shu4": "V_4",
    "z3": "Trap_4_H",
    "z4": "Trap_4_V",
    "zhijiao1": "L_3_BL",
    "zhijiao2": "L_3_TL",
    "zhijiao3": "L_3_TR",
    "zhijiao4": "L_3_BR",
}

_STAT_NAMES = {
    "AtkAdd": "攻击力",
    "AtkUp": "攻击力%",
    "CritBase": "暴击率%",
    "CritDamageBase": "暴击伤害%",
    "DamageUpChaosBase": "暗属性异能伤害增强%",
    "DamageUpCosmosBase": "光属性异能伤害增强%",
    "DamageUpGeneralBase": "伤害增加%",
    "DamageUpGeneralAdd": "伤害增加%",
    "DamageUpIncantationBase": "咒属性异能伤害增强%",
    "DamageUpLakshanaBase": "相属性异能伤害增强%",
    "DamageUpNatureBase": "灵属性异能伤害增强%",
    "DamageUpPsycheBase": "魂属性异能伤害增强%",
    "DamageUpPsychicallyBase": "心灵伤害增强%",
    "DefAdd": "防御力",
    "DefUp": "防御力%",
    "HealUp": "治疗加成",
    "HPMaxAdd": "生命值",
    "HPMaxUp": "生命值%",
    "MagBase": "环合强度",
    "UnbalIntensityBase": "倾陷强度",
}


@dataclass(frozen=True)
class AllocationInventoryProjection:
    """一次固定稳定快照生成的求解器输入。"""

    snapshot_id: int
    items: tuple[dict[str, Any], ...]
    discarded_count: int


def _display_suit_name(value: Any) -> str:
    name = str(value or "").strip()
    if name.startswith("「") and name.endswith("」"):
        return name[1:-1]
    return name


def legacy_stat_value(value: Any, percent: bool) -> float:
    """Project an official stat into the unit used by the legacy scorer."""

    value = float(value or 0.0)
    if percent:
        value *= 100.0
    rounded = round(value, 6)
    return 0.0 if rounded == -0.0 else rounded


def _stat_value(stat: Mapping[str, Any]) -> float:
    return legacy_stat_value(stat.get("value", 0.0), bool(stat.get("percent")))


def legacy_shape_id(value: Any) -> str:
    """Map an official geometry ID to the existing puzzle-solver shape ID."""

    geometry = str(value or "").strip().removeprefix("EquipmentGeometry_")
    if geometry in _SHAPE_IDS.values():
        return geometry
    shape_id = _SHAPE_IDS.get(geometry.casefold())
    if shape_id is None:
        raise AllocationInventoryProjectionError(f"未知官方驱动形状：{value!r}")
    return shape_id


def legacy_stat_name(property_id: Any) -> str | None:
    """Return the exact scoring label used by the established allocator."""

    return _STAT_NAMES.get(str(property_id or "").strip())


def _stats(stats: list[dict[str, Any]]) -> dict[str, float]:
    result: dict[str, float] = {}
    for stat in stats:
        property_id = str(stat.get("property_id") or "").strip()
        name = _STAT_NAMES.get(property_id)
        if name is None:
            raise AllocationInventoryProjectionError(
                f"求解器尚不支持官方属性 ID {property_id or '<empty>'}"
            )
        result[name] = _stat_value(stat)
    return result


class SqliteAllocationInventory:
    """从官方 ID 快照构造配装求解器的临时输入。"""

    def __init__(self, user_dao: UserDataDao, static_dao: StaticGameDataDao) -> None:
        self.user_dao = user_dao
        self.static_dao = static_dao

    def _suit_names(self) -> dict[str, str]:
        return {
            str(suit["suit_id"]): _display_suit_name(suit.get("name_zh"))
            for suit in self.static_dao.list_suits()
        }

    @staticmethod
    def _quality(value: Any) -> str:
        quality = _QUALITY_NAMES.get(str(value or "").strip().casefold())
        if quality is None:
            raise AllocationInventoryProjectionError(f"未知装备品质：{value!r}")
        return quality

    @staticmethod
    def _shape_id(value: Any) -> str:
        return legacy_shape_id(value)

    def build(self, snapshot_id: int | None = None) -> AllocationInventoryProjection:
        """固定一个快照并投影全部物品，返回纯内存求解输入。

        弃置状态是游戏内标记，不是“不可装配”状态；因此保留在候选集中并透传给结果 UI。
        """

        if snapshot_id is None:
            raise AllocationInventoryProjectionError(
                "配装库存投影必须显式指定稳定背包快照"
            )
        pinned_snapshot_id = int(snapshot_id)
        if pinned_snapshot_id is None:
            raise AllocationInventoryProjectionError(
                "尚无稳定背包快照，请先在首页启动背包同步并进入游戏"
            )
        try:
            _summary, snapshot_items = self.user_dao.export_inventory_snapshot(
                pinned_snapshot_id
            )
        except Exception as exc:
            raise AllocationInventoryProjectionError(
                f"稳定背包快照 {pinned_snapshot_id} 不可用"
            ) from exc

        suit_names = self._suit_names()
        projected: list[dict[str, Any]] = []
        discarded_count = 0
        for item in snapshot_items:
            if item.get("discarded"):
                discarded_count += 1
            kind = item.get("kind")
            uid_prefix = "module" if kind == "module" else "core"
            base = {
                "uid": f"nte-{uid_prefix}-{item['uid_slot']}-{item['uid_serial']}",
                "item_type": "drive" if kind == "module" else "tape",
                "quality": self._quality(item.get("quality")),
                "area": int(item.get("grid_count") or 15),
                "sub_stats": _stats(item.get("sub_stats") or []),
                "discarded": bool(item.get("discarded")),
                "is_duplicate_drive": bool(item.get("is_duplicate_drive", False)),
                "duplicate_group_id": item.get("duplicate_group_id"),
                "duplicate_index": item.get("duplicate_index"),
                "duplicate_count": item.get("duplicate_count"),
                # 旧求解器字段之外，同时保留可供第三方工具消费的官方快照字段。
                "official": {
                    "snapshot_id": pinned_snapshot_id,
                    "uid": {"slot": int(item["uid_slot"]), "serial": int(item["uid_serial"])},
                    "kind": str(item.get("kind") or ""),
                    "item_id": str(item.get("item_id") or ""),
                    "suit_id": item.get("suit_id"),
                    "geometry": item.get("geometry"),
                    "quality": item.get("quality"),
                    "level": int(item.get("level") or 0),
                    "max_level": int(item.get("max_level") or 0),
                    "locked": bool(item.get("locked")),
                    "discarded": bool(item.get("discarded")),
                    "equipped": bool(item.get("equipped")),
                    "equipped_character_id": item.get("equipped_character_id"),
                    "equipped_placement": item.get("equipped_placement"),
                    "is_duplicate_drive": bool(item.get("is_duplicate_drive", False)),
                    "duplicate_group_id": item.get("duplicate_group_id"),
                    "main_stats": [dict(stat) for stat in item.get("main_stats") or []],
                    "sub_stats": [dict(stat) for stat in item.get("sub_stats") or []],
                },
            }
            if kind == "module":
                base.update(
                    {
                        "shape_id": self._shape_id(item.get("geometry")),
                        # 驱动本身没有套装归属；套装由核心和规定形状共同激活。
                        "set_name": "未知套装",
                        "main_stats": _stats(item.get("main_stats") or []),
                    }
                )
            elif kind == "core":
                suit_id = str(item.get("suit_id") or "")
                set_name = suit_names.get(suit_id)
                if not set_name:
                    raise AllocationInventoryProjectionError(
                        f"静态数据库缺少核心套装 {suit_id or '<empty>'}"
                    )
                main_stats = _stats(item.get("main_stats") or [])
                if len(main_stats) != 1:
                    raise AllocationInventoryProjectionError(
                        f"核心 {base['uid']} 必须且仅包含一个主词条"
                    )
                base.update(
                    {
                        "area": 15,
                        "shape_id": "TAPE_15",
                        "set_name": set_name,
                        "main_stats": next(iter(main_stats)),
                    }
                )
            else:
                raise AllocationInventoryProjectionError(f"未知装备类型：{kind!r}")
            projected.append(base)

        return AllocationInventoryProjection(
            snapshot_id=pinned_snapshot_id,
            items=tuple(projected),
            discarded_count=discarded_count,
        )


def load_inventory_projection(
    database_path: str | None,
    snapshot_id: int,
) -> list[dict[str, Any]]:
    """投影指定快照，供结果差异等历史方案展示使用。"""

    if database_path is None:
        from src.app import runtime
        database_path = str(runtime.USER_DATABASE_PATH)
    with UserDataDao(database_path) as user_dao, StaticGameDataDao() as static_dao:
        return [
            dict(item)
            for item in SqliteAllocationInventory(user_dao, static_dao).build(snapshot_id).items
        ]


def load_current_inventory_projection(database_path: str | None = None) -> list[dict[str, Any]]:
    """兼容入口：明确读取调用时的当前快照，不用于已保存方案。"""

    if database_path is None:
        from src.app import runtime
        database_path = str(runtime.USER_DATABASE_PATH)
    with UserDataDao(database_path) as user_dao:
        snapshot_id = user_dao.current_inventory_snapshot_id()
    if snapshot_id is None:
        raise AllocationInventoryProjectionError("尚无稳定背包快照")
    return load_inventory_projection(database_path, snapshot_id)
