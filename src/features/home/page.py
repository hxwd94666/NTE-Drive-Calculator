# 构建并刷新 2.0 首页工作台。
"""构建并刷新 2.0 首页工作台。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.ui.dashboard_widgets import metric_card, set_status_badge


def _section(title: str, description: str = "") -> tuple[QFrame, QVBoxLayout]:
    card = QFrame()
    card.setObjectName("card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(10)
    title_label = QLabel(title)
    title_label.setObjectName("cardTitle")
    layout.addWidget(title_label)
    if description:
        subtitle = QLabel(description)
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
        layout.addWidget(subtitle)
    return card, layout


def build_home_page(window) -> QScrollArea:
    page = QWidget()
    page.setObjectName("homePage")
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)

    root = QVBoxLayout(page)
    root.setContentsMargins(22, 18, 22, 22)
    root.setSpacing(16)

    hero = QFrame()
    hero.setObjectName("homeHero")
    hero.setStyleSheet(
        themed_style(
            "QFrame#homeHero{background:#10243f;border:1px solid #1f6feb;"
            "border-radius:12px}"
        )
    )
    hero_layout = QHBoxLayout(hero)
    hero_layout.setContentsMargins(22, 18, 22, 18)
    title_column = QVBoxLayout()
    title = QLabel("NTE Drive Calc 2.0 工作台")
    title.setStyleSheet(themed_style("color:#f0f6fc;font-size:21px;font-weight:700"))
    window.home_account_label = QLabel("正在读取账号数据…")
    window.home_account_label.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    title_column.addWidget(title)
    title_column.addWidget(window.home_account_label)
    hero_layout.addLayout(title_column)
    hero_layout.addStretch()
    window.home_sync_badge = QLabel("未启动")
    window.home_sync_badge.setAlignment(Qt.AlignCenter)
    set_status_badge(window.home_sync_badge, "未启动", "neutral")
    hero_layout.addWidget(window.home_sync_badge)
    root.addWidget(hero)

    metrics = QGridLayout()
    metrics.setHorizontalSpacing(12)
    metrics.setVerticalSpacing(12)
    definitions = (
        ("inventory", "稳定背包", "等待首次同步"),
        ("module", "驱动", "原始游戏 UID"),
        ("core", "空幕", "原始游戏 UID"),
        ("equipped", "已装备", "按当前稳定快照"),
        ("plans", "配装方案", "保存在当前账号"),
        ("characters", "角色数据", "来自随程序静态数据库"),
    )
    window.home_metric_labels = {}
    for index, (key, label, subtitle) in enumerate(definitions):
        card, value_label, subtitle_label = metric_card(label, "—", subtitle)
        window.home_metric_labels[key] = (value_label, subtitle_label)
        metrics.addWidget(card, index // 3, index % 3)
    root.addLayout(metrics)

    sync_card, sync_layout = _section(
        "背包同步",
        "请先停留在游戏登录页，再启动同步并进入游戏。稳定后仍会在后台监听后续变化。",
    )
    window.home_sync_detail = QLabel("尚未启动 nte-core")
    window.home_sync_detail.setWordWrap(True)
    sync_layout.addWidget(window.home_sync_detail)
    sync_actions = QHBoxLayout()
    window.home_start_sync_button = QPushButton("启动背包同步")
    window.home_start_sync_button.setObjectName("btnPrimary")
    window.home_start_sync_button.clicked.connect(window._start_inventory_sync)
    window.home_stop_sync_button = QPushButton("停止同步")
    window.home_stop_sync_button.clicked.connect(window._stop_inventory_sync)
    window.home_stop_sync_button.setEnabled(False)
    refresh_button = QPushButton("刷新工作台")
    refresh_button.clicked.connect(window._refresh_home)
    sync_actions.addWidget(window.home_start_sync_button)
    sync_actions.addWidget(window.home_stop_sync_button)
    sync_actions.addWidget(refresh_button)
    sync_actions.addStretch()
    sync_layout.addLayout(sync_actions)
    root.addWidget(sync_card)

    actions_card, actions_layout = _section("快捷操作")
    actions = QHBoxLayout()
    for label, page_key in (
        ("计算配装", "execute"),
        ("查看方案", "equipment"),
        ("角色数据", "my_role"),
        ("同步设置", "settings"),
    ):
        button = QPushButton(label)
        button.clicked.connect(lambda _checked=False, key=page_key: window._go(key))
        actions.addWidget(button)
    actions.addStretch()
    actions_layout.addLayout(actions)
    root.addWidget(actions_card)

    data_card, data_layout = _section("数据状态")
    window.home_static_label = QLabel("正在读取静态数据库…")
    window.home_static_label.setWordWrap(True)
    window.home_recent_change_label = QLabel("最近变化：暂无稳定快照差异")
    window.home_recent_change_label.setWordWrap(True)
    data_layout.addWidget(window.home_static_label)
    data_layout.addWidget(window.home_recent_change_label)
    root.addWidget(data_card)
    root.addStretch()
    return scroll


def refresh_home_page(window, dashboard: dict[str, Any]) -> None:
    account = dashboard["account"]
    inventory = dashboard.get("inventory")
    window.home_account_label.setText(
        f"当前账号：{account['account_name']} · 数据库仅保存该账号的稳定背包与方案"
    )

    values = {
        "inventory": int(inventory["stored_item_count"]) if inventory else 0,
        "module": int(inventory["module_count"]) if inventory else 0,
        "core": int(inventory["core_count"]) if inventory else 0,
        "equipped": int(inventory["equipped_count"]) if inventory else 0,
        "plans": int(dashboard["loadout_plan_count"]),
        "characters": int(dashboard["static"]["counts"]["character"]),
    }
    for key, value in values.items():
        window.home_metric_labels[key][0].setText(str(value))

    inventory_subtitle = window.home_metric_labels["inventory"][1]
    if inventory:
        inventory_subtitle.setText(
            f"快照 #{inventory['snapshot_id']} · {inventory['captured_at_utc']}"
        )
    else:
        inventory_subtitle.setText("等待首次同步")

    static = dashboard["static"]
    dataset = static["dataset"]
    counts = static["counts"]
    version = dataset.get("game_version") or "版本待开发者确认"
    window.home_static_label.setText(
        f"静态数据集：{dataset['dataset_id']} · {version} · "
        f"空幕套装 {counts['equipment_suit']} · 装备模板 {counts['equipment_item']} · "
        f"驱动形状 {counts['equipment_shape']}"
    )

    change = dashboard.get("recent_change")
    if change:
        window.home_recent_change_label.setText(
            "最近变化："
            f"新增 {change['added_count']} · 移除 {change['removed_count']} · "
            f"属性/状态变化 {change['changed_count']}"
        )
    else:
        window.home_recent_change_label.setText("最近变化：暂无两个可比较的稳定快照")
