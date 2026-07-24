# 使用游戏官方 ID 和 SQLite 快照生成可复现的基础装配方案。
"""基于静态数据库蓝图和用户背包快照的官方 ID 配装入口。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


class LoadoutOptimizationError(RuntimeError):
    """当前官方蓝图或背包无法组成一套完整方案。"""


@dataclass(frozen=True)
class LoadoutOptimizationResult:
    """一次固定快照计算的结果。"""

    character_id: int
    source_snapshot_id: int
    plan_id: int | None
    score: float
    assignments: tuple[dict[str, Any], ...]
    property_weights: dict[str, float]


def _geometry_key(value: Any) -> str:
    geometry = str(value or "").strip()
    prefix = "EquipmentGeometry_"
    if geometry.startswith(prefix):
        geometry = geometry[len(prefix) :]
    return geometry.casefold()


def _uid_sort_key(item: Mapping[str, Any]) -> tuple[int, int]:
    return int(item["uid_slot"]), int(item["uid_serial"])


class SqliteLoadoutOptimizer:
    """按官方推荐蓝图完成一套可直接装配的基线方案。

    当前版本负责“固定布局 + 激活核心套装 + 官方属性 ID 加权”这一稳定边界；
    自定义布局、边际收益和战斗模型可以在同一输入结构上继续扩展。
    """

    def __init__(self, static_dao: StaticGameDataDao, user_dao: UserDataDao) -> None:
        self.static_dao = static_dao
        self.user_dao = user_dao

    @staticmethod
    def _score_item(item: Mapping[str, Any], weights: Mapping[str, float]) -> float:
        score = 0.0
        for stat in (*item.get("main_stats", ()), *item.get("sub_stats", ())):
            weight = weights.get(str(stat.get("property_id")), 0.0)
            value = float(stat.get("value", 0.0))
            if stat.get("percent"):
                value *= 100.0
            score += weight * value
        return score

    @staticmethod
    def _weights(
        plan: Mapping[str, Any], property_weights: Mapping[str, float] | None
    ) -> dict[str, float]:
        source = (
            property_weights
            if property_weights is not None
            else {property_id: 1.0 for property_id in plan["recommended_attribute_ids"]}
        )
        result: dict[str, float] = {}
        for raw_property_id, raw_weight in source.items():
            property_id = str(raw_property_id).strip()
            if not property_id:
                raise ValueError("property_id 不能为空")
            weight = float(raw_weight)
            if weight < 0:
                raise ValueError("属性权重不能小于 0")
            result[property_id] = weight
        if not result:
            raise ValueError("至少需要一个官方属性 ID 权重")
        return result

    def optimize(
        self,
        character_id: int,
        *,
        snapshot_id: int | None = None,
        property_weights: Mapping[str, float] | None = None,
        plan_name: str | None = None,
        save: bool = True,
    ) -> LoadoutOptimizationResult:
        """固定一次稳定背包快照，并按官方推荐蓝图选择最高分装备。"""

        character = self.static_dao.get_character(character_id)
        if character is None:
            raise LoadoutOptimizationError(f"静态数据库没有角色 {character_id}")
        blueprint = self.static_dao.get_equipment_plan(character_id)
        if blueprint is None:
            raise LoadoutOptimizationError(f"角色 {character_id} 没有官方装备蓝图")
        if snapshot_id is None:
            raise LoadoutOptimizationError(
                "保存官方配装方案必须显式指定计算使用的稳定背包快照"
            )
        pinned_snapshot_id = int(snapshot_id)
        if pinned_snapshot_id is None:
            raise LoadoutOptimizationError("尚无稳定背包快照")
        if self.user_dao.inventory_snapshot_summary(pinned_snapshot_id) is None:
            raise LoadoutOptimizationError(f"背包快照 {pinned_snapshot_id} 不存在")

        weights = self._weights(blueprint, property_weights)
        inventory = self.user_dao.list_inventory_items(pinned_snapshot_id)
        # 弃置仅是游戏内状态标记，不能让用户的可装配候选凭空消失。
        available = list(inventory)

        core_candidates = [
            item
            for item in available
            if item["kind"] == "core" and item["item_id"] == blueprint["core_item_id"]
        ]
        if not core_candidates:
            raise LoadoutOptimizationError(
                f"背包中没有官方推荐核心 {blueprint['core_item_id']}"
            )
        core = max(
            core_candidates,
            key=lambda item: (
                self._score_item(item, weights),
                int(item["level"]),
                tuple(-value for value in _uid_sort_key(item)),
            ),
        )
        core_template = self.static_dao.get_equipment_item(blueprint["core_item_id"])
        if core_template is None or not core_template.get("suit_id"):
            raise LoadoutOptimizationError("官方推荐核心缺少套装 ID")
        target_suit_id = str(core_template["suit_id"])
        suit = self.static_dao.get_suit(target_suit_id)
        if suit is None:
            raise LoadoutOptimizationError(f"静态数据库没有套装 {target_suit_id}")
        required_geometries = {
            _geometry_key(shape_id) for shape_id in suit["required_shape_ids"]
        }

        slots: list[dict[str, Any]] = []
        for cell in blueprint["cells"]:
            template_id = cell.get("anchor_item_id")
            if not template_id:
                continue
            template = self.static_dao.get_equipment_item(template_id)
            if template is None or template["kind"] != "module":
                raise LoadoutOptimizationError(f"官方蓝图装备模板无效：{template_id}")
            geometry_key = _geometry_key(template["geometry_id"])
            slots.append(
                {
                    "row": int(cell["row"]),
                    "column": int(cell["column"]),
                    "template_id": template_id,
                    "geometry_key": geometry_key,
                    "requires_suit": geometry_key in required_geometries,
                }
            )
        slots.sort(
            key=lambda slot: (
                not slot["requires_suit"],
                slot["row"],
                slot["column"],
            )
        )

        used_uids: set[tuple[int, int]] = set()
        module_assignments: list[dict[str, Any]] = []
        for slot in slots:
            candidates = [
                item
                for item in available
                if item["kind"] == "module"
                and _geometry_key(item.get("geometry")) == slot["geometry_key"]
                and (item["uid_serial"], item["uid_slot"]) not in used_uids
                and (
                    not slot["requires_suit"]
                    or item.get("suit_id") == target_suit_id
                )
            ]
            if not candidates:
                suit_hint = f"且 suit_id={target_suit_id}" if slot["requires_suit"] else ""
                raise LoadoutOptimizationError(
                    f"位置 ({slot['row']}, {slot['column']}) 缺少形状 "
                    f"{slot['geometry_key']}{suit_hint} 的驱动"
                )
            selected = max(
                candidates,
                key=lambda item: (
                    self._score_item(item, weights),
                    int(item["level"]),
                    tuple(-value for value in _uid_sort_key(item)),
                ),
            )
            used_uids.add((selected["uid_serial"], selected["uid_slot"]))
            module_assignments.append(
                {
                    "uid_serial": selected["uid_serial"],
                    "uid_slot": selected["uid_slot"],
                    "kind": "module",
                    "item_id": selected["item_id"],
                    "suit_id": selected.get("suit_id"),
                    "geometry": selected.get("geometry"),
                    "official_template_id": slot["template_id"],
                    "target_row": slot["row"],
                    "target_column": slot["column"],
                    "rotation": 0,
                    "score": self._score_item(selected, weights),
                    "discarded": bool(selected.get("discarded")),
                }
            )

        core_assignment = {
            "uid_serial": core["uid_serial"],
            "uid_slot": core["uid_slot"],
            "kind": "core",
            "item_id": core["item_id"],
            "suit_id": core.get("suit_id"),
            "target_row": None,
            "target_column": None,
            "rotation": 0,
            "score": self._score_item(core, weights),
            "discarded": bool(core.get("discarded")),
        }
        assignments = (*module_assignments, core_assignment)
        total_score = sum(float(item["score"]) for item in assignments)
        payload = {
            "schema": "official-id-v1",
            "optimizer": "sqlite_official_blueprint",
            "character_id": character_id,
            "core_item_id": blueprint["core_item_id"],
            "target_suit_id": target_suit_id,
            "recommended_attribute_ids": blueprint["recommended_attribute_ids"],
            "property_weights": weights,
        }
        saved_plan_id = None
        if save:
            saved_plan_id = self.user_dao.save_loadout_plan(
                name=plan_name or f"{character_id} 官方蓝图方案",
                character_id=character_id,
                source_snapshot_id=pinned_snapshot_id,
                status="ready",
                score=total_score,
                payload=payload,
                assignments=assignments,
            )
        return LoadoutOptimizationResult(
            character_id=character_id,
            source_snapshot_id=pinned_snapshot_id,
            plan_id=saved_plan_id,
            score=total_score,
            assignments=tuple(dict(item) for item in assignments),
            property_weights=weights,
        )
