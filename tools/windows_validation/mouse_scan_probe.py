# 只读校验鼠标视觉扫描诊断报告的完整性和三分辨率验收字段。
"""Read-only validation of one account-local mouse scan report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


SUPPORTED_RESOLUTIONS = {(1920, 1080), (2560, 1440), (3840, 2160)}
ALLOWED_WHEEL_AMOUNTS = {-280, -120}


def inspect_mouse_scan_report(path: str | Path | None) -> dict[str, Any]:
    result: dict[str, Any] = {"present": False, "passed": False, "issues": []}
    if path is None:
        result["issues"].append("未提供鼠标扫描诊断报告。")
        return result
    report_path = Path(path)
    if not report_path.is_file():
        result["issues"].append("鼠标扫描诊断报告不存在。")
        return result
    result["present"] = True
    try:
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        result["issues"].append("鼠标扫描诊断报告不是有效 UTF-8 JSON。")
        return result

    resolution = payload.get("resolution") or {}
    size = (int(resolution.get("width", 0) or 0), int(resolution.get("height", 0) or 0))
    inventory = payload.get("inventory") or {}
    expected = int(inventory.get("expected", 0) or 0)
    captured = int(inventory.get("captured", 0) or 0)
    preflight = payload.get("preflight") or {}
    checked = int(preflight.get("checked", 0) or 0)
    matched = int(preflight.get("matched", 0) or 0)
    pages = payload.get("pages") or []
    issues: list[str] = result["issues"]

    if payload.get("schema") != "mouse-visual-scan-report-v1":
        issues.append("报告 schema 不匹配。")
    if payload.get("status") != "complete":
        issues.append("扫描终态不是 complete。")
    if size not in SUPPORTED_RESOLUTIONS:
        issues.append("客户区分辨率不属于 1080p、2K 或 4K 验收集。")
    if expected <= 0 or captured != expected:
        issues.append("预计数量与捕获数量不一致。")
    if checked <= 0 or matched != checked:
        issues.append("首帧网格预检没有全部命中。")
    if not isinstance(pages, list) or not pages:
        issues.append("缺少逐页扫描指标。")
        pages = []

    next_index = 1
    wheel_count = 0
    for position, page in enumerate(pages):
        item_range = page.get("item_range") or []
        page_captured = int(page.get("captured", 0) or 0)
        if len(item_range) != 2:
            issues.append(f"第 {position + 1} 页物品范围无效。")
            continue
        first, last = int(item_range[0]), int(item_range[1])
        if first != next_index or last < first or page_captured != last - first + 1:
            issues.append(f"第 {position + 1} 页物品序号不连续。")
        next_index = last + 1
        amounts = [int(value) for value in page.get("wheel_amounts") or []]
        wheel_count += len(amounts)
        if any(value not in ALLOWED_WHEEL_AMOUNTS for value in amounts):
            issues.append(f"第 {position + 1} 页包含未批准的滚轮量。")
        if position < len(pages) - 1 and page.get("overlap_row") not in {0, 1, 2}:
            issues.append(f"第 {position + 1} 页缺少有效重叠行。")
    if next_index - 1 != captured:
        issues.append("逐页物品范围未覆盖完整捕获数量。")
    if int(payload.get("wheel_commands", 0) or 0) != wheel_count:
        issues.append("滚轮命令总数与逐页记录不一致。")

    result.update(
        {
            "passed": not issues,
            "resolution": list(size),
            "expected": expected,
            "captured": captured,
            "page_count": len(pages),
            "wheel_commands": wheel_count,
            "status": str(payload.get("status") or ""),
        }
    )
    return result


def compare_mouse_scan_to_account(
    report: dict[str, Any],
    account_summary: dict[str, Any],
) -> dict[str, Any]:
    """Prove that the report's completed mouse inventory is the account current snapshot."""

    current = account_summary.get("current_inventory") or {}
    issues: list[str] = []
    if not report.get("passed"):
        issues.append("鼠标扫描报告自身未通过。")
    if current.get("source") != "vision":
        issues.append("账号当前库存来源不是 vision。")
    if current.get("capture_driver") != "mouse":
        issues.append("账号当前视觉库存不是鼠标捕获。")
    if not current.get("complete"):
        issues.append("账号当前视觉库存不是完整快照。")
    captured = int(report.get("captured", 0) or 0)
    if captured <= 0 or int(current.get("stored_item_count", 0) or 0) != captured:
        issues.append("账号当前快照数量与鼠标扫描报告不一致。")
    return {
        "passed": not issues,
        "issues": issues,
        "snapshot_id": int(current.get("snapshot_id", 0) or 0),
        "source": str(current.get("source") or ""),
        "capture_driver": str(current.get("capture_driver") or ""),
        "stored_item_count": int(current.get("stored_item_count", 0) or 0),
    }
