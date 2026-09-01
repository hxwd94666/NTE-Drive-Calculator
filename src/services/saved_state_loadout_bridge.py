# 把旧配装页保存的棋盘结果转换为官方 UID/坐标的 SQLite 配装方案。
"""把旧配装页保存的棋盘结果转换为官方 UID/坐标的 SQLite 配装方案。"""

from __future__ import annotations

from src.i18n import display_term, tr

import re
from dataclasses import dataclass
from typing import Any, Mapping

from src.domain.drive_layout import extract_drive_blocks_from_state
from src.services.virtual_equipment_service import (
    is_virtual_equipment_assignment,
    virtual_equipment_inventory_item,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


_SAVED_UID_PATTERN = re.compile(
    r"^nte-(?P<kind>module|core)-(?P<slot>\d+)-(?P<serial>\d+)$"
)
_KNOWN_FEMALE_AVATAR_IDS = frozenset({1051})


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
        raise SavedStateLoadoutError(tr("角色 [{role}] 缺少配置，无法确定官方角色 ID", role=display_term(role_name)))
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
        raise SavedStateLoadoutError(tr("角色 [{role}] 缺少有效的官方角色 ID", role=display_term(role_name)))
    return tuple(character_ids)


def character_id_for_saved_role(
    role_name: str,
    roles_db: Mapping[str, Any],
) -> int:
    """兼容单 ID 调用方；主角变体始终优先使用女性官方 ID。"""

    candidates = character_ids_for_saved_role(role_name, roles_db)
    return next(
        (candidate for candidate in candidates if candidate in _KNOWN_FEMALE_AVATAR_IDS),
        candidates[0],
    )


def resolve_character_id_for_saved_role(
    role_name: str,
    roles_db: Mapping[str, Any],
    user_dao: UserDataDao,
    *,
    snapshot_id: int | None = None,
) -> int:
    """用固定稳定快照中的角色实例 UID 选择账号实际使用的官方角色 ID。"""

    candidates = character_ids_for_saved_role(role_name, roles_db)
    female_candidate = next(
        (candidate for candidate in candidates if candidate in _KNOWN_FEMALE_AVATAR_IDS),
        None,
    )
    if female_candidate is not None:
        return female_candidate
    if len(candidates) == 1:
        return candidates[0]
    selected_snapshot_id = (
        user_dao.current_inventory_snapshot_id()
        if snapshot_id is None
        else snapshot_id
    )
    if selected_snapshot_id is None:
        raise SavedStateLoadoutError(tr("用户数据库中还没有稳定背包快照"))
    if user_dao.inventory_snapshot_summary(selected_snapshot_id) is None:
        raise SavedStateLoadoutError(
            tr("指定的稳定背包快照不存在：{snapshot}", snapshot=selected_snapshot_id)
        )

    candidate_set = set(candidates)

    def instances_in(candidate_snapshot_id: int) -> dict[int, set[tuple[int, int]]]:
        instance_uids: dict[int, set[tuple[int, int]]] = {}
        for character_id in candidate_set:
            for row in user_dao.list_character_instance_mappings(character_id):
                if (
                    row.get("source") != "snapshot"
                    or row.get("last_seen_snapshot_id") != candidate_snapshot_id
                ):
                    continue
                instance_uids.setdefault(character_id, set()).add((
                    int(row["uid_slot"]), int(row["uid_serial"]),
                ))
        return instance_uids

    def legacy_equipped_instances_in(candidate_snapshot_id: int) -> dict[int, set[tuple[int, int]]]:
        """Compatibility for snapshots imported before core exposed characters."""
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
                tr("角色 [{role}] 的官方 ID {id} 对应多个角色实例 UID",
                   role=display_term(role_name), id=character_id)
            )
        matched_text = "、".join(str(value) for value in sorted(instance_uids))
        raise SavedStateLoadoutError(
            tr("角色 [{role}] 的多个候选 ID 同时存在角色实例（{matched}），无法自动选择",
               role=display_term(role_name), matched=matched_text)
        )

    resolved = resolve_instances(instances_in(selected_snapshot_id))
    if resolved is None:
        resolved = resolve_instances(legacy_equipped_instances_in(selected_snapshot_id))
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
            tr("角色 [{role}] 的候选 ID 已保存多个角色实例（{matched}），请手动选择",
               role=display_term(role_name), matched=matched_text)
        )

    candidate_text = "、".join(str(value) for value in candidates)
    raise SavedStateLoadoutError(
        tr("角色 [{role}] 有多个候选官方 ID（{candidates}），"
           "但当前、历史稳定背包和已保存映射都没有可用于判断的角色实例",
           role=display_term(role_name), candidates=candidate_text)
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
        raise SavedStateLoadoutError(tr("角色名称不能为空"))
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
        raise SavedStateLoadoutError(
            tr("静态数据库中没有角色 [{role}] 的官方 ID", role=display_term(raw_name))
        )
    return ids


def custom_character_id_for_role(
    role_name: str,
    user_dao: UserDataDao,
) -> int | None:
    """Resolve an account-owned custom role before querying release data."""

    raw_name = str(role_name).strip()
    if not raw_name:
        raise SavedStateLoadoutError(tr("角色名称不能为空"))
    matches = [
        row
        for row in user_dao.list_custom_characters()
        if str(row.get("name_zh") or "").strip() == raw_name
    ]
    if not matches:
        return None
    if len(matches) > 1:
        raise SavedStateLoadoutError(
            tr("自建角色 [{role}] 存在重复记录", role=display_term(raw_name))
        )
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
            tr("角色 [{role}] 的多个官方 ID 同时存在于固定快照，请手动选择", role=display_term(role_name))
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
        tr("角色 [{role}] 有多个候选官方 ID（{candidates}），"
           "请先在一键装配中手动选择角色实例并保存映射",
           role=display_term(role_name), candidates=candidate_text)
    )


def _saved_uid(value: Any, *, expected_kind: str) -> tuple[int, int]:
    match = _SAVED_UID_PATTERN.fullmatch(str(value or "").strip())
    if match is None or match.group("kind") != expected_kind:
        raise SavedStateLoadoutError(
            tr("无效的 {kind} UID：{value}", kind=expected_kind, value=repr(value))
        )
    return int(match.group("slot")), int(match.group("serial"))


def _shape_id(geometry: Any) -> str:
    name = str(geometry or "").strip()
    if not name:
        raise SavedStateLoadoutError(tr("背包驱动缺少官方 geometry"))
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
        raise SavedStateLoadoutError(tr("配装棋盘或官方形状坐标无效")) from exc
    if not occupied or not offsets or len(occupied) != len(offsets):
        raise SavedStateLoadoutError(tr("配装棋盘占用格与官方形状面积不一致"))

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
            tr("无法唯一确定官方配装锚点：occupied={occupied}", occupied=sorted(occupied))
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
                tr("保存配装方案必须显式指定计算使用的稳定背包快照")
            )
        selected_snapshot_id = int(snapshot_id)
        if selected_snapshot_id is None:
            raise SavedStateLoadoutError(tr("用户数据库中还没有稳定背包快照"))
        if self.user_dao.inventory_snapshot_summary(selected_snapshot_id) is None:
            raise SavedStateLoadoutError(
                tr("指定的稳定背包快照不存在：{snapshot}", snapshot=selected_snapshot_id)
            )

        character = self.static_dao.get_character(character_id)
        custom_character_id = custom_character_id_for_role(role_name, self.user_dao)
        is_matching_custom_role = custom_character_id == int(character_id)
        if character is None and not is_matching_custom_role:
            raise SavedStateLoadoutError(
                tr("静态数据库中不存在角色 ID {id}（{role}）", id=character_id, role=display_term(role_name))
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
                    tr("角色 [{role}] 的棋盘块 {block} 没有对应驱动",
                       role=display_term(role_name), block=block.get("block_id"))
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
                    tr("角色 [{role}] 的驱动 UID ({slot}, {serial}) 不在所选稳定背包中",
                       role=display_term(role_name), slot=slot, serial=serial)
                )
            official_shape_id = _shape_id(item.get("geometry"))
            shape = shapes.get(official_shape_id)
            if shape is None:
                raise SavedStateLoadoutError(
                    tr("静态数据库缺少形状 {shape}", shape=official_shape_id)
                )
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
                    tr("角色 [{role}] 的核心 UID ({slot}, {serial}) 不在所选稳定背包中",
                       role=display_term(role_name), slot=core_slot, serial=core_serial)
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
            raise SavedStateLoadoutError(tr("角色 [{role}] 没有可装配的驱动", role=display_term(role_name)))
        return PreparedLoadoutPlan(
            name=name or tr("配装页：{role}", role=display_term(role_name)),
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
