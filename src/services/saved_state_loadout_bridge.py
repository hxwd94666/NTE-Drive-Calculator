# 把旧配装页保存的棋盘结果转换为官方 UID/坐标的 SQLite 配装方案。
"""把旧配装页保存的棋盘结果转换为官方 UID/坐标的 SQLite 配装方案。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from src.features.drive_assembly.blocks import extract_drive_blocks_from_state
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


_SAVED_UID_PATTERN = re.compile(
    r"^nte-(?P<kind>module|core)-(?P<slot>\d+)-(?P<serial>\d+)$"
)


class SavedStateLoadoutError(RuntimeError):
    """旧配装结果无法无损转换成官方配装参数。"""


@dataclass(frozen=True)
class SavedLoadoutPlan:
    """刚写入用户 SQLite 的可执行配装方案。"""

    plan_id: int
    role_name: str
    character_id: int
    snapshot_id: int
    module_count: int


def character_id_for_saved_role(
    role_name: str,
    roles_db: Mapping[str, Any],
) -> int:
    """在旧 UI 边界读取角色 ID；进入服务层后只使用官方 character_id。"""

    role = roles_db.get(role_name)
    if not isinstance(role, Mapping):
        raise SavedStateLoadoutError(f"角色 [{role_name}] 缺少配置，无法确定官方角色 ID")
    raw_character_id = role.get("workshop_item_id")
    try:
        character_id = int(raw_character_id)
    except (TypeError, ValueError) as exc:
        raise SavedStateLoadoutError(
            f"角色 [{role_name}] 缺少有效的官方角色 ID"
        ) from exc
    if character_id <= 0:
        raise SavedStateLoadoutError(f"角色 [{role_name}] 的官方角色 ID 无效")
    return character_id


def _saved_uid(value: Any, *, expected_kind: str) -> tuple[int, int]:
    match = _SAVED_UID_PATTERN.fullmatch(str(value or "").strip())
    if match is None or match.group("kind") != expected_kind:
        raise SavedStateLoadoutError(f"无效的 {expected_kind} UID：{value!r}")
    return int(match.group("slot")), int(match.group("serial"))


def _shape_id(geometry: Any) -> str:
    name = str(geometry or "").strip()
    if not name:
        raise SavedStateLoadoutError("背包驱动缺少官方 geometry")
    return name if name.startswith("EquipmentGeometry_") else f"EquipmentGeometry_{name}"


def _official_anchor(
    occupied_cells: Any,
    shape_cells: list[Mapping[str, Any]],
) -> tuple[int, int]:
    """由棋盘占用格和官方相对坐标反推出插件所需的 1-based 锚点。"""

    try:
        occupied = {(int(row), int(column)) for row, column in occupied_cells}
        offsets = {(int(cell["x"]), int(cell["y"])) for cell in shape_cells}
    except (KeyError, TypeError, ValueError) as exc:
        raise SavedStateLoadoutError("配装棋盘或官方形状坐标无效") from exc
    if not occupied or not offsets or len(occupied) != len(offsets):
        raise SavedStateLoadoutError("配装棋盘占用格与官方形状面积不一致")

    matches: list[tuple[int, int]] = []
    for anchor_row in range(1, 6):
        for anchor_column in range(1, 6):
            projected = {
                (anchor_row + delta_x, anchor_column + delta_y)
                for delta_x, delta_y in offsets
            }
            if projected == occupied:
                matches.append((anchor_row, anchor_column))
    if len(matches) != 1:
        raise SavedStateLoadoutError(
            f"无法唯一确定官方配装锚点：occupied={sorted(occupied)}"
        )
    return matches[0]


class SavedStateLoadoutBridge:
    """将一个角色的已保存配装转换并保存为 SQLite loadout_plan。"""

    def __init__(
        self,
        user_dao: UserDataDao,
        static_dao: StaticGameDataDao,
    ) -> None:
        self.user_dao = user_dao
        self.static_dao = static_dao

    def save_role_plan(
        self,
        *,
        role_name: str,
        role_state: Mapping[str, Any],
        character_id: int,
    ) -> SavedLoadoutPlan:
        snapshot_id = self.user_dao.current_inventory_snapshot_id()
        if snapshot_id is None:
            raise SavedStateLoadoutError("用户数据库中还没有稳定背包快照")

        character = self.static_dao.get_character(character_id)
        if character is None:
            raise SavedStateLoadoutError(
                f"静态数据库中不存在角色 ID {character_id}（{role_name}）"
            )

        inventory = self.user_dao.list_inventory_items(snapshot_id)
        items_by_uid = {
            (item["uid_slot"], item["uid_serial"]): item for item in inventory
        }
        shapes = {shape["shape_id"]: shape for shape in self.static_dao.list_shapes()}

        assignments: list[dict[str, Any]] = []
        blocks = extract_drive_blocks_from_state({role_name: dict(role_state)})
        for block in blocks:
            drive = block.get("drive")
            if not isinstance(drive, Mapping):
                raise SavedStateLoadoutError(
                    f"角色 [{role_name}] 的棋盘块 {block.get('block_id')} 没有对应驱动"
                )
            slot, serial = _saved_uid(drive.get("uid"), expected_kind="module")
            item = items_by_uid.get((slot, serial))
            if item is None or item.get("kind") != "module":
                raise SavedStateLoadoutError(
                    f"角色 [{role_name}] 的驱动 UID ({slot}, {serial}) 不在当前稳定背包中"
                )
            official_shape_id = _shape_id(item.get("geometry"))
            shape = shapes.get(official_shape_id)
            if shape is None:
                raise SavedStateLoadoutError(f"静态数据库缺少形状 {official_shape_id}")
            row, column = _official_anchor(block.get("cells"), shape.get("cells") or [])
            assignments.append(
                {
                    "uid_serial": serial,
                    "uid_slot": slot,
                    "kind": "module",
                    "target_row": row,
                    "target_column": column,
                    "rotation": 0,
                    "geometry": item.get("geometry"),
                }
            )

        tape = role_state.get("equipped_tape") or role_state.get("tape")
        if not isinstance(tape, Mapping):
            raise SavedStateLoadoutError(f"角色 [{role_name}] 没有已保存的空幕核心")
        core_slot, core_serial = _saved_uid(tape.get("uid"), expected_kind="core")
        core_item = items_by_uid.get((core_slot, core_serial))
        if core_item is None or core_item.get("kind") != "core":
            raise SavedStateLoadoutError(
                f"角色 [{role_name}] 的核心 UID ({core_slot}, {core_serial}) 不在当前稳定背包中"
            )
        assignments.append(
            {
                "uid_serial": core_serial,
                "uid_slot": core_slot,
                "kind": "core",
                "target_row": None,
                "target_column": None,
                "rotation": 0,
            }
        )

        module_count = len(assignments) - 1
        if module_count <= 0:
            raise SavedStateLoadoutError(f"角色 [{role_name}] 没有可装配的驱动")
        plan_id = self.user_dao.save_loadout_plan(
            name=f"配装页：{role_name}",
            character_id=character_id,
            source_snapshot_id=snapshot_id,
            status="ready",
            assignments=assignments,
            payload={
                "schema": "saved-state-official-loadout-v1",
                "source": "equipment_page",
                "source_role_name": role_name,
            },
            is_active=True,
        )
        return SavedLoadoutPlan(
            plan_id=plan_id,
            role_name=role_name,
            character_id=character_id,
            snapshot_id=snapshot_id,
            module_count=module_count,
        )
