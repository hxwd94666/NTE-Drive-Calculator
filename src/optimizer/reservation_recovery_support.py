# 缓存同分预留恢复中按角色和驱动类型的候选排序。
"""Bounded, request-scoped helpers for deferred-reservation recovery."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from src.models.equipment import Drive


@dataclass
class ProtectedDriveScreenCache:
    """Cache complete frozen role/type rankings and refill filtered Top-K slots.

    The cache contains no mutable allocation state.  Each progressive round only
    filters the already ranked sequence, so protected UIDs automatically expose
    the next legal candidate rather than shrinking a Top-K bucket.
    """

    strategy: Any
    full_drives: list[Drive]
    configs: dict[str, dict]
    _ranked: dict[tuple[str, str], dict[int, tuple[Drive, ...]]] = field(
        default_factory=dict,
    )

    def select(
        self,
        roles: list[str],
        shapes: set[str],
        candidate_limit: int,
        unavailable_uids: set[str],
    ) -> list[Drive]:
        """Return per-depth Top-K candidates after dynamic exclusions."""

        selected: dict[str, Drive] = {}
        for role in roles:
            for shape in shapes:
                for ranked in self._rankings(role, shape).values():
                    kept = 0
                    for drive in ranked:
                        if drive.uid in unavailable_uids:
                            continue
                        selected[drive.uid] = drive
                        kept += 1
                        if kept >= candidate_limit:
                            break
        return [selected[uid] for uid in sorted(selected)]

    def fallback_candidates(
        self,
        role: str,
        shape: str,
        limit: int,
        unavailable_uids: set[str],
    ) -> tuple[Drive, ...]:
        """Expose a bounded slice for the optional local recovery refinement."""

        values: list[Drive] = []
        for ranked in self._rankings(role, shape).values():
            values.extend(drive for drive in ranked if drive.uid not in unavailable_uids)
        return tuple(values[:limit])

    def _rankings(self, role: str, shape: str) -> dict[int, tuple[Drive, ...]]:
        key = (role, shape)
        cached = self._ranked.get(key)
        if cached is not None:
            return cached
        config = self.configs.get(role)
        buckets: dict[int, list[Drive]] = defaultdict(list)
        for drive in self.full_drives:
            if drive.shape_id != shape:
                continue
            if not self.strategy._item_allowed_for_role(drive, config):
                continue
            depth = self.strategy._stat_priority_depth(role, drive, config)
            if not (float(drive.role_scores.get(role, 0.0)) > 0 or depth > 0):
                continue
            buckets[depth if bool(isinstance(config, dict) and config.get("stats")) else 0].append(drive)
        cached = {
            depth: tuple(
                sorted(
                    values,
                    key=(
                        (lambda item: (float(item.role_scores.get(role, 0.0)), item.uid))
                        if bool(isinstance(config, dict) and config.get("stats"))
                        else (lambda item: (
                            self.strategy._stat_priority_depth(role, item, config),
                            float(item.role_scores.get(role, 0.0)), item.uid,
                        ))
                    ),
                    reverse=True,
                )
            )
            for depth, values in buckets.items()
        }
        self._ranked[key] = cached
        return cached
