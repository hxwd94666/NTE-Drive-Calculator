# 汇总极速装配完成结果与提示。
"""Compact, actionable summaries for the fast-equipment completion dialog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.ui.common.text_catalog import resolve_text_catalog


def build_fast_apply_completion_summary(
    applied: Sequence[Mapping[str, Any]],
    *,
    mismatch_role_names: frozenset[str] = frozenset(),
    skipped_roles: Sequence[Mapping[str, Any]] = (),
    catalog=None,
) -> tuple[str, str]:
    """Return dispatched equipment counts without claiming fragment verification.

    Equipment residual events are not a complete inventory view.  They remain
    useful for internal retry diagnostics, but presenting their absence or a
    partial match as a player-visible loadout result would be misleading.
    """

    text = resolve_text_catalog(catalog).render
    del mismatch_role_names
    last_attempt = max((int(row.get("attempt_count") or 1) for row in applied), default=1)

    summary = text("assembly.fast.summary", count=len(applied))
    if last_attempt > 1:
        summary += text("assembly.fast.attempt", count=last_attempt)
    partial_count = sum(bool(row.get("skipped_assignments")) for row in applied)
    if partial_count:
        summary += text("assembly.fast.partial_count", count=partial_count)
    if skipped_roles:
        summary += text("assembly.fast.skipped_count", count=len(skipped_roles))
    if not applied and skipped_roles:
        summary = text("assembly.fast.no_equipment")
    lines = []
    for row in applied:
        role_name = str(row.get("role_name") or text("selector.unknown_role"))
        module_count = row.get("module_count")
        if module_count is None:
            detail = text("assembly.fast.dispatched")
        else:
            detail = text("assembly.fast.modules", count=int(module_count))
            if row.get("core_count"):
                detail += text("assembly.fast.core")
        if row.get("skipped_assignments"):
            detail = text("assembly.fast.partial_detail", detail=detail, skipped=_skip_details(row, text))
        lines.append(text("assembly.fast.role_detail", role=role_name, detail=detail))
    for row in skipped_roles:
        lines.append(text("assembly.fast.skipped_role", role=row.get("role_name") or text("selector.unknown_role"),
                          detail=_skip_details(row, text)))
    return summary, "\n".join(lines)


def _skip_details(row: Mapping[str, Any], text) -> str:
    skipped = row.get("skipped_assignments") or ()
    virtual = sum(item.get("reason") == "virtual" for item in skipped)
    missing = sum(item.get("reason") == "missing" for item in skipped)
    reasons = []
    if virtual:
        reasons.append(text("assembly.fast.virtual_count", count=virtual))
    if missing:
        reasons.append(text("assembly.fast.missing_count", count=missing))
    return text("assembly.fast.skip_reasons", reasons="、".join(reasons))
