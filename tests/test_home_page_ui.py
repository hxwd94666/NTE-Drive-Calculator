# 验证工作台页面的公共界面行为。
"""Visible workbench hero contracts."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel

from src.features.home.page import build_home_page, inventory_sync_error_guidance


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


def test_workbench_hero_uses_lingke_avatar() -> None:
    QApplication.instance() or QApplication([])
    asset_dir = Path("assets").resolve()
    window = SimpleNamespace(
        app_context=SimpleNamespace(paths=SimpleNamespace(asset_dir=asset_dir)),
        _start_inventory_sync=lambda: None,
        _stop_inventory_sync=lambda: None,
        _focus_environment_configuration=lambda: None,
        _go=lambda _key: None,
    )
    page = build_home_page(window)
    avatar = page.findChild(QLabel, "homeHeroAvatar")

    assert avatar is not None
    assert avatar.pixmap() is not None
    assert not avatar.pixmap().isNull()
    page.deleteLater()
