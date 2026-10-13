# 冻结配装管理所见的正式槽位、方案来源和计算锁，不携带可变页面状态。
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LoadoutAccount:
    account_id: str
    generation: int
    database_path: Path


@dataclass(frozen=True, slots=True)
class ManagedSlot:
    slot_id: int
    character_id: int
    slot_key: str
    slot_name: str
    revision: str
    plan_id: int | None
    source_snapshot_id: int | None
    plan_revision: str
    locked: bool
    role_name: str
    changed: bool

    @classmethod
    def from_row(cls, row):
        plan = row.get("current_plan") or {}
        payload = plan.get("payload") or {}
        return cls(int(row["slot_id"]), int(row["character_id"]),
                   str(row["slot_key"]), str(row["slot_name"]),
                   str(row.get("updated_at_utc") or ""), row.get("current_plan_id"),
                   plan.get("source_snapshot_id"), str(plan.get("updated_at_utc") or ""),
                   bool(plan.get("allocation_locked")), str(payload.get("source_role_name") or ""),
                   bool((payload.get("last_diff") or {}).get("changed")))


@dataclass(frozen=True, slots=True)
class LoadoutManagementSnapshot:
    account: LoadoutAccount
    slots: tuple[ManagedSlot, ...]
    character_id: int | None = None

    def slot(self, slot_id):
        return next((slot for slot in self.slots if slot.slot_id == slot_id), None)

    def plan(self, plan_id):
        return next((slot for slot in self.slots if slot.plan_id == plan_id), None)


@dataclass(frozen=True, slots=True)
class LoadoutMutation:
    action: str
    snapshot: LoadoutManagementSnapshot
    slot_id: int | None = None
    name: str = ""


@dataclass(frozen=True, slots=True)
class LoadoutMutationResult:
    action: str
    slot_id: int | None = None
    locked: bool | None = None
    skipped: tuple[str, ...] = ()
