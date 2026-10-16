# 把旧配装页保存的棋盘结果转换为官方 UID/坐标的 SQLite 配装方案。
"""把旧配装页保存的棋盘结果转换为官方 UID/坐标的 SQLite 配装方案。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from src.domain.drive_layout import extract_drive_blocks_from_state
from src.domain.equipment.native_layout import validate_native_layout
from src.domain.equipment.loadout_plan_scores import exact_assignment_score_total
from src.domain.equipment.virtual_equipment import (
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


def custom_character_id_for_role(
    role_name: str,
    user_dao: UserDataDao,
) -> int | None:
    """Resolve an account-owned custom role before querying release data."""

    raw_name = str(role_name).strip()
    if not raw_name:
        raise SavedStateLoadoutError("角色名称不能为空")
    matches = [
        row
        for row in user_dao.list_custom_characters()
        if str(row.get("name_zh") or "").strip() == raw_name
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise SavedStateLoadoutError(f"自建角色 [{raw_name}] 存在重复记录")
    return int(matches[0]["character_id"])


def resolve_character_id_for_allocation_role(
    role_name: str,
    static_dao: StaticGameDataDao,
    user_dao: UserDataDao,
    *,
    snapshot_id: int,
) -> int:
    """Resolve a calculated role to its account custom or official ID."""

    custom_character_id = custom_character_id_for_role(role_name, user_dao)
    if custom_character_id is not None:
        return custom_character_id
    return resolve_character_id_for_static_role(
        role_name,
        static_dao,
        user_dao,
        snapshot_id=snapshot_id,
    )


def _female_avatar_candidate(
    candidates: tuple[int, ...],
    static_dao: StaticGameDataDao,
) -> int | None:
    """Choose a female avatar template before any historical instance lookup."""

    female_ids = []
    for character_id in candidates:
        character = static_dao.get_character(character_id) or {}
        actor_path = str(character.get("actor_path") or "").casefold()
        if "female" in actor_path or "_female" in actor_path:
            female_ids.append(character_id)
    return min(female_ids) if female_ids else None


def resolve_character_id_for_static_role(
    role_name: str,
    static_dao: StaticGameDataDao,
    user_dao: UserDataDao,
    *,
    snapshot_id: int,
) -> int:
    """从官方静态角色与固定快照确定角色 ID，主角保留历史和映射兜底。"""

    candidates = character_ids_for_static_role(role_name, static_dao)
    female_candidate = _female_avatar_candidate(candidates, static_dao)
    if female_candidate is not None:
        return female_candidate
    if len(candidates) == 1:
        return candidates[0]

    candidate_set = set(candidates)

    def candidates_in(candidate_snapshot_id: int) -> set[int]:
        return {
            candidate
            for candidate in candidate_set
            if any(
                row.get("source") == "snapshot"
                and row.get("last_seen_snapshot_id") == candidate_snapshot_id
                for row in user_dao.list_character_instance_mappings(candidate)
            )
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
        *, frozen_snapshot_id: int | None = None,
    ) -> None:
        self.user_dao = user_dao
        self.static_dao = static_dao
        self._frozen_snapshot_id = frozen_snapshot_id
        self._frozen_items = (
            {(item["uid_slot"], item["uid_serial"]): item
             for item in user_dao.list_inventory_items(frozen_snapshot_id)}
            if frozen_snapshot_id is not None else None
        )
        self._frozen_shapes = (
            {shape["shape_id"]: shape for shape in static_dao.list_shapes()}
            if frozen_snapshot_id is not None else None
        )

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
        slot_id: int | None = None,
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
        slot = self.user_dao.get_loadout_slot(slot_id) if slot_id is not None else None
        plan_id = self.user_dao.save_loadout_plan(
            **prepared.as_record(),
            is_active=(slot_id is None or (slot or {}).get("slot_key") == "primary"),
            slot_id=slot_id,
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

        if snapshot_id is None:
            raise SavedStateLoadoutError(
                "保存配装方案必须显式指定计算使用的稳定背包快照"
            )
        selected_snapshot_id = int(snapshot_id)
        if selected_snapshot_id is None:
            raise SavedStateLoadoutError("用户数据库中还没有稳定背包快照")
        if self.user_dao.inventory_snapshot_summary(selected_snapshot_id) is None:
            raise SavedStateLoadoutError(
                f"指定的稳定背包快照不存在：{selected_snapshot_id}"
            )

        character = self.static_dao.get_character(character_id)
        custom_character_id = custom_character_id_for_role(role_name, self.user_dao)
        is_matching_custom_role = custom_character_id == int(character_id)
        if character is None and not is_matching_custom_role:
            raise SavedStateLoadoutError(
                f"静态数据库中不存在角色 ID {character_id}（{role_name}）"
            )

        if self._frozen_snapshot_id is not None:
            if selected_snapshot_id != self._frozen_snapshot_id:
                raise SavedStateLoadoutError("不能在同一次保存中切换冻结背包快照")
            items_by_uid = self._frozen_items
            shapes = self._frozen_shapes
        else:
            items_by_uid = {
                (item["uid_slot"], item["uid_serial"]): item
                for item in self.user_dao.list_inventory_items(selected_snapshot_id)
            }
            shapes = {shape["shape_id"]: shape for shape in self.static_dao.list_shapes()}

        assignments: list[dict[str, Any]] = []
        if "native_layout" in role_state:
            try:
                assignments.extend(validate_native_layout(role_state, items_by_uid, shapes, parse_uid=_saved_uid))
            except (ValueError, TypeError, KeyError) as error:
                raise SavedStateLoadoutError("原生配装的冻结布局或装备身份验收失败") from error
            blocks = []
        else:
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
        normalized_payload = dict(payload or {
            "schema": "saved-state-official-loadout-v1",
            "source": "equipment_page",
            "source_role_name": role_name,
        })
        exact_score = exact_assignment_score_total(
            assignments,
            normalized_payload.get("assignment_scores") or {},
        )
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
            payload=normalized_payload,
            score=exact_score if exact_score is not None else score,
            module_count=module_count,
        )
