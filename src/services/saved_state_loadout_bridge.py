# 把旧配装页保存的棋盘结果转换为官方 UID/坐标的 SQLite 配装方案。
"""把旧配装页保存的棋盘结果转换为官方 UID/坐标的 SQLite 配装方案。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from src.features.drive_assembly.blocks import extract_drive_blocks_from_state
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    virtual_equipment_inventory_item,
)
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


@dataclass(frozen=True)
class PreparedLoadoutPlan:
    """已校验、尚未写入 SQLite 的角色配装方案。"""

    name: str
    role_name: str
    character_id: int
    snapshot_id: int
    status: str
    score: float | None
    assignments: tuple[dict[str, Any], ...]
    payload: dict[str, Any]
    module_count: int

    def as_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "character_id": self.character_id,
            "source_snapshot_id": self.snapshot_id,
            "status": self.status,
            "score": self.score,
            "assignments": self.assignments,
            "payload": self.payload,
        }


def character_ids_for_saved_role(
    role_name: str,
    roles_db: Mapping[str, Any],
) -> tuple[int, ...]:
    """读取角色的全部官方 ID；主角会同时返回男性与女性 ID。"""

    role = roles_db.get(role_name)
    if not isinstance(role, Mapping):
        raise SavedStateLoadoutError(f"角色 [{role_name}] 缺少配置，无法确定官方角色 ID")
    raw_ids = role.get("workshop_item_ids")
    values = list(raw_ids) if isinstance(raw_ids, (list, tuple)) else []
    if role.get("workshop_item_id") is not None:
        values.append(role["workshop_item_id"])

    character_ids: list[int] = []
    for raw_value in values:
        try:
            character_id = int(raw_value)
        except (TypeError, ValueError):
            continue
        if character_id > 0 and character_id not in character_ids:
            character_ids.append(character_id)
    if not character_ids:
        raise SavedStateLoadoutError(f"角色 [{role_name}] 缺少有效的官方角色 ID")
    return tuple(character_ids)


def character_id_for_saved_role(
    role_name: str,
    roles_db: Mapping[str, Any],
) -> int:
    """兼容单 ID 调用方；多 ID 角色应使用当前背包自动解析。"""

    return character_ids_for_saved_role(role_name, roles_db)[0]


def resolve_character_id_for_saved_role(
    role_name: str,
    roles_db: Mapping[str, Any],
    user_dao: UserDataDao,
    *,
    snapshot_id: int | None = None,
) -> int:
    """用固定稳定快照中的角色实例 UID 选择账号实际使用的官方角色 ID。"""

    candidates = character_ids_for_saved_role(role_name, roles_db)
    if len(candidates) == 1:
        return candidates[0]
    selected_snapshot_id = (
        user_dao.current_inventory_snapshot_id()
        if snapshot_id is None
        else snapshot_id
    )
    if selected_snapshot_id is None:
        raise SavedStateLoadoutError("用户数据库中还没有稳定背包快照")
    if user_dao.inventory_snapshot_summary(selected_snapshot_id) is None:
        raise SavedStateLoadoutError(
            f"指定的稳定背包快照不存在：{selected_snapshot_id}"
        )

    candidate_set = set(candidates)

    def instances_in(candidate_snapshot_id: int) -> dict[int, set[tuple[int, int]]]:
        instance_uids: dict[int, set[tuple[int, int]]] = {}
        for item in user_dao.list_inventory_items(candidate_snapshot_id, equipped=True):
            character_id = item.get("equipped_character_id")
            character_uid = item.get("equipped_character_uid")
            if character_id not in candidate_set or not isinstance(character_uid, Mapping):
                continue
            try:
                uid = (int(character_uid["slot"]), int(character_uid["serial"]))
            except (KeyError, TypeError, ValueError):
                continue
            if uid[0] > 0 and uid[1] > 0:
                instance_uids.setdefault(character_id, set()).add(uid)
        return instance_uids

    def resolve_instances(
        instance_uids: dict[int, set[tuple[int, int]]],
    ) -> int | None:
        if not instance_uids:
            return None
        if len(instance_uids) == 1:
            character_id, uids = next(iter(instance_uids.items()))
            if len(uids) == 1:
                return character_id
            raise SavedStateLoadoutError(
                f"角色 [{role_name}] 的官方 ID {character_id} 对应多个角色实例 UID"
            )
        matched_text = "、".join(str(value) for value in sorted(instance_uids))
        raise SavedStateLoadoutError(
            f"角色 [{role_name}] 的多个候选 ID 同时存在角色实例（{matched_text}），无法自动选择"
        )

    resolved = resolve_instances(instances_in(selected_snapshot_id))
    if resolved is not None:
        return resolved

    # 批量装配可能把主角当前装备全部移走；官方角色 ID（如 1046/1051）仍可从
    # 最近一份包含该角色装备的稳定快照可靠恢复。
    for summary in user_dao.list_inventory_snapshots():
        historical_snapshot_id = int(summary["snapshot_id"])
        if historical_snapshot_id >= selected_snapshot_id:
            continue
        resolved = resolve_instances(instances_in(historical_snapshot_id))
        if resolved is not None:
            return resolved

    mapped_candidates = {
        row["character_id"]
        for candidate in candidates
        for row in user_dao.list_character_instance_mappings(candidate)
    }
    if len(mapped_candidates) == 1:
        return next(iter(mapped_candidates))
    if len(mapped_candidates) > 1:
        matched_text = "、".join(str(value) for value in sorted(mapped_candidates))
        raise SavedStateLoadoutError(
            f"角色 [{role_name}] 的候选 ID 已保存多个角色实例（{matched_text}），请手动选择"
        )

    candidate_text = "、".join(str(value) for value in candidates)
    raise SavedStateLoadoutError(
        f"角色 [{role_name}] 有多个候选官方 ID（{candidate_text}），"
        "但当前、历史稳定背包和已保存映射都没有可用于判断的角色实例"
    )


def character_ids_for_static_role(
    role_name: str,
    static_dao: StaticGameDataDao,
) -> tuple[int, ...]:
    """仅用静态库的官方角色资料解析 UI 角色名。

    此入口供新计算链路使用，不读取 ``my_roles_model.json`` 或其他旧配置。
    """

    raw_name = str(role_name).strip()
    if not raw_name:
        raise SavedStateLoadoutError("角色名称不能为空")
    characters = static_dao.list_characters()
    if raw_name == "主角":
        candidates = [
            row for row in characters
            if row.get("classification") == "available_avatar_variant"
        ]
    else:
        candidates = [
            row for row in characters
            if row.get("name_zh") == raw_name
            and row.get("classification") != "combat_transformation"
        ]
    ids = tuple(sorted({int(row["character_id"]) for row in candidates}))
    if not ids:
        raise SavedStateLoadoutError(f"静态数据库中没有角色 [{raw_name}] 的官方 ID")
    return ids


def resolve_character_id_for_static_role(
    role_name: str,
    static_dao: StaticGameDataDao,
    user_dao: UserDataDao,
    *,
    snapshot_id: int,
) -> int:
    """从官方静态角色与固定快照确定角色 ID，主角保留历史和映射兜底。"""

    candidates = character_ids_for_static_role(role_name, static_dao)
    if len(candidates) == 1:
        return candidates[0]

    candidate_set = set(candidates)

    def candidates_in(candidate_snapshot_id: int) -> set[int]:
        return {
            int(item["equipped_character_id"])
            for item in user_dao.list_inventory_items(candidate_snapshot_id, equipped=True)
            if item.get("equipped_character_id") in candidate_set
            and isinstance(item.get("equipped_character_uid"), Mapping)
        }

    found = candidates_in(snapshot_id)
    if len(found) == 1:
        return next(iter(found))
    if len(found) > 1:
        raise SavedStateLoadoutError(
            f"角色 [{role_name}] 的多个官方 ID 同时存在于固定快照，请手动选择"
        )
    for summary in user_dao.list_inventory_snapshots():
        historical_id = int(summary["snapshot_id"])
        if historical_id >= snapshot_id:
            continue
        found = candidates_in(historical_id)
        if len(found) == 1:
            return next(iter(found))
    mapped = {
        candidate
        for candidate in candidates
        if user_dao.list_character_instance_mappings(candidate)
    }
    if len(mapped) == 1:
        return next(iter(mapped))
    candidate_text = "、".join(str(value) for value in candidates)
    raise SavedStateLoadoutError(
        f"角色 [{role_name}] 有多个候选官方 ID（{candidate_text}），"
        "请先在一键装配中手动选择角色实例并保存映射"
    )


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
        snapshot_id: int | None = None,
        name: str | None = None,
        score: float | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> SavedLoadoutPlan:
        prepared = self.prepare_role_plan(
            role_name=role_name,
            role_state=role_state,
            character_id=character_id,
            snapshot_id=snapshot_id,
            name=name,
            score=score,
            payload=payload,
        )
        plan_id = self.user_dao.save_loadout_plan(
            **prepared.as_record(),
            is_active=True,
        )
        return SavedLoadoutPlan(
            plan_id=plan_id,
            role_name=prepared.role_name,
            character_id=prepared.character_id,
            snapshot_id=prepared.snapshot_id,
            module_count=prepared.module_count,
        )

    def prepare_role_plan(
        self,
        *,
        role_name: str,
        role_state: Mapping[str, Any],
        character_id: int,
        snapshot_id: int | None = None,
        name: str | None = None,
        score: float | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> PreparedLoadoutPlan:
        """校验并转换角色方案，但不启动写事务。"""

        selected_snapshot_id = (
            self.user_dao.current_inventory_snapshot_id()
            if snapshot_id is None
            else snapshot_id
        )
        if selected_snapshot_id is None:
            raise SavedStateLoadoutError("用户数据库中还没有稳定背包快照")
        if self.user_dao.inventory_snapshot_summary(selected_snapshot_id) is None:
            raise SavedStateLoadoutError(
                f"指定的稳定背包快照不存在：{selected_snapshot_id}"
            )

        character = self.static_dao.get_character(character_id)
        if character is None:
            raise SavedStateLoadoutError(
                f"静态数据库中不存在角色 ID {character_id}（{role_name}）"
            )

        inventory = self.user_dao.list_inventory_items(selected_snapshot_id)
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
            virtual = bool(drive.get("virtual"))
            item = (
                virtual_equipment_inventory_item(
                    {
                        **dict(drive),
                        "uid_slot": slot,
                        "uid_serial": serial,
                        "kind": "module",
                    }
                )
                if virtual
                else items_by_uid.get((slot, serial))
            )
            if item is None or item.get("kind") != "module":
                raise SavedStateLoadoutError(
                    f"角色 [{role_name}] 的驱动 UID ({slot}, {serial}) 不在所选稳定背包中"
                )
            official_shape_id = _shape_id(item.get("geometry"))
            shape = shapes.get(official_shape_id)
            if shape is None:
                raise SavedStateLoadoutError(f"静态数据库缺少形状 {official_shape_id}")
            row, column = _official_anchor(block.get("cells"), shape.get("cells") or [])
            assignment = {
                "uid_serial": serial,
                "uid_slot": slot,
                "kind": "module",
                "target_row": row,
                "target_column": column,
                "rotation": 0,
                "geometry": item.get("geometry"),
                "grid_count": item.get("grid_count"),
            }
            if virtual:
                assignment.update({
                    "virtual": True,
                    "virtual_equipment": dict(
                        drive.get("virtual_equipment") or {}
                    ),
                })
            assignments.append(assignment)

        tape = role_state.get("equipped_tape") or role_state.get("tape")
        if isinstance(tape, Mapping):
            core_slot, core_serial = _saved_uid(tape.get("uid"), expected_kind="core")
            virtual = bool(tape.get("virtual"))
            core_item = (
                virtual_equipment_inventory_item(
                    {
                        **dict(tape),
                        "uid_slot": core_slot,
                        "uid_serial": core_serial,
                        "kind": "core",
                    }
                )
                if virtual
                else items_by_uid.get((core_slot, core_serial))
            )
            if core_item is None or core_item.get("kind") != "core":
                raise SavedStateLoadoutError(
                    f"角色 [{role_name}] 的核心 UID ({core_slot}, {core_serial}) 不在所选稳定背包中"
                )
            assignment = {
                "uid_serial": core_serial,
                "uid_slot": core_slot,
                "kind": "core",
                "target_row": None,
                "target_column": None,
                "rotation": 0,
            }
            if virtual:
                assignment.update({
                    "virtual": True,
                    "virtual_equipment": dict(
                        tape.get("virtual_equipment") or {}
                    ),
                })
            assignments.append(assignment)

        module_count = sum(item["kind"] == "module" for item in assignments)
        if module_count <= 0:
            raise SavedStateLoadoutError(f"角色 [{role_name}] 没有可装配的驱动")
        return PreparedLoadoutPlan(
            name=name or f"配装页：{role_name}",
            role_name=role_name,
            character_id=character_id,
            snapshot_id=selected_snapshot_id,
            status=(
                "incomplete"
                if any(
                    is_virtual_equipment_assignment(item)
                    for item in assignments
                )
                else "ready"
            ),
            assignments=tuple(assignments),
            payload=dict(payload or {
                "schema": "saved-state-official-loadout-v1",
                "source": "equipment_page",
                "source_role_name": role_name,
            }),
            score=score,
            module_count=module_count,
        )
