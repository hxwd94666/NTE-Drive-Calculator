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
        for source in ("native", "packet"):
            guidance = inventory_sync_error_guidance(
                f"SNAPSHOT_SAVE_{category}", "无法导入背包快照", capture_source=source,
            )
            assert expected in guidance


def test_sync_error_login_guidance_depends_on_capture_source() -> None:
    for code in ("GAME_PROCESS_NOT_FOUND", "INVENTORY_NOT_READY"):
        native = inventory_sync_error_guidance(code, "", capture_source="native")
        packet = inventory_sync_error_guidance(code, "", capture_source="packet")
        unknown = inventory_sync_error_guidance(code, "", capture_source="unknown")
        assert "登录页" not in native and "登录界面" not in native
        assert "游戏场景" in native
        assert "登录页" in packet
        assert "登录页" not in unknown
    assert "完全退出游戏" in inventory_sync_error_guidance(
        "GAME_PROCESS_NOT_FOUND", "", capture_source="native",
    )
