# 构建单件识别页面控件。
"""Identify page UI builder and image preview helpers."""

from __future__ import annotations

import os
import re
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
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
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from functools import lru_cache

from src.models.equipment import Tape
from src.app.theme import GRADE_COLORS, theme_rgba, themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.ui.widgets import SearchableComboBox

@lru_cache(maxsize=512)
def _official_role_portrait(
    role_name: str,
    game_ui_asset_root: Path,
) -> QPixmap:
    """Resolve identification avatars from the same official asset manifest.

    Identification still receives legacy display names from the compatibility
    solver, so normalize the protagonist alias before matching static names.
    """

    name = str(role_name or "").strip().strip("「」")
    if name in {"零", "主角"}:
        character_id = 1051
    else:
        try:
            with StaticGameDataDao() as dao:
                character_id = next(
                    int(row["character_id"])
                    for row in dao.list_characters()
                    if str(row.get("name_zh") or "").strip() == name
                )
        except (StopIteration, ValueError):
            return QPixmap()
    icon_path = GameUiAssetCatalog(game_ui_asset_root).character_icon(character_id)
    return QPixmap(str(icon_path)) if icon_path is not None else QPixmap()


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
    if window.role_suit_matching is not None:
        matching = window.role_suit_matching
        button = QPushButton(matching.catalog.render("matching.title"))
        button.setObjectName("btnNew")
        button.setToolTip(matching.catalog.render("matching.scope_hint"))
        button.clicked.connect(lambda: matching.open(page))
        type_row.addWidget(button)
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
    window.ident_manual_text.setPlaceholderText("手动输入副词条名称；数值可省略。支持逗号、空格、换行混合分隔，例如：攻击力% 暴击，爆伤")
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
    window.ident_summary.setStyleSheet(themed_style("color:#8b949e"))
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
        frame.setStyleSheet(themed_style("QFrame{background:#0d1117;border:1px solid #30363d;border-radius:6px}"))
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
    dlg.setStyleSheet(style_sheet)
    layout = QVBoxLayout(dlg)
    label = QLabel()
    label.setAlignment(Qt.AlignCenter)
    pix = QPixmap(str(path))
    layout.addWidget(label, 1)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)
    # 使用 owner 所在屏幕的逻辑工作区，先限位，再为图片和关闭入口分配空间。
    target = fit_dialog_to_available_screen(dlg, QSize(900, 650))
    if not pix.isNull():
        max_size = QSize(max(1, target.width() - 48), max(1, target.height() - 100))
        label.setPixmap(pix.scaled(max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
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
    if window.role_suit_matching is not None and data.get("matching_status") == "incomplete":
        window.ident_summary.setText(window.role_suit_matching.catalog.render("matching.incomplete"))

    preview_weights = rows[0]["weights"] if rows else {}
    preview_main_weights = rows[0].get("main_weights") if rows else None
    if isinstance(item, Tape):
        preview = window._equip_card(
            item.set_name, item.main_stats, item.sub_stats, None, item.uid, preview_weights, None, item.quality,
            main_weights=preview_main_weights, card_variant="result",
        )
    else:
        preview = window._equip_card(
            item.shape_id, "", item.sub_stats, item.shape_id, item.uid, preview_weights, None, item.quality,
            card_variant="result",
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
        matching = window.role_suit_matching
        empty = QLabel(matching.catalog.render("matching.incomplete" if data.get("matching_status") == "incomplete"
                       else "matching.unmatched") if matching is not None else "没有找到图纸可使用该装备的角色。")
        empty.setWordWrap(True)
        empty.setAlignment(Qt.AlignCenter)
        empty.setStyleSheet(themed_style("color:#6e7681;padding:20px"))
        window.ident_result_layout.addWidget(empty)
        return

    for rank, row in enumerate(rows, 1):
        window.ident_result_layout.addWidget(
            build_identify_result_row(
                rank,
                row,
                game_ui_asset_root=window.app_context.paths.game_ui_asset_root,
                catalog=window.role_suit_matching.catalog if window.role_suit_matching is not None else None,
            )
        )
    window.ident_result_layout.addStretch()


def build_identify_result_row(
    rank: int,
    row: dict,
    *,
    game_ui_asset_root: Path,
    catalog=None,
):
    grade = row["grade"]
    grade_color = GRADE_COLORS.get(grade, "#58a6ff")
    grade_bg = theme_rgba(grade_color, 0.10)
    frame = QFrame()
    frame.setStyleSheet(themed_style("QFrame{background:#0d1117;border:1px solid #21262d;border-radius:8px;padding:8px}"))
    layout = QHBoxLayout(frame)
    layout.setSpacing(10)
    layout.setContentsMargins(8, 4, 8, 4)

    rank_label = QLabel(str(rank))
    rank_label.setFixedSize(28, 28)
    rank_label.setAlignment(Qt.AlignCenter)
    rank_label.setStyleSheet(themed_style("background:#21262d;color:#c9d1d9;border-radius:14px;font-weight:700"))
    layout.addWidget(rank_label)

    info = QVBoxLayout()
    info.setSpacing(2)
    role_header = QHBoxLayout()
    role_header.setSpacing(7)
    avatar = QLabel()
    avatar.setFixedSize(48, 48)
    avatar.setAlignment(Qt.AlignCenter)
    avatar.setStyleSheet(themed_style("background:transparent;border:none;color:#8b949e;font-size:14px;font-weight:700"))
    portrait = _official_role_portrait(
        str(row["role"]),
        game_ui_asset_root,
    )
    if portrait.isNull():
        avatar.setText(str(row["role"])[:1])
    else:
        avatar.setPixmap(portrait.scaled(avatar.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
    role_header.addWidget(avatar)
    role = QLabel(row["role"])
    role.setStyleSheet(themed_style("font-size:14px;font-weight:700;color:#c9d1d9;border:none"))
    role_header.addWidget(role)
    role_header.addStretch()
    info.addLayout(role_header)
    if catalog is not None and row.get("matched_suits"):
        # 默认保持结果行紧凑，用户展开后才显示每个套装的全部适配位置。
        toggle = QToolButton()
        toggle.setText(catalog.render("matching.result_details"))
        toggle.setCheckable(True)
        toggle.setAutoRaise(True)
        toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toggle.setArrowType(Qt.RightArrow)
        toggle.setStyleSheet(themed_style(
            "QToolButton{color:#58a6ff;background:transparent;border:1px solid transparent;padding:2px 4px}"
            "QToolButton:hover,QToolButton:checked{background:#161b22}"
            "QToolButton:focus{border:1px solid #58a6ff;border-radius:4px}"))
        details = QLabel("\n".join(catalog.render("matching.result_suit",
            suit=match["name"], source=catalog.render("matching.source.default" if match["is_default"] else "matching.source.extra"),
            positions=" / ".join(catalog.render("matching.position." + position) for position in match["positions"]))
            for match in row["matched_suits"]))
        details.setTextFormat(Qt.PlainText)
        details.setWordWrap(True)
        details.setStyleSheet(themed_style("color:#8b949e;background:transparent;border:none;padding:0"))
        details.hide()
        toggle.toggled.connect(details.setVisible)
        toggle.toggled.connect(lambda expanded: toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow))
        info.addWidget(toggle, 0, Qt.AlignLeft)
        info.addWidget(details)
    layout.addLayout(info, 1)

    badge = QFrame()
    badge.setObjectName("identificationScoreBadge")
    # 徽标只容纳两行数值，不跟随左侧展开内容拉高；样式也不扩散到子标签。
    badge.setStyleSheet(
        f"QFrame#identificationScoreBadge{{background:{grade_bg};border:1px solid {grade_color};border-radius:6px;padding:0}}"
    )
    badge_layout = QVBoxLayout(badge)
    badge_layout.setContentsMargins(12, 7, 12, 7)
    badge_layout.setSpacing(2)
    score = QLabel(f"{row['score']:.1f}")
    score.setAlignment(Qt.AlignCenter)
    score.setStyleSheet(f"font-size:18px;font-weight:800;color:{grade_color};background:transparent;border:none;padding:0")
    grade_label = QLabel(grade)
    grade_label.setAlignment(Qt.AlignCenter)
    grade_label.setStyleSheet(f"font-size:11px;font-weight:700;color:{grade_color};background:transparent;border:none;padding:0")
    badge_layout.addWidget(score)
    badge_layout.addWidget(grade_label)
    layout.addWidget(badge, 0, Qt.AlignVCenter)
    return frame
