# 角色与词条配装共用的卡片式装备替换比较窗口。
"""复用仓库卡片比较展示副本，只有显式确认才提交替换。"""

from __future__ import annotations

from src.domain.localization.catalog import localized_text
from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import TextActionButton, plain_label, plain_tooltip, resolve_text_catalog
from copy import deepcopy
from dataclasses import dataclass
from html import escape
from time import perf_counter
from typing import Any, Callable, Mapping, Sequence

from PySide6.QtCore import QObject, QSize, Qt, QTimer
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QDialog,
    QCheckBox,
    QBoxLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLayout,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QTextBrowser,
    QToolButton,
    QWidget,
)

from src.app.theme import theme_color, themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.inventory.warehouse.display.result_card import WarehouseResultCard


REPLACEMENT_CANDIDATE_COLUMNS = 4
REPLACEMENT_CANDIDATE_GAP = 10
# Four warehouse cards, three inter-card gaps, one vertical scrollbar and
# enough root/group chrome for the fourth card to remain fully visible.
REPLACEMENT_DIALOG_CHROME_WIDTH = 122
REPLACEMENT_DIALOG_WIDTH = (
    WarehouseResultCard.CARD_SIZE.width() * REPLACEMENT_CANDIDATE_COLUMNS
    + REPLACEMENT_CANDIDATE_GAP * (REPLACEMENT_CANDIDATE_COLUMNS - 1)
    + REPLACEMENT_DIALOG_CHROME_WIDTH
)
REPLACEMENT_DIALOG_HEIGHT = 920


class ReplacementCandidateLoader(QObject):
    """首批最多三十件，展开只消费已冻结池；后续卡片在有界 Qt 轮次构建。"""

    def __init__(self, dialog, layout, candidates, append_candidate, *, is_current=lambda: True,
                 title: str | None = None, text_catalog=None):
        super().__init__(dialog)
        self.text_catalog = resolve_text_catalog(text_catalog)
        title = (title if title is not None else localized_text(
            self.text_catalog, "ui.equipment_replacement.loader.default_title", "候选装备",
        ))
        self._dialog, self._candidates = dialog, tuple(candidates)
        self._append, self._is_current, self._title = append_candidate, is_current, title
        self._loaded, self._limit = 0, min(30, len(self._candidates))
        self._closed, self._paused = False, False
        header = QHBoxLayout()
        self.count_label = plain_label(resolve_text_catalog(text_catalog).render('ui.equipment.init.count_label', title=f'{title}', limit=f'{self._limit}'))
        self.count_label.setObjectName("equipmentReplacementCandidateCount")
        header.addWidget(self.count_label)
        self.all_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.equipment.init.all_button'))
        self.all_button.setObjectName("equipmentReplacementAllCandidates")
        self.all_button.setToolTip(plain_tooltip(resolve_text_catalog(text_catalog).render('ui.equipment.init.all_button_2')))
        self.all_button.setEnabled(self._limit < len(self._candidates))
        self.all_button.clicked.connect(self.show_all)
        header.addWidget(self.all_button)
        self.progress = plain_label()
        header.addWidget(self.progress)
        header.addStretch()
        layout.addLayout(header)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._batch)
        dialog.finished.connect(self.close)
        # 首批保持原来可立即比较的三十件；不在“全部”按钮点击前转换剩余 DTO 或构卡。
        while self._loaded < self._limit:
            self._append(self._loaded, self._candidates[self._loaded])
            self._loaded += 1
        self.progress.hide()

    def show_all(self):
        if self._closed or self._paused or self._limit == len(self._candidates):
            return
        if not self._is_current():
            self._dialog.reject()
            return
        self._limit = len(self._candidates)
        self.count_label.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment.show_all.count_label', title=f'{self._title}', limit=f'{self._limit}'))
        self.all_button.setEnabled(False)
        self._schedule()

    def _schedule(self):
        if not self._closed and not self._paused and self._loaded < self._limit:
            self.progress.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment.schedule.progress', loaded=f'{self._loaded}', limit=f'{self._limit}'))
            self.progress.show()
            self._timer.start(0)
        elif self._loaded == self._limit:
            self.progress.hide()

    def _batch(self):
        if self._closed or self._paused:
            return
        if not isValid(self._dialog) or not self._dialog.isVisible():
            self.close()
            return
        if not self._is_current():
            self._dialog.reject()
            return
        started, count = perf_counter(), 0
        try:
            while self._loaded < self._limit and count < 4:
                # 每次追加前检查代次/取消；慢机器上单轮最多四卡且十二毫秒后交还事件循环。
                if self._closed or not self._is_current():
                    self._dialog.reject()
                    return
                self._append(self._loaded, self._candidates[self._loaded])
                self._loaded += 1
                count += 1
                if perf_counter()-started >= .012:
                    break
        except Exception as exc:
            self._timer.stop()
            self.progress.setText(resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment.batch.progress'))
            QMessageBox.warning(self._dialog, resolve_text_catalog(getattr(self, 'text_catalog', None)).render('ui.equipment.batch.qmessagebox'), ui_enum_text(getattr(self, 'text_catalog', None), str(exc)))
            return
        self._schedule()

    def set_paused(self, paused):
        self._paused = bool(paused)
        self._timer.stop()
        self.all_button.setEnabled(not self._closed and not self._paused and self._limit < len(self._candidates))
        if not self._paused:
            self._schedule()

    def close(self, *_args):
        self._closed = True
        self._timer.stop()
        self._append = None


@dataclass(frozen=True, slots=True)
class EquipmentReplacementCard:
    key: str
    item_view: Mapping[str, Any]
    score: float | None
    grade: str | None
    direct_damage_score: float | None
    payload: Any
    note: str = ""
    score_ratio: float | None = None
    stat_differences: tuple[tuple[str, float], ...] = ()


def review_comparison_html(current, choice, *, text_catalog=None):
    """用分组、箭头和正负数共同表达差异；未知得分率不补零。"""
    def rate(value):
        return escape(resolve_text_catalog(text_catalog).render('ui.review.unrated')) if value is None else f'{value:.1%}'
    parts = [f"{escape(resolve_text_catalog(text_catalog).render('ui.review.score_rate'))}<br><b>{rate(current.score_ratio)} → {rate(choice.score_ratio)}</b>"]
    for positive, label, color in ((True, escape(resolve_text_catalog(text_catalog).render('ui.review.increase')), '#2ea043'), (False, escape(resolve_text_catalog(text_catalog).render('ui.review.decrease')), '#f85149')):
        rows = [(key, delta) for key, delta in choice.stat_differences if (delta > 0 if positive else delta < 0)]
        if not rows:
            continue
        shade = theme_color(color)
        parts.append(f'<p style="color:{shade}"><b>{label}</b></p><table width="100%" cellspacing="3">')
        for key, delta in rows:
            value = f'{delta:+g}' + ('%' if key.endswith('%') else '')
            parts.append(f'<tr><td>{escape(key.removesuffix("%"))}</td>'
                         f'<td align="right" style="color:{shade}"><b>{value}</b></td></tr>')
        parts.append('</table>')
    if not choice.stat_differences:
        parts.append('<p>' + escape(resolve_text_catalog(text_catalog).render('ui.review.unchanged')) + '</p>')
    if choice.note:
        parts.append(f'<p>{escape(choice.note)}</p>')
    return ''.join(parts)


@dataclass(frozen=True, slots=True)
class EquipmentReviewOptions:
    """只审查冻结建议；采用回调仅改变上层草稿，不执行装备替换。"""
    reasons: str
    checked: bool
    actionable: bool
    on_checked: Callable[[bool], None]


def _stat_key(stat: Mapping[str, Any]) -> str:
    return str(stat.get("property_id") or stat.get("label") or "")


def _stat_numeric_value(stat: Mapping[str, Any] | None) -> float:
    value = (stat or {}).get("value") or 0.0
    if isinstance(value, str):
        value = value.replace("+", "").replace("%", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def comparison_item_views(
    current: Mapping[str, Any],
    selected: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """在深拷贝的比较副本中标记词条增减；优先用 property_id 对齐，不回写输入。"""

    old_view = deepcopy(dict(current))
    new_view = deepcopy(dict(selected))

    def stats(view: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            stat
            for stat in [
                *(view.get("main_stats") or ()),
                *(view.get("sub_stats") or ()),
            ]
            if isinstance(stat, dict) and _stat_key(stat)
        ]

    old_stats = stats(old_view)
    new_stats = stats(new_view)
    old_by_key = {_stat_key(stat): stat for stat in old_stats}
    new_by_key = {_stat_key(stat): stat for stat in new_stats}
    for key in set(old_by_key) | set(new_by_key):
        old_stat = old_by_key.get(key)
        new_stat = new_by_key.get(key)
        old_value = _stat_numeric_value(old_stat)
        new_value = _stat_numeric_value(new_stat)
        if abs(new_value - old_value) < 0.0001:
            continue
        if new_value > old_value:
            if old_stat is not None:
                old_stat["comparison_background"] = "#f85149"
            if new_stat is not None:
                new_stat["comparison_background"] = "#2ea043"
        else:
            if old_stat is not None:
                old_stat["comparison_background"] = "#2ea043"
            if new_stat is not None:
                new_stat["comparison_background"] = "#f85149"
    return old_view, new_view


def show_equipment_replacement_dialog(
    parent: QWidget,
    *,
    title: str,
    role_name: str,
    summary: str,
    current: EquipmentReplacementCard,
    candidates: Sequence[Any],
    candidate_adapter: Callable[[Any], EquipmentReplacementCard] | None = None,
    on_confirm: Callable[[EquipmentReplacementCard], None] | None = None,
    submit_confirm=None, is_current: Callable[[], bool] = lambda: True,
    cancel_pending: Callable[[], None] = lambda: None,
    review: EquipmentReviewOptions | None = None,
    text_catalog=None,
) -> bool:
    """未知边际值保留占位；异步提交区分已保存事实、失败和失效展示。"""

    dialog = QDialog(parent)
    dialog.setObjectName("equipmentReplacementDialog")
    dialog.setWindowTitle(title)
    fitted_size = fit_dialog_to_available_screen(
        dialog,
        QSize(REPLACEMENT_DIALOG_WIDTH, REPLACEMENT_DIALOG_HEIGHT),
    )
    root = QVBoxLayout(dialog)
    root.setSpacing(6 if review else 10)
    if review:
        root.setContentsMargins(8, 8, 8, 8)
    card_size = WarehouseResultCard.REVIEW_CARD_SIZE if review else WarehouseResultCard.CARD_SIZE

    header = plain_label(localized_text(
        text_catalog,
        "ui.equipment_replacement.header.review" if review
        else "ui.equipment_replacement.header.current",
        "比较评分角色：{role_name}" if review else "装配角色：{role_name}",
        role_name=role_name,
    )
                    + (resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.header') if review else ''))
    header.setStyleSheet(themed_style(
        "font-size:15px;font-weight:800;color:#4dd0e1;"
        "border:1px solid #4dd0e1;border-radius:7px;padding:5px 12px;"
        "background:rgba(77,208,225,0.10)"
    ))
    root.addWidget(header)
    description = plain_label(summary)
    description.setWordWrap(True)
    description.setStyleSheet(themed_style("color:#8b949e"))
    root.addWidget(description)

    comparison_group = QGroupBox("" if review else resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.comparison_group'))
    comparison_group.setObjectName("equipmentReplacementComparisonGroup")
    comparison_group.setLayoutDirection(Qt.LeftToRight)
    comparison_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
    if review:
        comparison_group.setStyleSheet(themed_style(
            'QGroupBox#equipmentReplacementComparisonGroup{margin-top:0;padding:4px;}'
        ))
    comparison_layout = QHBoxLayout(comparison_group)
    comparison_layout.setDirection(QBoxLayout.LeftToRight)
    comparison_layout.setContentsMargins(8, 4, 8, 6)
    comparison_layout.setSpacing(10)
    current_column = QVBoxLayout()
    current_column.setSpacing(3)
    current_label = plain_label(resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.current_label') if review else resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.current_label_2'))
    current_label.setStyleSheet(themed_style("font-weight:700;color:#c9d1d9"))
    current_column.addWidget(current_label)
    current_host = QWidget(comparison_group)
    current_host.setFixedSize(card_size)
    current_host_layout = QVBoxLayout(current_host)
    current_host_layout.setContentsMargins(0, 0, 0, 0)
    current_card = WarehouseResultCard(
        current.item_view,
        score=current.score,
        grade=current.grade,
        direct_damage_score=current.direct_damage_score,
        split_metrics=True,
        ratio_only=review is not None, score_ratio=current.score_ratio,
        parent=current_host,
        text_catalog=text_catalog,
    )
    current_host_layout.addWidget(current_card)
    current_column.addWidget(current_host)
    comparison_layout.addLayout(current_column)

    comparison_layout.addStretch(1)

    comparison_text = plain_label(resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.comparison_text'))
    comparison_text.setObjectName("equipmentReplacementComparison")
    comparison_text.setAlignment(Qt.AlignCenter)
    comparison_text.setWordWrap(True)
    comparison_text.setMinimumWidth(170 if review else 260)
    comparison_text.setMaximumWidth(300)
    comparison_text.setStyleSheet(themed_style(
        "font-size:13px;color:#c9d1d9;padding:6px;"
    ))
    comparison_layout.addWidget(comparison_text, 0, Qt.AlignVCenter)

    comparison_layout.addStretch(1)

    selected_column = QVBoxLayout()
    selected_column.setSpacing(3)
    selected_label = plain_label(resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.selected_label') if review else resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.selected_label_2'))
    selected_label.setStyleSheet(themed_style("font-weight:700;color:#c9d1d9"))
    selected_column.addWidget(selected_label)
    selected_host = QWidget(comparison_group)
    selected_host.setFixedSize(card_size)
    selected_host_layout = QVBoxLayout(selected_host)
    selected_host_layout.setContentsMargins(0, 0, 0, 0)
    selected_placeholder = plain_label(resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.selected_placeholder'), selected_host)
    selected_placeholder.setAlignment(Qt.AlignCenter)
    selected_placeholder.setStyleSheet(themed_style(
        "color:#8b949e;border:1px dashed #484f58;border-radius:9px;"
    ))
    selected_host_layout.addWidget(selected_placeholder)
    selected_column.addWidget(selected_host)
    comparison_layout.addLayout(selected_column)
    comparison_scroll = QScrollArea(dialog)
    comparison_scroll.setObjectName("equipmentReplacementComparisonScroll")
    comparison_scroll.setWidgetResizable(True)
    comparison_scroll.setFrameShape(QFrame.NoFrame)
    comparison_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    comparison_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    comparison_scroll.setMinimumHeight(
        min(310, max(140, fitted_size.height() * 38 // 100))
    )
    comparison_scroll.setWidget(comparison_group)
    if review:
        # 按两张卡片的实际内容收紧上区，高度余量全部留给候选；过长差异可在上区滚动。
        comparison_group.ensurePolished()
        comparison_scroll.setMinimumHeight(min(180, fitted_size.height() // 3))
        comparison_scroll.setMaximumHeight(comparison_group.sizeHint().height() + 2)
    root.addWidget(comparison_scroll)

    if review is not None:
        reason_host = QWidget(dialog)
        reason_layout = QVBoxLayout(reason_host)
        reason_layout.setContentsMargins(0, 0, 0, 0)
        reason_layout.setSpacing(4)
        toggle_reasons = QToolButton(reason_host)
        toggle_reasons.setObjectName('equipmentReviewReasonsToggle')
        toggle_reasons.setText(resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.toggle_reasons'))
        toggle_reasons.setCheckable(True)
        toggle_reasons.setArrowType(Qt.RightArrow)
        toggle_reasons.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        toggle_reasons.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.toggle_reasons_2'))
        reason_layout.addWidget(toggle_reasons, 0, Qt.AlignLeft)
        reasons = QTextBrowser(dialog)
        reasons.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.reasons'))
        reasons.setObjectName('equipmentReviewReasons')
        reasons.setPlainText(review.reasons)
        reasons.setFixedHeight(min(96, max(48, fitted_size.height() // 8)))
        reasons.hide()
        reason_layout.addWidget(reasons)
        def show_reasons(expanded):
            reasons.setVisible(expanded)
            toggle_reasons.setText(resolve_text_catalog(text_catalog).render('ui.equipment.show_reasons.toggle_reasons') if expanded else resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.toggle_reasons'))
            toggle_reasons.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        toggle_reasons.toggled.connect(show_reasons)
        root.addWidget(reason_host)

    candidate_group = QGroupBox()
    candidate_group.setObjectName("equipmentReplacementCandidateGroup")
    candidate_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
    candidate_group.setStyleSheet(themed_style("QGroupBox#equipmentReplacementCandidateGroup{margin-top:0;padding:8px}"))
    if review:
        candidate_group.setStyleSheet(themed_style('QGroupBox#equipmentReplacementCandidateGroup{margin-top:0;padding:4px;}'))
    # The card list owns its own scrollbar, so it may yield vertical space to
    # the comparison and bottom action row on scaled notebook screens.
    candidate_group.setMinimumHeight(
        min(120, max(60, fitted_size.height() // 5))
    )
    candidate_layout = QVBoxLayout(candidate_group)
    scroll = QScrollArea(candidate_group)
    scroll.setWidgetResizable(True)
    content = QWidget()
    grid = QGridLayout(content)
    # 追加候选后同步推进内容最小高度，滚动区域不能停留在首批三十件的尺寸。
    grid.setSizeConstraint(QLayout.SetMinimumSize)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(REPLACEMENT_CANDIDATE_GAP)
    grid.setVerticalSpacing(10)
    candidate_widgets: dict[str, WarehouseResultCard] = {}
    selected: list[EquipmentReplacementCard | None] = [None]

    confirm = TextActionButton(resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.confirm'))
    confirm.setObjectName("btnAction")
    confirm.setEnabled(False)

    submitting = [False]

    def select(choice: EquipmentReplacementCard) -> None:
        if submitting[0]:
            return
        selected[0] = choice
        for key, card in candidate_widgets.items():
            card.set_selected(key == choice.key)
        current_view, selected_view = comparison_item_views(
            current.item_view,
            choice.item_view,
        )
        while selected_host_layout.count():
            widget = selected_host_layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        selected_copy = WarehouseResultCard(
            selected_view,
            score=choice.score,
            grade=choice.grade,
            direct_damage_score=choice.direct_damage_score,
            split_metrics=True,
            ratio_only=review is not None, score_ratio=choice.score_ratio,
            parent=selected_host,
            text_catalog=text_catalog,
        )
        selected_copy.setObjectName("equipmentReplacementSelectedPreview")
        selected_host_layout.addWidget(selected_copy)
        while current_host_layout.count():
            widget = current_host_layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        compared_current = WarehouseResultCard(
            current_view,
            score=current.score,
            grade=current.grade,
            direct_damage_score=current.direct_damage_score,
            split_metrics=True,
            ratio_only=review is not None, score_ratio=current.score_ratio,
            parent=current_host,
            text_catalog=text_catalog,
        )
        compared_current.setObjectName("equipmentReplacementCurrentPreview")
        current_host_layout.addWidget(compared_current)
        current_direct_text = (
            "--"
            if current.direct_damage_score is None
            else f"{float(current.direct_damage_score):.2f}%"
        )
        selected_direct_text = (
            "--"
            if choice.direct_damage_score is None
            else f"{float(choice.direct_damage_score):.2f}%"
        )
        comparison_text.setText(
            resolve_text_catalog(text_catalog).render('ui.equipment.select.comparison_text', value=f"{choice.item_view.get('display_name') or choice.key}", current_direct_text=f'{current_direct_text}', selected_direct_text=f'{selected_direct_text}')
            + (f"\n{choice.note}" if choice.note else "")
        )
        if review is not None:
            comparison_text.setTextFormat(Qt.RichText)
            comparison_text.setText(review_comparison_html(current, choice, text_catalog=text_catalog))
        confirm.setEnabled(review is None)

    candidate_columns = max(
        1,
        min(
            REPLACEMENT_CANDIDATE_COLUMNS,
            (
                fitted_size.width()
                - REPLACEMENT_DIALOG_CHROME_WIDTH
                + REPLACEMENT_CANDIDATE_GAP
            )
            // (WarehouseResultCard.CARD_SIZE.width() + REPLACEMENT_CANDIDATE_GAP),
        ),
    )
    def append_candidate(index, raw_choice):
        choice = candidate_adapter(raw_choice) if candidate_adapter is not None else raw_choice
        card = WarehouseResultCard(
            choice.item_view,
            score=choice.score,
            grade=choice.grade,
            direct_damage_score=choice.direct_damage_score,
            split_metrics=True,
            ratio_only=review is not None, score_ratio=choice.score_ratio,
            replacement_callback=lambda value=choice: select(value),
            parent=content,
            text_catalog=text_catalog,
        )
        card.setToolTip(plain_tooltip(resolve_text_catalog(text_catalog).render('ui.equipment.append_candidate.card') + card.toolTip()))
        candidate_widgets[choice.key] = card
        grid.addWidget(
            card,
            index // candidate_columns,
            index % candidate_columns,
            Qt.AlignLeft | Qt.AlignTop,
        )
    loader = ReplacementCandidateLoader(dialog, candidate_layout, candidates, append_candidate, is_current=is_current, text_catalog=text_catalog)
    grid.setColumnStretch(candidate_columns, 1)
    scroll.setWidget(content)
    candidate_layout.addWidget(scroll)
    root.addWidget(candidate_group, 1)

    actions = QHBoxLayout()
    if review is not None:
        adopt = QCheckBox(resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.adopt'))
        adopt.setObjectName('equipmentReviewAdopt')
        adopt.setChecked(review.checked)
        adopt.setEnabled(review.actionable)
        adopt.toggled.connect(review.on_checked)
        actions.addWidget(adopt)
        confirm.hide()
        if not candidates:
            comparison_text.setText(resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.comparison_text_2'))
    actions.addStretch()
    cancel = TextActionButton(resolve_text_catalog(text_catalog).render('common.close') if review else resolve_text_catalog(text_catalog).render('common.cancel'))
    cancel.clicked.connect(dialog.reject)
    actions.addWidget(cancel)
    actions.addWidget(confirm)
    root.addLayout(actions)

    def active() -> bool:
        return isValid(dialog) and dialog.isVisible() and is_current()

    def committed(receipt) -> None:
        if not active():
            return  # 数据库事实已提交；离页/取消只撤销这次界面投递。
        diagnostic = receipt.get("postcommit_diagnostic") if isinstance(receipt, Mapping) else None
        if diagnostic:
            QMessageBox.information(dialog, resolve_text_catalog(text_catalog).render('rewind.execution.save_plan.save_plan_button'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.equipment.committed.qmessagebox', diagnostic=f'{diagnostic}')))
        dialog.accept()

    def failed(error) -> None:
        if not active():
            return
        submitting[0] = False
        loader.set_paused(False)
        confirm.setEnabled(True)
        confirm.setText(resolve_text_catalog(text_catalog).render('ui.equipment.show_equipment_replacement_dialog.confirm'))
        QMessageBox.warning(dialog, resolve_text_catalog(text_catalog).render('ui.inventory.apply_replacement.qmessagebox_2'), ui_enum_text(text_catalog, str(error)))

    def application_failed(error) -> None:
        if active():
            QMessageBox.information(dialog, resolve_text_catalog(text_catalog).render('ui.equipment.application_failed.qmessagebox'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.equipment.application_failed.qmessagebox_2', error=f'{error}')))
            dialog.reject()

    def commit() -> None:
        if review is not None:
            return
        choice = selected[0]
        if choice is None or submitting[0] or not active():
            return
        if submit_confirm is not None:
            submitting[0] = True
            loader.set_paused(True)
            confirm.setEnabled(False)
            confirm.setText(resolve_text_catalog(text_catalog).render('matching.saving'))
            try:
                if not submit_confirm(choice, committed, failed, application_failed):
                    failed(localized_text(
                        text_catalog,
                        "ui.equipment_replacement.submit.busy",
                        "已有替换方案正在保存，请等待完成。",
                    ))
            except Exception as exc:
                failed(str(exc))
            return
        try:
            if on_confirm is not None:
                on_confirm(choice)
        except Exception as exc:
            QMessageBox.warning(dialog, resolve_text_catalog(text_catalog).render('ui.inventory.apply_replacement.qmessagebox_2'), ui_enum_text(text_catalog, str(exc)))
            return
        dialog.accept()

    monitor = QTimer(dialog)
    monitor.setInterval(25)
    def watch():
        if not is_current():
            cancel_pending()
            dialog.reject()
    monitor.timeout.connect(watch)
    dialog.rejected.connect(cancel_pending)
    confirm.clicked.connect(commit)
    fit_dialog_to_available_screen(dialog, fitted_size)
    monitor.start()
    try:
        return dialog.exec() == QDialog.Accepted
    finally:
        monitor.stop()
        loader.close()
        dialog.deleteLater()
