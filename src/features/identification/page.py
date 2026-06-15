# 构建单件识别页面控件。
"""Identify page UI builder and image preview helpers."""

from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.models.equipment import Tape
from src.ui.widgets import SearchableComboBox

GRADE_COLORS = {"ACE": "#ffa726", "SSS": "#ffa726", "SS": "#f0883e", "S": "#f0883e", "A": "#7ec8e3", "B": "#5b9bd5", "C": "#4a7fb5", "D": "#3d5a80"}
GRADE_BGS = {"ACE": "#ffa72630", "SSS": "#ffa72620", "SS": "#f0883e18", "S": "#f0883e18", "A": "#7ec8e318", "B": "#5b9bd515", "C": "#4a7fb512", "D": "#3d5a8010"}


def build_identify_page(window, text_edit_cls):
    page = QWidget()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(12)

    input_card = window._card("快速鉴定")
    type_row = QHBoxLayout()
    type_row.setSpacing(12)
    type_row.addWidget(QLabel("装备类型"))
    window.ident_type_group = QButtonGroup(window)
    window.ident_drive_rb = QRadioButton("驱动块")
    window.ident_tape_rb = QRadioButton("卡带")
    window.ident_drive_rb.setChecked(True)
    window.ident_type_group.addButton(window.ident_drive_rb, 0)
    window.ident_type_group.addButton(window.ident_tape_rb, 1)
    window.ident_type_group.buttonToggled.connect(lambda *_: window._on_identify_type_changed())
    type_row.addWidget(window.ident_drive_rb)
    type_row.addWidget(window.ident_tape_rb)
    type_row.addSpacing(18)
    type_row.addWidget(QLabel("品质"))
    window.ident_quality_combo = QComboBox()
    for label, value in [("金色", "Gold"), ("紫色", "Purple"), ("蓝色", "Blue")]:
        window.ident_quality_combo.addItem(label, value)
    type_row.addWidget(window.ident_quality_combo)
    type_row.addStretch()
    input_card.layout().addLayout(type_row)

    window.ident_shape_row = QWidget()
    shape_row = QHBoxLayout(window.ident_shape_row)
    shape_row.setContentsMargins(0, 0, 0, 0)
    shape_row.addWidget(QLabel("驱动形状"))
    window.ident_shape_combo = SearchableComboBox()
    shape_row.addWidget(window.ident_shape_combo, 1)
    input_card.layout().addWidget(window.ident_shape_row)

    window.ident_tape_row = QWidget()
    tape_row = QHBoxLayout(window.ident_tape_row)
    tape_row.setContentsMargins(0, 0, 0, 0)
    tape_row.setSpacing(8)
    tape_row.addWidget(QLabel("卡带套装"))
    window.ident_set_combo = SearchableComboBox()
    tape_row.addWidget(window.ident_set_combo, 1)
    tape_row.addWidget(QLabel("主词条"))
    window.ident_main_combo = SearchableComboBox()
    tape_row.addWidget(window.ident_main_combo, 1)
    input_card.layout().addWidget(window.ident_tape_row)

    path_row = QHBoxLayout()
    path_row.setSpacing(8)
    window.ident_path_edit = QLineEdit()
    window.ident_path_edit.setPlaceholderText("图片路径；多个图片可用分号分隔")
    window.ident_path_edit.textChanged.connect(window._refresh_identify_previews)
    path_row.addWidget(window.ident_path_edit, 1)
    choose_btn = QPushButton("选择图片")
    choose_btn.clicked.connect(window._identify_choose_file)
    path_row.addWidget(choose_btn)
    paste_btn = QPushButton("粘贴")
    paste_btn.clicked.connect(window._identify_from_clipboard)
    path_row.addWidget(paste_btn)
    capture_btn = QPushButton("截图鉴定")
    capture_btn.clicked.connect(window._start_identify_capture_mode)
    path_row.addWidget(capture_btn)
    window.ident_parse_btn = QPushButton("解析图片")
    window.ident_parse_btn.setObjectName("btnPrimary")
    window.ident_parse_btn.clicked.connect(window._identify_from_image_path)
    window.ident_parse_btn.setVisible(False)
    path_row.addWidget(window.ident_parse_btn)
    input_card.layout().addLayout(path_row)

    window.ident_preview_scroll = QScrollArea()
    window.ident_preview_scroll.setWidgetResizable(True)
    window.ident_preview_scroll.setFixedHeight(106)
    window.ident_preview_widget = QWidget()
    window.ident_preview_layout = QHBoxLayout(window.ident_preview_widget)
    window.ident_preview_layout.setContentsMargins(4, 4, 4, 4)
    window.ident_preview_layout.setSpacing(8)
    window.ident_preview_scroll.setWidget(window.ident_preview_widget)
    window.ident_preview_scroll.setVisible(False)
    input_card.layout().addWidget(window.ident_preview_scroll)

    window.ident_manual_text = text_edit_cls()
    window.ident_manual_text.setAcceptDrops(False)
    window.ident_manual_text.setPlaceholderText("手动输入副词条，每行一条，例如：暴击率 1.0%")
    window.ident_manual_text.setFixedHeight(108)
    input_card.layout().addWidget(window.ident_manual_text)

    button_row = QHBoxLayout()
    button_row.addStretch()
    clear_btn = QPushButton("清空")
    clear_btn.clicked.connect(window._clear_identify_input)
    button_row.addWidget(clear_btn)
    window.ident_manual_btn = QPushButton("开始鉴定")
    window.ident_manual_btn.setObjectName("btnPrimary")
    window.ident_manual_btn.clicked.connect(window._identify_start)
    button_row.addWidget(window.ident_manual_btn)
    input_card.layout().addLayout(button_row)
    layout.addWidget(input_card)

    result_card = window._card("鉴定结果")
    window.ident_summary = QLabel("等待输入装备数据")
    window.ident_summary.setStyleSheet("color:#8b949e")
    result_card.layout().addWidget(window.ident_summary)
    window.ident_result_widget = QWidget()
    window.ident_result_layout = QVBoxLayout(window.ident_result_widget)
    window.ident_result_layout.setContentsMargins(0, 0, 0, 0)
    window.ident_result_layout.setSpacing(8)
    result_card.layout().addWidget(window.ident_result_widget)
    layout.addWidget(result_card)
    layout.addStretch()

    window._on_identify_type_changed()
    return scroll


def parse_identify_paths(raw_text: str) -> list[Path]:
    raw = raw_text.strip().strip('"')
    if not raw:
        return []
    return [Path(os.path.expandvars(part.strip().strip('"'))) for part in re.split(r"[;\n]+", raw) if part.strip()]


def refresh_identify_previews(window, paths: list[Path], max_count: int = 12):
    if not hasattr(window, "ident_preview_layout"):
        return
    while window.ident_preview_layout.count():
        item = window.ident_preview_layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            window._delete_layout(item.layout())

    existing_paths = [path for path in paths if path.exists()]
    window.ident_preview_scroll.setVisible(bool(existing_paths))
    for path in existing_paths[:max_count]:
        frame = QFrame()
        frame.setFixedSize(98, 98)
        frame.setStyleSheet("QFrame{background:#0d1117;border:1px solid #30363d;border-radius:6px}")
        grid = QGridLayout(frame)
        grid.setContentsMargins(2, 2, 2, 2)
        grid.setSpacing(0)

        label = QLabel()
        label.setFixedSize(92, 92)
        label.setAlignment(Qt.AlignCenter)
        label.setStyleSheet("border:none;background:transparent")
        pix = QPixmap(str(path))
        if not pix.isNull():
            label.setPixmap(pix.scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
        label.setToolTip(str(path))
        label.mousePressEvent = lambda event, p=path: window._show_identify_preview_image(p)
        grid.addWidget(label, 0, 0)

        close_btn = QPushButton("×")
        close_btn.setObjectName("btnDanger")
        close_btn.setFixedSize(20, 20)
        close_btn.clicked.connect(lambda checked, p=path: window._remove_identify_preview_path(p))
        grid.addWidget(close_btn, 0, 0, Qt.AlignTop | Qt.AlignRight)
        window.ident_preview_layout.addWidget(frame)
    window.ident_preview_layout.addStretch()


def show_identify_preview_image(parent, path: Path, style_sheet: str):
    dlg = QDialog(parent)
    dlg.setWindowTitle(path.name)
    dlg.setMinimumSize(900, 650)
    dlg.setStyleSheet(style_sheet)
    layout = QVBoxLayout(dlg)
    label = QLabel()
    label.setAlignment(Qt.AlignCenter)
    pix = QPixmap(str(path))
    if not pix.isNull():
        screen = QApplication.primaryScreen().availableGeometry()
        max_size = QSize(min(1200, screen.width() - 160), min(800, screen.height() - 180))
        label.setPixmap(pix.scaled(max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    layout.addWidget(label, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)
    dlg.exec()


def render_identify_result_page(window, pages: list[dict]):
    if not pages:
        return
    index = max(0, min(getattr(window, "_identify_result_page_index", 0), len(pages) - 1))
    window._identify_result_page_index = index
    data = pages[index]
    window._set_identify_busy(False)
    window._clear_identify_results()

    item = data.get("item")
    rows = data.get("rows", [])
    item_name = "卡带" if isinstance(item, Tape) else "驱动块"
    page_text = f"（{index + 1}/{len(pages)}）" if len(pages) > 1 else ""
    window.ident_summary.setText(f"{item_name}鉴定完成{page_text}：{len(rows)} 名角色可使用")

    preview_weights = rows[0]["weights"] if rows else {}
    if isinstance(item, Tape):
        preview = window._equip_card(
            item.set_name, item.main_stats, item.sub_stats, None, item.uid, preview_weights, None, item.quality
        )
    else:
        preview = window._equip_card(
            item.shape_id, "", item.sub_stats, item.shape_id, item.uid, preview_weights, None, item.quality
        )
    window.ident_result_layout.addWidget(preview)

    if len(pages) > 1:
        nav = QHBoxLayout()
        prev_btn = QPushButton("上一页")
        next_btn = QPushButton("下一页")
        prev_btn.setEnabled(index > 0)
        next_btn.setEnabled(index < len(pages) - 1)
        prev_btn.clicked.connect(lambda: window._set_identify_result_page(index - 1))
        next_btn.clicked.connect(lambda: window._set_identify_result_page(index + 1))
        nav.addWidget(prev_btn)
        nav.addWidget(next_btn)
        nav.addStretch()
        window.ident_result_layout.addLayout(nav)

    if not rows:
        empty = QLabel("没有找到图纸可使用该装备的角色。")
        empty.setAlignment(Qt.AlignCenter)
        empty.setStyleSheet("color:#6e7681;padding:20px")
        window.ident_result_layout.addWidget(empty)
        return

    for rank, row in enumerate(rows, 1):
        window.ident_result_layout.addWidget(build_identify_result_row(rank, row))
    window.ident_result_layout.addStretch()


def build_identify_result_row(rank: int, row: dict):
    grade = row["grade"]
    grade_color = GRADE_COLORS.get(grade, "#58a6ff")
    grade_bg = GRADE_BGS.get(grade, f"{grade_color}15")
    frame = QFrame()
    frame.setStyleSheet("QFrame{background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:8px}")
    layout = QHBoxLayout(frame)
    layout.setSpacing(10)
    layout.setContentsMargins(8, 4, 8, 4)

    rank_label = QLabel(str(rank))
    rank_label.setFixedSize(28, 28)
    rank_label.setAlignment(Qt.AlignCenter)
    rank_label.setStyleSheet("background:#21262d;color:#c9d1d9;border-radius:14px;font-weight:700")
    layout.addWidget(rank_label)

    info = QVBoxLayout()
    info.setSpacing(2)
    role = QLabel(row["role"])
    role.setStyleSheet("font-size:14px;font-weight:700;color:#c9d1d9;border:none")
    meta = QLabel(f"{row['set']} · {row['match']} · 占比 {row['percent']:.1f}%")
    meta.setStyleSheet("color:#8b949e;font-size:11px;border:none")
    info.addWidget(role)
    info.addWidget(meta)
    layout.addLayout(info, 1)

    badge = QFrame()
    badge.setStyleSheet(
        f"QFrame{{background:{grade_bg};border:1px solid {grade_color};border-radius:6px;padding:4px 10px}}"
    )
    badge_layout = QVBoxLayout(badge)
    badge_layout.setContentsMargins(8, 2, 8, 2)
    badge_layout.setSpacing(0)
    score = QLabel(f"{row['score']:.1f}")
    score.setAlignment(Qt.AlignCenter)
    score.setStyleSheet(f"font-size:18px;font-weight:800;color:{grade_color};border:none")
    grade_label = QLabel(grade)
    grade_label.setAlignment(Qt.AlignCenter)
    grade_label.setStyleSheet(f"font-size:11px;font-weight:700;color:{grade_color};border:none")
    badge_layout.addWidget(score)
    badge_layout.addWidget(grade_label)
    layout.addWidget(badge)
    return frame
