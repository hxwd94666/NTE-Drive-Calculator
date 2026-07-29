# 编排仓库卡片鉴定、同类装备对比和结果对话框。
"""Warehouse identification and comparison controller helpers."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.app.workers import WorkerThread
from src.features.identification.page import build_identify_result_row
from src.features.inventory.warehouse import warehouse_item_compare_category
from src.services.warehouse_identification_service import (
    WarehouseIdentificationService,
)


def show_warehouse_item_identification(
    owner: Any,
    index: QModelIndex | None,
) -> None:
    item = (
        index.data(Qt.ItemDataRole.UserRole)
        if index is not None
        else None
    )
    snapshot_id = getattr(owner, "_warehouse_snapshot_id", None)
    if not isinstance(item, dict) or not isinstance(snapshot_id, int):
        return
    active_worker = getattr(owner, "_warehouse_identification_worker", None)
    if active_worker is not None and active_worker.isRunning():
        return
    service = WarehouseIdentificationService(
        owner.app_context.account.user_database_path
    )
    worker = WorkerThread(
        target=lambda: owner._run_identify_item(
            service.load_item(snapshot_id, str(item.get("uid") or ""))
        ),
        parent=owner,
    )
    owner._warehouse_identification_worker = worker
    worker.result_ready.connect(
        lambda result, current=dict(item): show_warehouse_identification_dialog(
            owner, current, result
        )
    )
    worker.error.connect(
        lambda error: QMessageBox.warning(
            owner,
            "装备鉴定失败",
            f"未能完成角色匹配评分：\n{error}",
        )
    )
    worker.start()


def select_warehouse_compare_item(
    owner: Any,
    index: QModelIndex | None,
) -> None:
    item = (
        index.data(Qt.ItemDataRole.UserRole)
        if index is not None
        else None
    )
    snapshot_id = getattr(owner, "_warehouse_snapshot_id", None)
    if not isinstance(item, dict) or not isinstance(snapshot_id, int):
        return
    category = warehouse_item_compare_category(item)
    first = getattr(owner, "_warehouse_compare_first", None)
    if first is None:
        owner._warehouse_compare_first = {
            "item": dict(item),
            "category": category,
        }
        owner.warehouse_hint.setText(
            f"已选择左栏 [{item.get('display_name') or item.get('title')}]；"
            "请选择同类别装备作为右栏。"
        )
        owner.warehouse_hint.show()
        return
    first_item = first["item"]
    if str(first_item.get("uid")) == str(item.get("uid")):
        QMessageBox.information(
            owner, "装备对比", "请再选择另一件同类别装备进行对比。"
        )
        return
    if first["category"] != category:
        QMessageBox.warning(
            owner, "装备对比", "驱动和卡带不能互相对比；请选择同类别装备。"
        )
        return
    active_worker = getattr(owner, "_warehouse_identification_worker", None)
    if active_worker is not None and active_worker.isRunning():
        return
    owner._warehouse_compare_first = None
    service = WarehouseIdentificationService(
        owner.app_context.account.user_database_path
    )
    worker = WorkerThread(
        target=lambda: (
            owner._run_identify_item(
                service.load_item(
                    snapshot_id, str(first_item.get("uid") or "")
                )
            ),
            owner._run_identify_item(
                service.load_item(snapshot_id, str(item.get("uid") or ""))
            ),
        ),
        parent=owner,
    )
    owner._warehouse_identification_worker = worker
    worker.result_ready.connect(
        lambda result, left=dict(first_item), right=dict(
            item
        ): show_warehouse_identification_comparison(
            owner, left, right, result
        )
    )
    worker.error.connect(
        lambda error: QMessageBox.warning(
            owner,
            "装备对比失败",
            f"未能完成同类别装备鉴定对比：\n{error}",
        )
    )
    worker.start()


def show_warehouse_identification_dialog(
    owner: Any,
    item: dict[str, Any],
    result: dict[str, Any],
) -> None:
    del item
    dialog = QDialog(owner)
    dialog.setWindowTitle("装备鉴定结果")
    dialog.setFixedSize(560, 520)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 12)
    layout.setSpacing(10)
    scroll = QScrollArea(dialog)
    scroll.setWidgetResizable(True)
    scroll.setHorizontalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    content = QWidget()
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(2, 2, 2, 2)
    content_layout.setSpacing(8)
    match_group = QGroupBox("匹配角色评分")
    match_layout = QVBoxLayout(match_group)
    match_layout.setSpacing(8)
    rows = list(result.get("rows") or []) if isinstance(result, dict) else []
    if rows:
        for rank, row in enumerate(rows, start=1):
            match_layout.addWidget(
                build_identify_result_row(
                    rank,
                    row,
                    game_ui_asset_root=owner.app_context.paths.asset_dir / "game_ui",
                )
            )
    else:
        empty = QLabel("没有找到图纸可使用该装备的角色。")
        empty.setStyleSheet(themed_style("color:#8b949e"))
        match_layout.addWidget(empty)
    content_layout.addWidget(match_group)
    content_layout.addStretch()
    scroll.setWidget(content)
    layout.addWidget(scroll, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()


def show_warehouse_identification_comparison(
    owner: Any,
    left: dict[str, Any],
    right: dict[str, Any],
    results: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    dialog = QDialog(owner)
    dialog.setWindowTitle("装备鉴定对比")
    dialog.setFixedSize(1120, 620)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 12)
    columns = QHBoxLayout()
    columns.setSpacing(12)
    for title, item, result in (
        ("左栏", left, results[0]),
        ("右栏", right, results[1]),
    ):
        group = QGroupBox(
            f"{title} · {item.get('display_name') or item.get('title')}"
        )
        group_layout = QVBoxLayout(group)
        scroll = QScrollArea(group)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(2, 2, 2, 2)
        rows = (
            list(result.get("rows") or [])
            if isinstance(result, dict)
            else []
        )
        if rows:
            for rank, row in enumerate(rows, start=1):
                content_layout.addWidget(
                    build_identify_result_row(
                        rank,
                        row,
                        game_ui_asset_root=(
                            owner.app_context.paths.asset_dir / "game_ui"
                        ),
                    )
                )
        else:
            empty = QLabel("没有找到图纸可使用该装备的角色。")
            empty.setStyleSheet(themed_style("color:#8b949e"))
            content_layout.addWidget(empty)
        content_layout.addStretch()
        scroll.setWidget(content)
        group_layout.addWidget(scroll)
        columns.addWidget(group, 1)
    layout.addLayout(columns, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    dialog.exec()
