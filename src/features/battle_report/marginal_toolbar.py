"""Fixed navigation and role-selection toolbar for battle marginal analysis."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QCheckBox, QFrame, QHBoxLayout, QLabel, QPushButton

from src.app.dialogs import show_help
from src.app.theme import themed_style
from src.ui.widgets import NoWheelComboBox


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
) -> BattleMarginalToolbar:
    help_text = (
        "冻结本场动作、逐击、目标和时段，只替换角色属性与配置。"
        "缺少变化依赖时分别展示已知分量与缺口，不把未知记为零收益。"
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
    back_button = QPushButton("← 返回战报")
    back_button.clicked.connect(back)
    layout.addWidget(back_button)
    title = QLabel("固定轴边际计算")
    title.setObjectName("pageTitle")
    title.setToolTip(help_text)
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
    role_label = QLabel("分析角色")
    role_label.setStyleSheet(themed_style("color:#58a6ff;font-weight:700"))
    role_layout.addWidget(role_label)
    character_combo = NoWheelComboBox()
    character_combo.currentIndexChanged.connect(role_changed)
    role_layout.addWidget(character_combo)
    layout.addWidget(role_box)
    change_summary = QLabel("等待角色配置")
    change_summary.setStyleSheet(themed_style("color:#8b949e;font-size:12px"))
    layout.addWidget(change_summary, 1)
    use_inferred_facts = QCheckBox("使用逐击补充的生效事实")
    use_inferred_facts.setChecked(True)
    use_inferred_facts.setToolTip(
        "仅在当前生效基线缺少、但完整原始逐击可精确证明角色效果已生效时显示；"
        "取消后只影响本页候选，不会改写战报快照、修改副本或角色页。"
    )
    use_inferred_facts.hide()
    use_inferred_facts.toggled.connect(inferred_toggled)
    layout.addWidget(use_inferred_facts)
    reset_button = QPushButton("重置")
    reset_button.setToolTip("恢复进入本页时的内存基线，不读库、不保存。")
    reset_button.clicked.connect(reset)
    layout.addWidget(reset_button)
    recalculate_button = QPushButton("重算")
    recalculate_button.setObjectName("btnPrimary")
    recalculate_button.clicked.connect(recalculate)
    layout.addWidget(recalculate_button)
    help_button = QPushButton("?")
    help_button.setObjectName("btnHelp")
    help_button.setToolTip(help_text)
    help_button.clicked.connect(
        lambda _checked=False: show_help(
            help_button,
            "固定轴边际计算",
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
