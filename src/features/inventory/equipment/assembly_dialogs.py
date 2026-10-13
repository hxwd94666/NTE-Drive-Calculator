# 生成游戏界面自动装配完成或未完成时的汇总对话框内容。
"""Presentation helpers for equipment-assembly confirmation reports."""

from __future__ import annotations


def assembly_report_dialog(
    action_name: str,
    report,
    expected_role_count: int | None = None,
):
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
    )
    if expected_role_count is not None and role_count < expected_role_count:
        incomplete = True
    if role_count == 0:
        incomplete = True

    title = f"{action_name}未完成" if incomplete else f"{action_name}完成"
    lines = [
        f"已装配 {role_count} 个角色，执行 {action_count} 个动作。"
    ]
    if expected_role_count is not None and role_count < expected_role_count:
        lines.append(
            f"预计装配 {expected_role_count} 个角色，还有 "
            f"{expected_role_count - role_count} 个未完成。"
        )
    if missing:
        lines.append("未找到角色：" + "、".join(str(role) for role in missing))
    if skipped:
        lines.append("跳过角色：" + "、".join(str(role) for role in skipped))
    if duplicates:
        lines.append(f"重复识别角色槽位：{len(duplicates)} 个。")
    if unrecognized:
        lines.append(f"未识别角色槽位：{len(unrecognized)} 个。")
        for entry in unrecognized:
            if not isinstance(entry, dict):
                lines.append(f"- {entry}")
                continue
            if entry.get("roster_index") is not None:
                position = f"第 {int(entry['roster_index']) + 1} 个角色"
            elif (
                entry.get("page_index") is not None
                and entry.get("slot_index") is not None
            ):
                position = (
                    f"第 {int(entry['page_index']) + 1} 页"
                    f"第 {int(entry['slot_index']) + 1} 个角色"
                )
            else:
                position = "未知位置"
            raw_text = (
                str(entry.get("raw_text") or "").strip() or "未读取到文字"
            )
            lines.append(f"- {position}（OCR：{raw_text}）")
    duplicate_missing: list[tuple[str, str, int | None]] = []
    if verification_failures:
        lines.append(
            f"图纸截图校验失败：{len(verification_failures)} 个角色。"
        )
        ordinary_missing: list[tuple[str, str]] = []
        empty_screenshots: list[str] = []
        for failure in verification_failures:
            if not isinstance(failure, dict):
                continue
            role_name = str(failure.get("role_name") or "未知角色")
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
                f"（同条件驱动共 {duplicate_count} 个）"
                if duplicate_count
                else ""
            )
            lines.append(
                f"- {role_name}：重复驱动块 #{block_id}{count_text} 未能确认装入。"
            )
        for role_name, block_id in ordinary_missing:
            lines.append(
                f"- {role_name}：驱动块 #{block_id} 未通过截图校验。"
            )
        for role_name in empty_screenshots:
            lines.append(f"- {role_name}：未取得游戏截图，无法确认图纸结果。")
        if duplicate_missing:
            lines.append(
                "原因：这些驱动在游戏筛选器中的形状、品质和词条条件相同，"
                "自动装配无法唯一定位目标，可能装错同类驱动或留下空位。"
            )
            lines.append(
                "处理：请在游戏内手动补装上述驱动；"
                "也可先让重复驱动的等级、锁定或弃置状态不同后再重试。"
            )
    if incomplete and (missing or skipped or duplicates):
        lines.append("请检查角色识别结果后重新执行。")
    elif incomplete and verification_failures and not duplicate_missing:
        lines.append("请检查游戏画面是否稳定、图纸位置是否正确后重新执行。")
    elif unrecognized:
        lines.append("其余未识别槽位不属于本次目标角色，不影响本次装配结果。")
    return title, "\n".join(lines), not incomplete

