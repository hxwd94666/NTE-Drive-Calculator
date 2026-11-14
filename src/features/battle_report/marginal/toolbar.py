# 组合战报边际分析工具栏控件。
"""Fixed navigation and role-selection toolbar for battle marginal analysis."""

from __future__ import annotations

from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import TextActionButton, plain_label, plain_tooltip, resolve_text_catalog
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton

from src.app.dialogs import show_help
from src.app.theme import themed_style
from src.ui.common.widgets import NoWheelComboBox


@dataclass(frozen=True, slots=True)
class BattleMarginalToolbar:
    widget: QFrame
    character_combo: NoWheelComboBox
    change_summary: QLabel
    use_inferred_facts: QCheckBox
    reset_button: QPushButton
    recalculate_button: QPushButton


def build_marginal_toolbar(
    *,
    back: Callable[[], None],
    role_changed: Callable[[int], None],
    inferred_toggled: Callable[[bool], None],
    reset: Callable[[], None],
    recalculate: Callable[[], None],
    axis_optimize: Callable[[], None] | None = None,
    text_catalog=None,
) -> BattleMarginalToolbar:
    help_text = (
        resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_toolbar.help_text_text')
    )
    toolbar = QFrame()
    toolbar.setObjectName("marginalStickyToolbar")
    toolbar.setStyleSheet(themed_style(
        "QFrame#marginalStickyToolbar{background:#0d1117;"
        "border-bottom:1px solid #30363d;}"
    ))
    layout = QHBoxLayout(toolbar)
    layout.setContentsMargins(22, 10, 22, 10)
    layout.setSpacing(10)
    back_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_toolbar.back_button'))
    back_button.clicked.connect(back)
    layout.addWidget(back_button)
    title = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_toolbar.title'))
    title.setObjectName("pageTitle")
    title.setToolTip(plain_tooltip(ui_enum_text(text_catalog, help_text)))
    layout.addWidget(title)
    role_box = QFrame()
    role_box.setObjectName("marginalRoleSelector")
    role_box.setStyleSheet(themed_style(
        "QFrame#marginalRoleSelector{background:#13233a;border:1px solid #2f81f7;"
        "border-radius:7px;}"
    ))
    role_layout = QHBoxLayout(role_box)
    role_layout.setContentsMargins(10, 4, 8, 4)
    role_layout.setSpacing(7)
    role_label = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_toolbar.role_label'))
    role_label.setStyleSheet(themed_style("color:#58a6ff;font-weight:700"))
    role_layout.addWidget(role_label)
    character_combo = NoWheelComboBox()
    character_combo.currentIndexChanged.connect(role_changed)
    role_layout.addWidget(character_combo)
    layout.addWidget(role_box)
    change_summary = plain_label(resolve_text_catalog(text_catalog).render('ui.battle_report.refresh_change_summary.change_summary'))
    change_summary.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    layout.addWidget(change_summary, 1)
    use_inferred_facts = QCheckBox(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_toolbar.use_inferred_facts'))
    use_inferred_facts.setChecked(True)
    use_inferred_facts.setToolTip(
        plain_tooltip(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_toolbar.use_inferred_facts_2'))
    )
    use_inferred_facts.hide()
    use_inferred_facts.toggled.connect(inferred_toggled)
    layout.addWidget(use_inferred_facts)
    reset_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.allocation.build.primary_reset_btn'))
    reset_button.setToolTip(plain_tooltip(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_toolbar.reset_button')))
    reset_button.clicked.connect(reset)
    layout.addWidget(reset_button)
    recalculate_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_toolbar.recalculate_button'))
    recalculate_button.setObjectName("btnPrimary")
    recalculate_button.clicked.connect(recalculate)
    layout.addWidget(recalculate_button)
    if axis_optimize is not None:
        optimize_button = TextActionButton(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_toolbar.optimize_button'))
        optimize_button.setAccessibleName(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_toolbar.optimize_button'))
        optimize_button.setToolTip(plain_tooltip(resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_toolbar.optimize_button_2')))
        optimize_button.clicked.connect(axis_optimize)
        layout.addWidget(optimize_button)
    help_button = QPushButton("?")
    help_button.setObjectName("btnHelp")
    help_button.setToolTip(plain_tooltip(ui_enum_text(text_catalog, help_text)))
    help_button.clicked.connect(
        lambda _checked=False: show_help(
            help_button,
            resolve_text_catalog(text_catalog).render('ui.battle_report.build_marginal_toolbar.title'),
            help_text,
        )
    )
    layout.addWidget(help_button)
    return BattleMarginalToolbar(
        widget=toolbar,
        character_combo=character_combo,
        change_summary=change_summary,
        use_inferred_facts=use_inferred_facts,
        reset_button=reset_button,
        recalculate_button=recalculate_button,
    )
