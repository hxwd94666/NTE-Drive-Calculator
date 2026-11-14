# 汇总扫描后管理动作结果。
"""Text projection for completed scan state-management results."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.localization.catalog import localized_text
from src.ui.common.enum_text import ui_enum_text


def append_scan_post_action_summary(
    summary: str, stats: Mapping[str, Any], *, text_catalog=None,
) -> str:
    """Render the existing management summary and append any skipped-row notice."""

    if not stats.get("post_actions_enabled"):
        return summary
    planned = ui_enum_text(text_catalog, "计划") if stats.get("post_action_issue_count") else ""
    summary += localized_text(
        text_catalog,
        "scanning.post_action_summary.management",
        "\n扫描后管理：参与计算 {candidates} 件，目标变更 {targets} 个，已处理 {applied} 个。"
        "\n{planned}弃置 {discarded} 个，取消弃置 {cleared} 个；锁定 {locked} 个，取消锁定 {unlocked} 个。",
        candidates=int(stats.get("post_action_candidate_count", 0) or 0),
        targets=int(stats.get("post_action_target_count", 0) or 0),
        applied=int(stats.get("post_action_applied_count", 0) or 0),
        planned=planned,
        discarded=int(stats.get("discard_set_count", 0) or 0),
        cleared=int(stats.get("discard_clear_count", 0) or 0),
        locked=int(stats.get("lock_set_count", 0) or 0),
        unlocked=int(stats.get("lock_clear_count", 0) or 0),
    )
    filtered_parts = []
    for key, label in (
        ("post_action_quality_filtered_count", "品质范围过滤"),
        ("post_action_type_filtered_count", "处理类别过滤"),
        ("post_action_type_range_filtered_count", "类型范围过滤"),
        ("post_action_unmatched_count", "评分未匹配"),
        ("post_action_incomplete_count", "评分未完成（未执行评分动作）"),
    ):
        count = int(stats.get(key, 0) or 0)
        if count:
            filtered_parts.append(localized_text(
                text_catalog,
                "scanning.post_action_summary.filtered_item",
                "{label} {count} 件",
                label=ui_enum_text(text_catalog, label),
                count=count,
            ))
    if filtered_parts:
        summary += localized_text(
            text_catalog,
            "scanning.post_action_summary.filtered",
            "\n{items}。",
            items="，".join(filtered_parts),
        )
    return append_post_action_issue_summary(summary, stats, text_catalog=text_catalog)


def append_post_action_issue_summary(
    summary: str, stats: Mapping[str, Any], *, text_catalog=None,
) -> str:
    """Keep the notice compact; individual scan indexes belong in details."""

    issue_count = int(stats.get("post_action_issue_count", 0) or 0)
    if not issue_count:
        return summary
    applied = int(stats.get("post_action_applied_count", 0) or 0)
    return localized_text(
        text_catalog,
        "scanning.post_action_summary.issue",
        "{summary}\n状态管理已结束：成功 {applied} 件，跳过 {issue_count} 件。"
        "跳过的装备未执行状态修改，请查看详情。",
        summary=summary,
        applied=applied,
        issue_count=issue_count,
    )


def post_action_issue_details(stats: Mapping[str, Any], *, text_catalog=None) -> str:
    """Describe only the frozen plan and the pre-action failure."""

    actions = ui_enum_text(text_catalog, {
        "locked": "锁定",
        "discarded": "弃置",
        "normal": "恢复普通状态",
    })
    reasons = ui_enum_text(text_catalog, {
        "identity_mismatch": "详情与扫描截图不一致",
        "state_mismatch": "当前状态与计划不一致",
    })
    fallback_reason = ui_enum_text(text_catalog, "操作前校验未通过")
    fallback_action = ui_enum_text(text_catalog, "状态修改")
    return "\n".join(
        localized_text(
            text_catalog,
            "scanning.post_action_summary.issue_detail",
            "第 {index} 件：{reason}，已跳过原计划的{action}操作。",
            index=int(issue["index"]),
            reason=reasons.get(issue["reason"], fallback_reason),
            action=actions.get(issue["target_state"], fallback_action),
        )
        for issue in stats.get("post_action_issues", ()) or ()
    )


def show_scan_completion(
    parent: QWidget | None, title: str, summary: str, stats: Mapping[str, Any],
    *, text_catalog=None,
) -> None:
    """Use the existing completion notice, with one warning when rows were skipped."""

    if not int(stats.get("post_action_issue_count", 0) or 0):
        QMessageBox.information(parent, title, summary)
        return
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setText(summary)
    box.setDetailedText(post_action_issue_details(stats, text_catalog=text_catalog))
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.setDefaultButton(QMessageBox.StandardButton.Ok)
    box.adjustSize()
    fit_dialog_to_available_screen(box)
    QApplication.beep()
    box.exec()
