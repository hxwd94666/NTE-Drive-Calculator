# 倒带执行选项与十二种驱动槽位选择弹窗。
"""UI-only dialogs for configuring a custom rewind run."""

from __future__ import annotations

from src.ui.common.text_catalog import plain_label, plain_tooltip, resolve_text_catalog
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QGridLayout,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.domain.rewind.rewind_shape_recommendation import RewindShapeRecommendation
from src.domain.rewind.shape_overview import MarginTotals
from src.domain.rewind.strategy_preferences import TEN_LIMIT_MAXIMUM, TEN_LIMIT_QUALITIES
from src.features.inventory.warehouse.display.cards import warehouse_shape_pixmap


@dataclass(frozen=True, slots=True)
class RewindExecutionOptions:
    """Player-selected draw qualities and custom-pool behavior."""

    qualities: tuple[str, ...] = ("gold",)
    drive_customization: str = "none"
    # 各品质最多进行的十连次数，只含正整数；未列出的品质按识别余额执行。
    ten_limits: tuple[tuple[str, int], ...] = ()


class RewindExecutionDialog(QDialog):
    """Collect multi-quality and custom-pool choices as recommendation-style tiles."""

    def __init__(self, parent=None, *, initial: RewindExecutionOptions | None = None, text_catalog=None) -> None:
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        text = self.text_catalog.render
        options = initial or RewindExecutionOptions()
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('rewind.page.init.start_rewind_button'))
        self.setMinimumWidth(560)
        root = QVBoxLayout(self)
        root.setSpacing(12)
        prerequisite = plain_label(
            resolve_text_catalog(text_catalog).render('ui.toolbox.init.prerequisite', value=f'{self._stop_hotkey_label()}')
        )
        prerequisite.setObjectName("rewindStopNotice")
        prerequisite.setWordWrap(True)
        prerequisite.setStyleSheet(themed_style(
            "background:#1f6feb33;color:#58a6ff;border:1px solid #58a6ff;"
            "border-radius:8px;padding:9px;font-weight:700"
        ))
        root.addWidget(prerequisite)

        root.addWidget(plain_label(resolve_text_catalog(text_catalog).render('ui.toolbox.init.caption_2')))
        quality_grid = QGridLayout()
        self._quality_buttons: dict[str, QPushButton] = {}
        self._ten_limits: dict[str, QSpinBox] = {}
        initial_limits = dict(options.ten_limits)
        limit_grid = QGridLayout()
        for index, (label, value, tone) in enumerate((
            ("蓝色品质", "blue", "#58a6ff"),
            ("紫色品质", "purple", "#bc8cff"),
            ("金色品质", "gold", "#f2cc60"),
        )):
            button = self._tile(label, tone, checked=value in options.qualities)
            button.setObjectName("rewindQualityTile")
            button.setProperty("rewindValue", value)
            self._quality_buttons[value] = button
            button.toggled.connect(self._sync_customization_availability)
            quality_grid.addWidget(button, 0, index)
            # 次数输入只在对应品质被选中时可编辑；0 显示为“默认”，表示按识别余额、不设上限。
            spin = QSpinBox()
            spin.setObjectName("rewindTenLimit")
            spin.setProperty("rewindValue", value)
            spin.setRange(0, TEN_LIMIT_MAXIMUM)
            spin.setValue(initial_limits.get(value, 0))
            spin.setSpecialValueText(text('rewind.execution.ten_limit.auto'))
            spin.setSuffix(text('rewind.execution.ten_limit.suffix'))
            spin.setAlignment(Qt.AlignCenter)
            # 去掉上下箭头让文字真正居中；键盘与滚轮仍可调整，说明放在悬浮提示。
            spin.setButtonSymbols(QSpinBox.NoButtons)
            # 与品质卡片同一套底色；禁用态明显变暗，避免误以为未选品质的次数也会生效。
            spin.setMinimumHeight(34)
            spin.setStyleSheet(themed_style(
                "QSpinBox{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:8px;padding:4px;}"
                "QSpinBox:focus{border-color:#58a6ff;}"
                "QSpinBox:disabled{background:#161b22;color:#484f58;border-color:#21262d;}"))
            spin.setAccessibleName(text('rewind.execution.ten_limit.accessible', quality=label))
            spin.setToolTip(plain_tooltip(text('rewind.execution.ten_limit.hint')))
            spin.setEnabled(button.isChecked())
            button.toggled.connect(spin.setEnabled)
            self._ten_limits[value] = spin
            limit_grid.addWidget(spin, 0, index)
        root.addLayout(quality_grid)
        root.addWidget(plain_label(text('rewind.execution.ten_limit.title')))
        root.addLayout(limit_grid)

        customization_header = QHBoxLayout()
        customization_header.addWidget(plain_label(resolve_text_catalog(text_catalog).render('ui.toolbox.init.caption_3')))
        customization_help = QPushButton("?")
        customization_help.setObjectName("rewindCustomizationHelp")
        customization_help.setToolTip(plain_tooltip(resolve_text_catalog(text_catalog).render('ui.toolbox.init.customization_help')))
        customization_help.setStyleSheet(themed_style(
            "QPushButton{background:transparent;border:1px solid #30363d;border-radius:10px;"
            "color:#8b949e;font-size:11px;font-weight:700;padding:2px 7px;"
            "min-width:20px;max-width:20px;min-height:20px;max-height:20px;}"
            "QPushButton:hover{background:#1f6feb33;color:#58a6ff;border-color:#58a6ff;}"
        ))
        customization_help.clicked.connect(self._show_customization_help)
        customization_header.addWidget(customization_help)
        customization_header.addStretch(1)
        root.addLayout(customization_header)
        customization_grid = QGridLayout()
        self._customization = QButtonGroup(self)
        self._customization_buttons: dict[str, QPushButton] = {}
        for index, (label, value, tone) in enumerate((
            ("否", "none", "#8b949e"),
            ("是且不做更改", "enabled", "#58a6ff"),
            ("是且应用方案", "apply_plan", "#3fb950"),
        )):
            button = self._tile(label, tone, checked=value == options.drive_customization)
            button.setObjectName("rewindCustomizationTile")
            button.setProperty("rewindValue", value)
            self._customization.addButton(button)
            self._customization_buttons[value] = button
            customization_grid.addWidget(button, 0, index)
        root.addLayout(customization_grid)

        buttons = QDialogButtonBox()
        start = buttons.addButton(resolve_text_catalog(text_catalog).render('ui.toolbox.init.start'), QDialogButtonBox.AcceptRole)
        cancel = buttons.addButton(resolve_text_catalog(text_catalog).render('common.cancel'), QDialogButtonBox.RejectRole)
        start.setObjectName("rewindStart")
        cancel.setObjectName("rewindCancel")
        start.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        root.addWidget(buttons)
        self._sync_customization_availability()

    def _stop_hotkey_label(self) -> str:
        parent = self.parentWidget()
        while parent is not None:
            manager = getattr(parent, "global_hotkey_manager", None)
            configuration = getattr(manager, "configuration", None)
            if configuration is not None:
                return str(getattr(configuration, "stop", "全局停止键"))
            parent = parent.parentWidget()
        return "F12"

    @staticmethod
    def _tile(label: str, tone: str, *, checked: bool) -> QPushButton:
        """Match the compact strategy and grade buttons in the recommendation panel."""

        button = QPushButton(label)
        button.setCheckable(True)
        button.setChecked(checked)
        button.setMinimumHeight(42)
        button.setStyleSheet(themed_style(
            "QPushButton{background:#21262d;color:#8b949e;border:1px solid #30363d;border-radius:8px;"
            "font-size:13px;font-weight:700;padding:7px 13px;}"
            f"QPushButton:hover{{border-color:{tone};background:#30363d;color:#c9d1d9;}}"
            f"QPushButton:checked{{border:1px solid {tone};background:#1f6feb33;color:{tone};}}"
            "QPushButton:disabled{background:#161b22;color:#484f58;border-color:#21262d;}"
        ))
        return button

    def options(self) -> RewindExecutionOptions:
        qualities = tuple(value for value, button in self._quality_buttons.items() if button.isChecked())
        if not qualities:
            qualities = ("gold",)
        selected = self._customization.checkedButton()
        customization = str(selected.property("rewindValue")) if selected is not None else "none"
        # 只提交已选品质的正整数上限；未选品质的输入保持禁用、不参与本次执行。
        limits = tuple((quality, self._ten_limits[quality].value()) for quality in TEN_LIMIT_QUALITIES
                       if quality in qualities and self._ten_limits[quality].value() > 0)
        return RewindExecutionOptions(qualities=qualities, drive_customization=customization, ten_limits=limits)

    def _sync_customization_availability(self, _checked: bool | None = None) -> None:
        custom_quality_selected = any(
            self._quality_buttons[quality].isChecked() for quality in ("purple", "gold")
        )
        for value in ("enabled", "apply_plan"):
            self._customization_buttons[value].setEnabled(custom_quality_selected)
        if not custom_quality_selected:
            self._customization_buttons["none"].setChecked(True)

    def _show_customization_help(self) -> None:
        QMessageBox.information(
            self,
            resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.show_customization_help.qmessagebox'),
            plain_tooltip(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.toolbox.show_customization_help.qmessagebox_2')),
        )


def score_amount(value: float, *, ceiling: bool = False) -> str:
    """分数展示到一位小数并去掉多余的零；缺口向上取整，避免把仍差一点的形状显示成达标。"""
    rounding = ROUND_CEILING if ceiling else ROUND_HALF_UP
    text = f"{Decimal(str(abs(value))).quantize(Decimal('0.1'), rounding=rounding):.1f}"
    return text[:-2] if text.endswith('.0') else text


def _score_text(text, totals: MarginTotals | None) -> str:
    """有驱动低于目标时显示缺分合计（与八槽卡片“缺分”和推荐算法同口径，不被其他件富余抵消）；
    全部达标才显示高于目标的多分合计；未被所选方案使用的形状单独标出。"""
    if totals is None:
        return text('rewind.picker.unused')
    if totals.shortfall_count:
        return text('rewind.picker.short', score=score_amount(totals.shortfall, ceiling=True))
    if totals.surplus > 0:
        return text('rewind.picker.over', score=score_amount(totals.surplus))
    return text('rewind.picker.even')


def _score_color(totals: MarginTotals | None) -> str:
    if totals is None:
        return "#8b949e"
    return "#d29922" if totals.shortfall_count else "#58a6ff" if totals.surplus > 0 else "#8b949e"


class RewindShapeReplacementDialog(QDialog):
    """Choose one of the 12 drive shapes for the clicked candidate slot."""

    def __init__(
        self,
        parent,
        *,
        candidates: tuple[RewindShapeRecommendation, ...],
        current_shape_id: str | None = None,
        text_catalog=None,
        scores: Mapping[str, MarginTotals] | None = None,
        selected_shape_ids=(),
    ) -> None:
        """scores 为最近一次有效生成推荐的按形状余量；None 表示没有可用结果，只显示库存。

        selected_shape_ids 为当前八槽已选择的形状，以黄框标出；与是否有分值无关。
        """
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(text_catalog)
        text = self.text_catalog.render
        self.setWindowTitle(resolve_text_catalog(text_catalog).render('ui.toolbox.init.self_2'))
        self.setFixedWidth(760)
        self._candidates = candidates
        self._selected_shape_id = current_shape_id or (candidates[0].shape.shape_id if candidates else "")
        selected = frozenset(selected_shape_ids)

        root = QVBoxLayout(self)
        root.addWidget(plain_label(resolve_text_catalog(text_catalog).render('ui.toolbox.init.caption_4')))
        hint = plain_label(text('rewind.picker.hint'))
        hint.setObjectName("rewindShapeScoresHint")
        hint.setWordWrap(True)
        hint.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
        root.addWidget(hint)
        grid = QGridLayout()
        grid.setContentsMargins(0, 8, 0, 8)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        grid.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        by_size = {
            size: [candidate for candidate in candidates if candidate.shape.cell_count == size]
            for size in (2, 3, 4)
        }
        for row, size in enumerate((2, 3, 4)):
            label = plain_label(resolve_text_catalog(text_catalog).render('ui.toolbox.init.label', size=f'{size}'))
            label.setObjectName("rewindShapeGroupLabel")
            label.setFixedWidth(88)
            label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            label.setStyleSheet(themed_style("color:#8b949e;font-weight:800;padding:3px 6px"))
            grid.addWidget(label, row, 0)
            for column, candidate in enumerate(by_size[size], start=1):
                option_box = QWidget()
                option_layout = QVBoxLayout(option_box)
                option_layout.setContentsMargins(0, 0, 0, 0)
                option_layout.setSpacing(2)
                button = QToolButton()
                button.setObjectName("rewindShapeReplacementOption")
                button.setCheckable(True)
                button.setChecked(candidate.shape.shape_id == self._selected_shape_id)
                button.setText("")
                button.setToolTip(
                    plain_tooltip(resolve_text_catalog(text_catalog).render('ui.toolbox.init.button', cell_count=f'{candidate.shape.cell_count}', owned_count=f'{candidate.owned_count}'))
                )
                button.setToolButtonStyle(Qt.ToolButtonIconOnly)
                pixmap = warehouse_shape_pixmap(candidate.shape.shape_id, "Gold", text_catalog=text_catalog)
                if not pixmap.isNull():
                    button.setIcon(QIcon(pixmap))
                    button.setIconSize(QSize(42, 42))
                button.setProperty("shapeId", candidate.shape.shape_id)
                button.setFixedSize(96, 58)
                in_slots = candidate.shape.shape_id in selected
                button.setProperty("inSlots", in_slots)
                # 黄框 = 当前八槽已选择；蓝框 = 本次点击的槽位将填入的形状，二者同时成立时以蓝框为准。
                button.setStyleSheet(themed_style(
                    "QToolButton{background:#161b22;color:#c9d1d9;border-radius:8px;"
                    + ("border:2px solid #d29922;}" if in_slots else "border:1px solid #30363d;}")
                    + "QToolButton:hover{border-color:#58a6ff;background:#1f6feb33;}"
                    "QToolButton:checked{border:2px solid #58a6ff;background:#0d1f35;color:#f0f6fc;}"
                ))
                button.clicked.connect(
                    lambda _checked=False, shape_id=candidate.shape.shape_id: self._set_selected(shape_id)
                )
                option_layout.addWidget(button, 0, Qt.AlignHCenter)
                stock_label = plain_label(resolve_text_catalog(text_catalog).render('ui.toolbox.init.stock_label', owned_count=f'{candidate.owned_count}'))
                stock_label.setObjectName("rewindShapeReplacementStock")
                stock_label.setProperty("shapeId", candidate.shape.shape_id)
                stock_label.setAlignment(Qt.AlignCenter)
                stock_label.setWordWrap(False)
                stock_label.setStyleSheet(themed_style("color:#8b949e;font-size:10px"))
                option_layout.addWidget(stock_label)
                if scores is not None:
                    totals = scores.get(candidate.shape.shape_id)
                    score_label = plain_label(_score_text(text, totals))
                    score_label.setObjectName("rewindShapeReplacementScore")
                    score_label.setProperty("shapeId", candidate.shape.shape_id)
                    score_label.setAlignment(Qt.AlignCenter)
                    score_label.setWordWrap(False)
                    # 缺分用黄色、多分用蓝色，持平与未使用保持次要灰色。
                    score_label.setStyleSheet(themed_style(f"color:{_score_color(totals)};font-size:10px"))
                    option_layout.addWidget(score_label)
                # 各格文字顶对齐，三行分值与两行“未使用”不因垂直居中而错位。
                option_layout.addStretch(1)
                grid.addWidget(option_box, row, column)
        root.addLayout(grid)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def selected(self) -> RewindShapeRecommendation | None:
        return next((candidate for candidate in self._candidates if candidate.shape.shape_id == self._selected_shape_id), None)

    def _set_selected(self, shape_id: str) -> None:
        self._selected_shape_id = shape_id
        for button in self.findChildren(QToolButton, "rewindShapeReplacementOption"):
            button.setChecked(str(button.property("shapeId")) == shape_id)
