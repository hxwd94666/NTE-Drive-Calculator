# 对 SQLite 装配方案执行一键装配，并可选择等待后续稳定快照验证结果。
"""本地核心组件一键装配的前置检查、调用和结果确认。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import time
from typing import Any, Protocol

from src.services.character_instance_cache import (
    CharacterInstanceCache,
    default_character_instance_cache_path,
    mirror_user_character_instance_cache,
)
from src.storage.sqlite.user_data_dao import UserDataDao

from .inventory_sync_service import InventorySyncState


MAX_UID_COMPONENT = 4_294_967_295


class EquipmentApplyError(RuntimeError):
    """装配未满足安全前提，或新快照未能确认装配结果。"""


class _LiveInventorySync(Protocol):
    @property
    def state(self) -> InventorySyncState: ...

    @property
    def is_running(self) -> bool: ...

    @property
    def core_hello_result(self) -> dict[str, Any] | None: ...

    def equip_one_key(self, **kwargs: Any) -> Any: ...

    def equip_module(self, **kwargs: Any) -> Any: ...

    def move_module_to_character(self, **kwargs: Any) -> Any: ...

    def wait_for_snapshot(
        self, *, after_snapshot_id: int | None = None, timeout: float = 30.0
    ) -> InventorySyncState: ...


@dataclass(frozen=True)
class EquipmentApplyResult:
    """由当前或后续稳定背包快照确认，或已下发的一键装配结果。"""

    plan_id: int
    before_snapshot_id: int
    after_snapshot_id: int
    character_uid: dict[str, int]
    rpc_result: Any
    verified: bool = True
    already_applied: bool = False


def _uid(value: Mapping[str, Any], field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for component in ("slot", "serial"):
        raw = value.get(component)
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise EquipmentApplyError(f"{field}.{component} 必须是整数")
        if raw <= 0 or raw >= MAX_UID_COMPONENT:
            raise EquipmentApplyError(
                f"{field}.{component} 必须在 1 到 {MAX_UID_COMPONENT - 1} 之间"
            )
        result[component] = raw
    return result


def _item_uid(item: Mapping[str, Any]) -> dict[str, int]:
    return _uid(
        {"slot": item["uid_slot"], "serial": item["uid_serial"]},
        "equipment",
    )


class EquipmentApplyService:
    """把已保存方案交给持续运行的核心组件，并确认最终背包状态。"""

    def __init__(
        self, user_dao: UserDataDao, sync_service: _LiveInventorySync,
        *, instance_cache_path: str | None = None,
    ) -> None:
        self.user_dao = user_dao
        self.sync_service = sync_service
        self.instance_cache_path = instance_cache_path
        self._instance_cache_mirrored = False

    def _mirror_instance_cache(self) -> None:
        """Bootstrap the public-local cache from existing private mappings once."""
        if self._instance_cache_mirrored:
            return
        self._instance_cache_mirrored = True
        try:
            mirror_user_character_instance_cache(
                self.user_dao,
                cache_path=self.instance_cache_path,
            )
        except Exception:
            # A cache mirror can be recreated later from private data, so it
            # must never block a valid equipment action.
            pass

    def _remember_character_uid(
        self, character_id: int, uid: Mapping[str, Any], *, source: str,
    ) -> None:
        """Keep an account-scoped copy outside user data without blocking apply."""
        try:
            with CharacterInstanceCache(
                self.instance_cache_path
                or default_character_instance_cache_path(self.user_dao.database_path)
            ) as cache:
                cache.upsert(self.user_dao.profile()["account_id"], character_id, uid, source=source)
        except Exception:
            # The account database remains the authority.  A read-only install
            # must not make an otherwise valid fast-assembly request fail.
            pass

    def _shared_cached_character_uid(self, character_id: int) -> dict[str, int] | None:
        try:
            with CharacterInstanceCache(
                self.instance_cache_path
                or default_character_instance_cache_path(self.user_dao.database_path)
            ) as cache:
                return cache.get(self.user_dao.profile()["account_id"], character_id)
        except Exception:
            return None

    def require_stable_snapshot(self) -> int:
        """Validate the live sync once and return the snapshot to pin for a job."""
        state = self.sync_service.state
        if not self.sync_service.is_running or state.phase != "listening":
            raise EquipmentApplyError("背包同步必须处于稳定监听状态才能一键装配")
        snapshot_id = self.user_dao.current_inventory_snapshot_id()
        if snapshot_id is None or state.last_snapshot_id != snapshot_id:
            raise EquipmentApplyError("同步状态与当前稳定背包快照不一致，请等待同步完成")
        return snapshot_id

    def resolve_character_uid(
        self,
        character_id: int,
        snapshot_id: int,
        explicit_uid: Mapping[str, Any] | None = None,
    ) -> dict[str, int]:
        """解析账号内角色实例 UID；当前为空时回查该账号的稳定历史快照。"""

        if explicit_uid is not None:
            return _uid(explicit_uid, "character")

        self._mirror_instance_cache()

        def candidates_for(candidate_snapshot_id: int) -> set[tuple[int, int]]:
            candidates: set[tuple[int, int]] = set()
            for item in self.user_dao.list_inventory_items(
                candidate_snapshot_id,
                equipped=True,
                character_id=character_id,
            ):
                raw_uid = item.get("equipped_character_uid")
                if isinstance(raw_uid, Mapping):
                    validated = _uid(raw_uid, "equipped_character_uid")
                    candidates.add((validated["slot"], validated["serial"]))
            return candidates

        current_candidates = candidates_for(snapshot_id)
        if len(current_candidates) == 1:
            slot, serial = next(iter(current_candidates))
            uid = {"slot": slot, "serial": serial}
            self._remember_character_uid(character_id, uid, source="snapshot")
            return uid
        if len(current_candidates) > 1:
            raise EquipmentApplyError(
                f"当前稳定背包中角色 {character_id} 对应多个角色实例 UID"
            )

        # nte-core 目前只能从“装备已穿在角色身上”的背包项中观察角色 UID。
        # 所以一旦已从任意稳定快照得到并持久化实例映射，缓存就是当前账号
        # 的稳定身份来源；不能让更早的历史快照噪声抢在它前面导致手动选择。
        mapped_rows = self.user_dao.list_character_instance_mappings(character_id)
        manual_candidates = {
            (row["uid_slot"], row["uid_serial"])
            for row in mapped_rows if row.get("source") == "manual"
        }
        if len(manual_candidates) == 1:
            slot, serial = next(iter(manual_candidates))
            uid = {"slot": slot, "serial": serial}
            self._remember_character_uid(character_id, uid, source="manual")
            return uid
        if len(manual_candidates) > 1:
            raise EquipmentApplyError(
                f"角色 {character_id} 存在多个手动保存的实例 UID，请在装配前整理映射"
            )
        mapped_candidates = {
            (row["uid_slot"], row["uid_serial"])
            for row in mapped_rows
        }
        if len(mapped_candidates) == 1:
            slot, serial = next(iter(mapped_candidates))
            uid = {"slot": slot, "serial": serial}
            self._remember_character_uid(character_id, uid, source="snapshot")
            return uid
        if len(mapped_candidates) > 1:
            raise EquipmentApplyError(
                f"角色 {character_id} 存在多个已保存的实例 UID，请在装配前手动选择"
            )

        shared_cached_uid = self._shared_cached_character_uid(character_id)
        if shared_cached_uid is not None:
            return _uid(shared_cached_uid, "public_character_instance_cache")

        historical_candidates: set[tuple[int, int]] = set()
        for summary in self.user_dao.list_inventory_snapshots():
            historical_snapshot_id = int(summary["snapshot_id"])
            if historical_snapshot_id >= snapshot_id:
                continue
            historical_candidates.update(candidates_for(historical_snapshot_id))
        if len(historical_candidates) == 1:
            slot, serial = next(iter(historical_candidates))
            uid = {"slot": slot, "serial": serial}
            self._remember_character_uid(character_id, uid, source="snapshot")
            return uid
        if len(historical_candidates) > 1:
            raise EquipmentApplyError(
                f"角色 {character_id} 在历史稳定背包中对应多个实例 UID，无法安全选择"
            )
        raise EquipmentApplyError(
            "无法从当前或历史稳定背包确定角色实例 UID，请手动选择并保存该角色实例"
        )

    @staticmethod
    def _plan_mismatch(
        *,
        items: list[dict[str, Any]],
        modules: list[dict[str, Any]],
        core_assignment: dict[str, Any],
        character_id: int,
        character_uid: dict[str, int],
    ) -> str | None:
        """返回方案与稳定快照的首个差异；完全一致时返回 ``None``。"""

        by_uid = {(item["uid_serial"], item["uid_slot"]): item for item in items}
        expected_uids = {
            (assignment["uid_serial"], assignment["uid_slot"])
            for assignment in modules
        }
        core_pair = (
            core_assignment["uid_serial"],
            core_assignment["uid_slot"],
        )
        expected_uids.add(core_pair)

        for assignment in modules:
            uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
            item = by_uid.get(uid_pair)
            expected_placement = {
                "row": assignment["target_row"],
                "column": assignment["target_column"],
            }
            if (
                item is None
                or not item["equipped"]
                or item["equipped_character_uid"] != character_uid
                or item["equipped_character_id"] != character_id
                or item["equipped_placement"] != expected_placement
            ):
                return f"驱动 UID {uid_pair} 的装配位置不一致"

        verified_core = by_uid.get(core_pair)
        if (
            verified_core is None
            or not verified_core["equipped"]
            or verified_core["equipped_character_uid"] != character_uid
            or verified_core["equipped_character_id"] != character_id
        ):
            return f"核心 UID {core_pair} 的装备状态不一致"

        actual_uids = {
            (item["uid_serial"], item["uid_slot"])
            for item in items
            if item["equipped"]
            and item["equipped_character_uid"] == character_uid
            and item["equipped_character_id"] == character_id
        }
        if actual_uids != expected_uids:
            return "角色当前装备数量或装备 UID 与方案不一致"
        return None

    @staticmethod
    def _module_plan_mismatch(
        *,
        items: list[dict[str, Any]],
        modules: list[dict[str, Any]],
        character_id: int,
        character_uid: dict[str, int],
    ) -> str | None:
        """Verify a driver-only plan without changing an existing core.

        ``equipment.equip_one_key`` requires a core, but a saved plan may
        intentionally contain drivers only.  In that case the current core and
        unrelated placements must remain untouched; only requested modules are
        verified.
        """

        by_uid = {(item["uid_serial"], item["uid_slot"]): item for item in items}
        for assignment in modules:
            uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
            item = by_uid.get(uid_pair)
            expected_placement = {
                "row": assignment["target_row"],
                "column": assignment["target_column"],
            }
            if (
                item is None
                or not item["equipped"]
                or item["equipped_character_uid"] != character_uid
                or item["equipped_character_id"] != character_id
                or item["equipped_placement"] != expected_placement
            ):
                return f"驱动 UID {uid_pair} 的装配位置不一致"
        return None

    def apply_plan(
        self,
        plan_id: int,
        *,
        character_uid: Mapping[str, Any] | None = None,
        timeout: float = 30.0,
        verify_after_dispatch: bool = True,
        stable_snapshot_id: int | None = None,
    ) -> EquipmentApplyResult:
        """执行方案。

        ``verify_after_dispatch`` 适合诊断或登录页抓包可用的环境，会等待新
        稳定背包快照并逐项确认。游戏内极速装配则只依赖已有快照做前置校验；
        指令成功下发后立即返回，不能把登录时才会出现的背包快照当作成功条件。
        """

        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        hello = self.sync_service.core_hello_result or {}
        capabilities = hello.get("capabilities", [])
        if not isinstance(capabilities, list) or "equipment" not in capabilities:
            raise EquipmentApplyError("当前 nte-core 不支持 equipment 能力")
        if stable_snapshot_id is None:
            before_snapshot_id = self.require_stable_snapshot()
        else:
            before_snapshot_id = int(stable_snapshot_id)
            if self.user_dao.inventory_snapshot_summary(before_snapshot_id) is None:
                raise EquipmentApplyError("指定的稳定背包快照不存在")

        plan = self.user_dao.get_loadout_plan(plan_id)
        if plan is None:
            raise EquipmentApplyError(f"装配方案 {plan_id} 不存在")
        assignments = plan["assignments"]
        modules = [item for item in assignments if item["kind"] == "module"]
        cores = [item for item in assignments if item["kind"] == "core"]
        if not 1 <= len(modules) <= 64 or len(cores) > 1:
            raise EquipmentApplyError("装配方案必须包含 1..64 个驱动，且至多包含 1 个核心")

        current_items = self.user_dao.list_inventory_items(before_snapshot_id)
        by_uid = {
            (item["uid_serial"], item["uid_slot"]): item for item in current_items
        }
        selected_uids: set[tuple[int, int]] = set()
        placements: list[dict[str, Any]] = []
        for index, assignment in enumerate(modules):
            uid_pair = (assignment["uid_serial"], assignment["uid_slot"])
            if uid_pair in selected_uids:
                raise EquipmentApplyError("方案中存在重复装备 UID")
            selected_uids.add(uid_pair)
            item = by_uid.get(uid_pair)
            if item is None or item["kind"] != "module":
                raise EquipmentApplyError(f"方案驱动 UID {uid_pair} 不在当前稳定背包中")
            if assignment.get("rotation") not in (None, 0):
                raise EquipmentApplyError("nte-core 一键装配不接受旋转参数")
            row = assignment.get("target_row")
            column = assignment.get("target_column")
            if row not in range(1, 6) or column not in range(1, 6):
                raise EquipmentApplyError(f"第 {index + 1} 个驱动位置必须在 1..5")
            placements.append(
                {
                    "equipment": _item_uid(item),
                    "row": row,
                    "column": column,
                }
            )
        core_assignment = cores[0] if cores else None
        core_item = None
        if core_assignment is not None:
            core_pair = (core_assignment["uid_serial"], core_assignment["uid_slot"])
            if core_pair in selected_uids:
                raise EquipmentApplyError("方案中存在重复装备 UID")
            core_item = by_uid.get(core_pair)
            if core_item is None or core_item["kind"] != "core":
                raise EquipmentApplyError(f"方案核心 UID {core_pair} 不在当前稳定背包中")
            if core_assignment.get("rotation") not in (None, 0):
                raise EquipmentApplyError("核心不能包含旋转参数")

        resolved_character_uid = self.resolve_character_uid(
            plan["character_id"], before_snapshot_id, character_uid
        )
        current_mismatch = (
            self._plan_mismatch(
                items=current_items,
                modules=modules,
                core_assignment=core_assignment,
                character_id=plan["character_id"],
                character_uid=resolved_character_uid,
            )
            if core_assignment is not None
            else self._module_plan_mismatch(
                items=current_items,
                modules=modules,
                character_id=plan["character_id"],
                character_uid=resolved_character_uid,
            )
        )
        if current_mismatch is None:
            return EquipmentApplyResult(
                plan_id=plan["plan_id"],
                before_snapshot_id=before_snapshot_id,
                after_snapshot_id=before_snapshot_id,
                character_uid=resolved_character_uid,
                rpc_result={"status": "already_applied"},
                already_applied=True,
            )

        if core_item is not None:
            rpc_result = self.sync_service.equip_one_key(
                character=resolved_character_uid,
                placements=placements,
                core=_item_uid(core_item),
                timeout=timeout,
            )
            if not verify_after_dispatch:
                return EquipmentApplyResult(
                    plan_id=plan["plan_id"],
                    before_snapshot_id=before_snapshot_id,
                    after_snapshot_id=before_snapshot_id,
                    character_uid=resolved_character_uid,
                    rpc_result=rpc_result,
                    verified=False,
                )
            after_state = self.sync_service.wait_for_snapshot(
                after_snapshot_id=before_snapshot_id,
                timeout=timeout,
            )
            after_snapshot_id = after_state.last_snapshot_id
        else:
            rpc_result = []
            after_snapshot_id = before_snapshot_id
            for placement, assignment in zip(placements, modules):
                source_item = by_uid[(assignment["uid_serial"], assignment["uid_slot"])]
                dispatcher = (
                    self.sync_service.move_module_to_character
                    if source_item["equipped"]
                    else self.sync_service.equip_module
                )
                for attempt in range(6):
                    try:
                        rpc_result.append(dispatcher(
                            character=resolved_character_uid,
                            equipment=placement["equipment"],
                            row=placement["row"],
                            column=placement["column"],
                        ))
                        break
                    except Exception as exc:
                        # 驱动逐个下发时，游戏插件仅有一个执行位和一个排队位。
                        # 极速模式没有背包回包可等，因此只对明确的队列繁忙做短暂
                        # 重试；其他错误仍立即交给上层显示。
                        if (
                            verify_after_dispatch
                            or "EQUIPMENT_PLUGIN_BUSY" not in str(exc)
                            or attempt == 5
                        ):
                            raise
                        time.sleep(0.35)
                if not verify_after_dispatch:
                    continue
                # The plugin permits only one active and one queued request.
                # Wait after every module so driver-only plans cannot overfill
                # that queue when they contain several placements.
                after_state = self.sync_service.wait_for_snapshot(
                    after_snapshot_id=after_snapshot_id,
                    timeout=timeout,
                )
                after_snapshot_id = after_state.last_snapshot_id
            if not verify_after_dispatch:
                return EquipmentApplyResult(
                    plan_id=plan["plan_id"],
                    before_snapshot_id=before_snapshot_id,
                    after_snapshot_id=before_snapshot_id,
                    character_uid=resolved_character_uid,
                    rpc_result=rpc_result,
                    verified=False,
                )
        if after_snapshot_id is None or after_snapshot_id <= before_snapshot_id:
            raise EquipmentApplyError("核心组件没有返回装配后的新稳定快照")

        mismatch = (
            self._plan_mismatch(
                items=self.user_dao.list_inventory_items(after_snapshot_id),
                modules=modules,
                core_assignment=core_assignment,
                character_id=plan["character_id"],
                character_uid=resolved_character_uid,
            )
            if core_assignment is not None
            else self._module_plan_mismatch(
                items=self.user_dao.list_inventory_items(after_snapshot_id),
                modules=modules,
                character_id=plan["character_id"],
                character_uid=resolved_character_uid,
            )
        )
        if mismatch is not None:
            raise EquipmentApplyError(f"新快照未确认目标配装：{mismatch}")
        return EquipmentApplyResult(
            plan_id=plan["plan_id"],
            before_snapshot_id=before_snapshot_id,
            after_snapshot_id=after_snapshot_id,
            character_uid=resolved_character_uid,
            rpc_result=rpc_result,
        )
