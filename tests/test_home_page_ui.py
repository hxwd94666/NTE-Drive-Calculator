# 验证工作台页面的公共界面行为。
"""Visible workbench hero contracts."""

from __future__ import annotations

from src.features.home.page import inventory_sync_error_guidance


def test_snapshot_save_guidance_distinguishes_database_failures() -> None:
    for category, expected in (
        ("BUSY", "锁冲突"),
        ("READONLY", "只读"),
        ("FULL", "容量限制"),
        ("CORRUPT", "损坏"),
        ("SCHEMA", "表、字段"),
        ("CONSTRAINT", "约束冲突"),
        ("TRANSACTION", "事务状态异常"),
        ("FAILED", "暂未识别具体原因"),
    ):
        guidance = inventory_sync_error_guidance(f"SNAPSHOT_SAVE_{category}", "无法导入背包快照")
        assert expected in guidance

