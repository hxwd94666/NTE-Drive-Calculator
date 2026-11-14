# 装配展示只消费冻结槽位和证据状态，派发计数与完整方案确认分开维护。
from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class AssemblyTarget:
    slot_id: int
    character_id: int | None
    role_name: str
    slot_name: str
    score: float | None
    plan_id: int | None = None
    source_snapshot_id: int | None = None


@dataclass(frozen=True)
class AssemblyRow:
    target: AssemblyTarget
    state: str = "pending"
    dispatched: bool = False
    verified: bool = False
    scope: str = "none"
    detail: str = ""
    skip_counts: tuple[int, int] = (0, 0)


class AssemblyProgress:
    """由 owner 在 UI 线程投影；外部读者只取得不可变行，事件不反向改变冻结目标。"""

    def __init__(self, targets: tuple[AssemblyTarget, ...]):
        if len({target.slot_id for target in targets}) != len(targets):
            raise ValueError("装配目标槽位重复")
        self.rows = tuple(AssemblyRow(target) for target in targets)
        self.phase = "preparing"

    def update(
        self, slot_id: int, state: str, *, dispatched=None, verified=None, scope=None, detail=None, skip_counts=None
    ):
        changes = {"state": state}
        for key, value in (
            ("dispatched", dispatched),
            ("verified", verified),
            ("scope", scope),
            ("detail", detail),
            ("skip_counts", skip_counts),
        ):
            if value is not None:
                changes[key] = value
        self.rows = tuple(replace(row, **changes) if row.target.slot_id == slot_id else row for row in self.rows)

    def finish_pending(self, *, stopped=False):
        for row in self.rows:
            if row.state in {"pending", "operating", "waiting"}:
                state = (
                    "unknown" if row.dispatched or row.state == "operating" else "not_started" if stopped else "skipped"
                )
                self.update(row.target.slot_id, state)
        self.phase = "stopped" if stopped else "finished"

    @property
    def dispatched(self):
        return sum(row.dispatched for row in self.rows)

    @property
    def confirmed(self):
        return sum(row.state == "confirmed" and row.verified and row.scope == "full" for row in self.rows)

    @property
    def partial(self):
        return sum(row.state == "partial" for row in self.rows)

    def count_state(self, *states):
        return sum(row.state in states for row in self.rows)
