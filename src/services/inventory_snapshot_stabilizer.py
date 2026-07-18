"""把 nte-core 的连续完整背包快照收敛为可持久化的稳定版本。

稳定器不猜测玩家应有多少件物品。它只观察完整快照的实际内容：当内容在配置的
静默时间内没有再次变化，调用方才可以把最新快照提交到用户数据库。提交后稳定器
仍会继续接收后续变化，适合贯穿应用生命周期的后台同步。
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal


SnapshotStatus = Literal[
    "collecting",
    "changed",
    "duplicate",
    "unchanged",
    "reverted",
    "ignored",
]


@dataclass(frozen=True)
class StableInventorySnapshot:
    """已经满足静默时间、等待数据库提交的快照。"""

    message: dict[str, Any]
    payload: dict[str, Any]
    fingerprint: str
    item_count: int
    uid_count: int
    generation: int | None
    sequence: int | None
    first_seen_at: float
    last_changed_at: float


@dataclass(frozen=True)
class SnapshotOfferResult:
    """一次快照输入对当前稳定周期造成的影响。"""

    status: SnapshotStatus
    item_count: int | None = None
    added_count: int = 0
    removed_count: int = 0
    reason: str | None = None

    @property
    def accepted(self) -> bool:
        return self.status not in {"ignored", "unchanged"}


def _integer_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _snapshot_parts(snapshot: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    message = dict(snapshot)
    if message.get("method") == "event.inventory.snapshot":
        params = message.get("params")
        if not isinstance(params, Mapping):
            raise ValueError("背包事件 params 必须是对象")
        return message, dict(params)
    return message, message


def _canonical_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """消除条目和词条数组的无意义顺序差异，但保留全部业务字段。"""

    canonical = dict(item)
    for field in ("main_stats", "sub_stats"):
        stats = canonical.get(field)
        if isinstance(stats, list):
            canonical[field] = sorted(
                stats,
                key=lambda value: json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
    return canonical


def _validated_content(
    snapshot: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], int, frozenset[tuple[int, int]], str]:
    message, payload = _snapshot_parts(snapshot)
    if payload.get("complete") is not True:
        raise ValueError("只接收 complete=true 的完整背包快照")

    item_count = payload.get("item_count")
    if isinstance(item_count, bool) or not isinstance(item_count, int) or item_count < 0:
        raise ValueError("背包快照 item_count 必须是非负整数")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("背包快照 items 必须是数组")
    if item_count != len(items):
        raise ValueError(
            f"背包快照声明 {item_count} 件，但实际包含 {len(items)} 条"
        )

    canonical_items: list[dict[str, Any]] = []
    uids: set[tuple[int, int]] = set()
    for index, raw_item in enumerate(items):
        if not isinstance(raw_item, Mapping):
            raise ValueError(f"背包条目 items[{index}] 必须是对象")
        uid = raw_item.get("uid")
        if not isinstance(uid, Mapping):
            raise ValueError(f"背包条目 items[{index}].uid 必须是对象")
        slot = _integer_or_none(uid.get("slot"))
        serial = _integer_or_none(uid.get("serial"))
        if slot is None or serial is None:
            raise ValueError(f"背包条目 items[{index}].uid 必须包含非负整数 slot/serial")
        key = (slot, serial)
        if key in uids:
            raise ValueError(f"背包快照包含重复 UID：slot={slot}, serial={serial}")
        uids.add(key)
        canonical_items.append(_canonical_item(raw_item))

    canonical_items.sort(
        key=lambda item: (
            int(item["uid"]["slot"]),
            int(item["uid"]["serial"]),
        )
    )
    content = json.dumps(
        canonical_items,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    fingerprint = hashlib.sha256(content).hexdigest()
    return message, payload, item_count, frozenset(uids), fingerprint


class InventorySnapshotStabilizer:
    """在同一个 nte-core 会话内判断完整背包快照何时稳定。

    ``offer`` 和 ``ready`` 应由同一个工作线程调用。重复快照不会延后静默截止
    时间；任何实际内容变化都会开启新的静默窗口。
    """

    def __init__(
        self,
        settle_seconds: float = 5.0,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if settle_seconds <= 0:
            raise ValueError("settle_seconds 必须大于 0")
        self.settle_seconds = float(settle_seconds)
        self._clock = clock
        self._candidate: StableInventorySnapshot | None = None
        self._candidate_uids: frozenset[tuple[int, int]] = frozenset()
        self._committed_fingerprint: str | None = None
        self._committed_uids: frozenset[tuple[int, int]] = frozenset()
        self._last_generation: int | None = None
        self._last_sequence: int | None = None

    @property
    def has_pending_changes(self) -> bool:
        return self._candidate is not None

    @property
    def pending_item_count(self) -> int | None:
        return self._candidate.item_count if self._candidate is not None else None

    @property
    def committed_fingerprint(self) -> str | None:
        return self._committed_fingerprint

    def _is_out_of_order(self, generation: int | None, sequence: int | None) -> bool:
        if generation is None or sequence is None:
            return False
        if self._last_generation is None or self._last_sequence is None:
            return False
        if generation < self._last_generation:
            return True
        return generation == self._last_generation and sequence <= self._last_sequence

    def offer(
        self,
        snapshot: Mapping[str, Any],
        *,
        received_at: float | None = None,
    ) -> SnapshotOfferResult:
        now = self._clock() if received_at is None else float(received_at)
        try:
            message, payload, item_count, uids, fingerprint = _validated_content(snapshot)
        except (TypeError, ValueError) as exc:
            return SnapshotOfferResult("ignored", reason=str(exc))

        generation = _integer_or_none(payload.get("generation"))
        sequence = _integer_or_none(payload.get("sequence"))
        if self._is_out_of_order(generation, sequence):
            return SnapshotOfferResult(
                "ignored",
                item_count=item_count,
                reason="忽略重复或乱序的 generation/sequence",
            )
        if generation is not None and sequence is not None:
            self._last_generation = generation
            self._last_sequence = sequence

        if self._candidate is not None and fingerprint == self._candidate.fingerprint:
            return SnapshotOfferResult("duplicate", item_count=item_count)
        if self._candidate is None and fingerprint == self._committed_fingerprint:
            return SnapshotOfferResult("unchanged", item_count=item_count)

        previous_uids = self._candidate_uids if self._candidate is not None else self._committed_uids
        added_count = len(uids - previous_uids)
        removed_count = len(previous_uids - uids)

        if self._candidate is not None:
            first_seen_at = self._candidate.first_seen_at
            status: SnapshotStatus = "changed"
        else:
            first_seen_at = now
            status = "collecting"

        if fingerprint == self._committed_fingerprint:
            self._candidate = None
            self._candidate_uids = frozenset()
            return SnapshotOfferResult(
                "reverted",
                item_count=item_count,
                added_count=added_count,
                removed_count=removed_count,
            )

        self._candidate = StableInventorySnapshot(
            message=message,
            payload=payload,
            fingerprint=fingerprint,
            item_count=item_count,
            uid_count=len(uids),
            generation=generation,
            sequence=sequence,
            first_seen_at=first_seen_at,
            last_changed_at=now,
        )
        self._candidate_uids = uids
        return SnapshotOfferResult(
            status,
            item_count=item_count,
            added_count=added_count,
            removed_count=removed_count,
        )

    def ready(self, *, now: float | None = None) -> StableInventorySnapshot | None:
        candidate = self._candidate
        if candidate is None:
            return None
        current = self._clock() if now is None else float(now)
        if current - candidate.last_changed_at < self.settle_seconds:
            return None
        return candidate

    def mark_committed(self, fingerprint: str) -> None:
        candidate = self._candidate
        if candidate is None or candidate.fingerprint != fingerprint:
            raise ValueError("只能提交当前已经稳定的候选快照")
        self._committed_fingerprint = candidate.fingerprint
        self._committed_uids = self._candidate_uids
        self._candidate = None
        self._candidate_uids = frozenset()

    def seed_committed(self, snapshot: Mapping[str, Any]) -> None:
        """用数据库中的当前快照初始化去重基线，不开启新的稳定周期。"""

        _message, _payload, _count, uids, fingerprint = _validated_content(snapshot)
        self._committed_fingerprint = fingerprint
        self._committed_uids = uids
        self._candidate = None
        self._candidate_uids = frozenset()
        # generation/sequence 只在当前 nte-core 进程会话内有序。应用重启后新会话可能
        # 从较小的序号重新开始，因此数据库中的旧序号不能作为乱序过滤基线。
        self._last_generation = None
        self._last_sequence = None
