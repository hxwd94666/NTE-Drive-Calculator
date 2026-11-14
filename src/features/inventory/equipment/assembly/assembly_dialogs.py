# 生成游戏界面自动装配完成或未完成时的汇总对话框内容。
"""Presentation helpers for equipment-assembly confirmation reports."""

from __future__ import annotations

from src.ui.common.text_catalog import resolve_text_catalog


def assembly_report_dialog(
    action_name: str,
    report,
    expected_role_count: int | None = None,
    catalog=None,
):
    text = resolve_text_catalog(catalog).render
    role_count = len(getattr(report, "role_reports", []) or [])
    action_count = getattr(report, "executed_actions", 0)
    missing = list(getattr(report, "missing_roles", []) or [])
    skipped = list(getattr(report, "skipped_roles", []) or [])
    duplicates = list(getattr(report, "duplicate_roles", []) or [])
    unrecognized = list(getattr(report, "unrecognized_roles", []) or [])
    verification_failures = list(
        getattr(report, "verification_failures", []) or []
    )
    incomplete = bool(
        missing or skipped or duplicates or verification_failures
        or getattr(report, "stopped", False) or getattr(report, "error", "")
        or any(getattr(row, "skipped_actions", ()) for row in (getattr(report, "role_reports", ()) or ()))
    )
    verified_roles = getattr(report, "verified_roles", None)
    if verified_roles is not None and len(verified_roles) < role_count:
        incomplete = True
    if expected_role_count is not None and role_count < expected_role_count:
        incomplete = True
    if role_count == 0:
        incomplete = True

    title = text("assembly.report.incomplete" if incomplete else "assembly.report.completed", action=action_name)
    lines = [
        text("assembly.report.summary", count=role_count, actions=action_count)
    ]
    if getattr(report, "error", ""):
        lines.append(text("assembly.status.problem", role=getattr(report, "current_role", None) or text("selector.unknown_role"),
                          reason=report.error))
    if getattr(report, "stopped", False):
        lines.append(text("assembly.stopped_detail"))
    if expected_role_count is not None and role_count < expected_role_count:
        lines.append(
            text("assembly.report.remaining", count=expected_role_count, remaining=expected_role_count - role_count)
        )
    if missing:
        lines.append(text("assembly.report.missing", roles="、".join(str(role) for role in missing)))
    if skipped:
        lines.append(text("assembly.report.skipped", roles="、".join(str(role) for role in skipped)))
    if duplicates:
        lines.append(text("assembly.report.duplicates", count=len(duplicates)))
    if unrecognized:
        lines.append(text("assembly.report.unrecognized", count=len(unrecognized)))
        for entry in unrecognized:
            if not isinstance(entry, dict):
                lines.append(f"- {entry}")
                continue
            if entry.get("roster_index") is not None:
                position = text("assembly.report.roster_position", index=int(entry["roster_index"]) + 1)
            elif (
                entry.get("page_index") is not None
                and entry.get("slot_index") is not None
            ):
                position = (
                    text("assembly.report.page_position", page=int(entry["page_index"]) + 1,
                         index=int(entry["slot_index"]) + 1)
                )
            else:
                position = text("assembly.report.unknown_position")
            raw_text = (
                str(entry.get("raw_text") or "").strip() or text("assembly.report.no_text")
            )
            lines.append(text("assembly.report.ocr_detail", position=position, content=raw_text))
    duplicate_missing: list[tuple[str, str, int | None]] = []
    if verification_failures:
        lines.append(
            text("assembly.report.verification_failed", count=len(verification_failures))
        )
        ordinary_missing: list[tuple[str, str]] = []
        empty_screenshots: list[str] = []
        for failure in verification_failures:
            if not isinstance(failure, dict):
                continue
            role_name = str(failure.get("role_name") or text("selector.unknown_role"))
            if failure.get("reason") == "empty_screenshot":
                empty_screenshots.append(role_name)
                continue
            for item in failure.get("missing_blocks") or []:
                if (
                    not isinstance(item, dict)
                    or item.get("block_id") is None
                ):
                    continue
                block_id = str(item["block_id"])
                if item.get("is_duplicate_drive"):
                    duplicate_missing.append(
                        (
                            role_name,
                            block_id,
                            int(item["duplicate_count"])
                            if item.get("duplicate_count") is not None
                            else None,
                        )
                    )
                else:
                    ordinary_missing.append((role_name, block_id))
        for role_name, block_id, duplicate_count in duplicate_missing:
            count_text = (
                text("assembly.report.duplicate_count", count=duplicate_count)
                if duplicate_count
                else ""
            )
            lines.append(
                text("assembly.report.duplicate_block", role=role_name, block=block_id, detail=count_text)
            )
        for role_name, block_id in ordinary_missing:
            lines.append(
                text("assembly.report.missing_block", role=role_name, block=block_id)
            )
        for role_name in empty_screenshots:
            lines.append(text("assembly.report.empty_screenshot", role=role_name))
        if duplicate_missing:
            lines.append(
                text("assembly.report.duplicate_reason")
            )
            lines.append(
                text("assembly.report.duplicate_next")
            )
    if incomplete and (missing or skipped or duplicates):
        lines.append(text("assembly.report.check_roles"))
    elif incomplete and verification_failures and not duplicate_missing:
        lines.append(text("assembly.report.check_screen"))
    elif unrecognized:
        lines.append(text("assembly.report.other_slots"))
    return title, "\n".join(lines), not incomplete

