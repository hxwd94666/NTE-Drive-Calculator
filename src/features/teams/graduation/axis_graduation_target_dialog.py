# 目标编辑只操作冻结草稿；确认不保存或求解，图纸锚点来自已校验的保存方案而非界面猜测。
from dataclasses import replace

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QLabel, QScrollArea, QSizePolicy, QTabWidget, QVBoxLayout, QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.features.teams.dialog_support import ExplicitActionDialog, LocalizedDisclosure
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog, set_button_text
from src.domain.teams.graduation.axis_graduation_configuration import make_configuration_target
from src.domain.teams.graduation.axis_graduation_target import TargetWorldBonus


class GraduationTargetDialog(ExplicitActionDialog):
    def __init__(self, *, configuration, parent=None, catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(catalog)
        self.text = self.text_catalog.render
        self.configuration, self.target = configuration, None
        self.setWindowTitle(self.text("teams.target.title", team=configuration.team.name))
        self.setModal(True)
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self._fit)
        outer = QVBoxLayout(self)
        hint = QLabel(self.text('teams.target.hint'))
        hint.setTextFormat(Qt.PlainText)
        hint.setWordWrap(True)
        outer.addWidget(hint)
        tabs = QTabWidget()
        outer.addWidget(tabs, 1)
        self.growth, self.skills, self.likes, self.layouts = {}, {}, {}, {}
        for member in configuration.members:
            cid = member.default.character_id
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            content = QWidget()
            form = QFormLayout(content)
            form.setRowWrapPolicy(QFormLayout.WrapAllRows)
            growth = QComboBox()
            growth.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            growth.setMinimumContentsLength(8)
            growth.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            growth.setAccessibleName(self.text('teams.target.growth_accessible', role=member.name))
            for level, stage in member.growth:
                growth.addItem(self.text('teams.target.growth_value', level=level, stage=stage), (level, stage))
            # Qt会将tuple用户数据转为QVariantList；按冻结档位值找默认行，不依赖跨类型findData比较。
            growth.setCurrentIndex(next((n for n, point in enumerate(member.growth)
                if point == (member.default.character_level, member.default.breakthrough_stage)), -1))
            self.growth[cid] = growth
            form.addRow(self.text('teams.target.growth'), growth)
            like = QCheckBox(self.text('teams.target.like'))
            like.setChecked(member.default.likeability_level_10_enabled)
            self.likes[cid] = like
            form.addRow(like)
            fixed = QLabel(self.text('teams.target.fixed', awake=member.default.awakening_level))
            fixed.setToolTip(plain_tooltip(self.text('teams.target.fixed_hint', effects=str(member.default.selected_awaken_effect_ids), fork=str(member.default.fork))))
            fixed.setTextFormat(Qt.PlainText)
            fixed.setWordWrap(True)
            form.addRow(fixed)
            for skill, _levels in member.skill_levels:
                combo = QComboBox()
                combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
                combo.setMinimumContentsLength(8)
                combo.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
                combo.setAccessibleName(self.text('teams.target.skill_accessible', role=member.name, skill=skill))
                combo.setPlaceholderText(self.text('teams.target.skill_placeholder'))
                self.skills[cid, skill] = combo
                form.addRow(skill, combo)
            layout = QComboBox()
            layout.setAccessibleName(self.text('teams.target.layout_accessible', role=member.name))
            layout.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            layout.setMinimumContentsLength(12)
            for template in configuration.templates:
                if template.character_id != cid:
                    continue
                layout.addItem(template.label + (" · " + template.reason if template.reason else self.text('teams.target.complete')), template.slot_id)
                if template.layout is None:
                    layout.model().item(layout.count()-1).setEnabled(False)
            layout.setCurrentIndex(layout.findData(member.preferred_slot_id))
            layout.setPlaceholderText(self.text('teams.target.layout_placeholder'))
            layout.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            self.layouts[cid] = layout
            form.addRow(self.text('teams.target.layout'), layout)
            for row in range(form.rowCount()):
                item = form.itemAt(row, QFormLayout.LabelRole)
                label = item.widget() if item else None
                if isinstance(label, QLabel):
                    label.setTextFormat(Qt.PlainText)
                    label.setWordWrap(True)
            scroll.setWidget(content)
            tabs.addTab(scroll, member.name)
            self._skills_for_growth(member, initial=True)
            growth.currentIndexChanged.connect(lambda _n, actor=member: self._skills_for_growth(actor))
        world_body = QWidget()
        world = QFormLayout(world_body)
        self.attack = QDoubleSpinBox()
        self.attack.setDecimals(6)
        self.attack.setRange(0, 20)
        self.attack.setValue(configuration.world.yaodao_attack_add)
        self.attack.setAccessibleName(self.text('teams.target.attack_accessible'))
        self.crit = QDoubleSpinBox()
        self.crit.setRange(0, 4)
        self.crit.setDecimals(6)
        self.crit.setSuffix("%")
        self.crit.setValue(configuration.world.quantao_crit_damage * 100)
        self.crit.setAccessibleName(self.text('teams.target.crit_accessible'))
        world.addRow(self.text('teams.target.attack'), self.attack)
        world.addRow(self.text('teams.target.crit'), self.crit)
        for row in range(world.rowCount()):
            label = world.itemAt(row, QFormLayout.LabelRole).widget()
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
        outer.addWidget(LocalizedDisclosure(self.text("teams.target.world"), world_body, catalog=self.text_catalog))
        self.error = QLabel()
        self.error.setTextFormat(Qt.PlainText)
        self.error.setWordWrap(True)
        outer.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        set_button_text(self.buttons.button(QDialogButtonBox.Ok), self.text("teams.target.confirm"))
        set_button_text(self.buttons.button(QDialogButtonBox.Cancel), self.text("common.cancel"))
        self.buttons.accepted.connect(self._accept_draft)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        if any(self.layouts[cid].currentData() is None for cid in configuration.team.character_ids):
            self.error.setText(self.text('teams.target.no_layout'))
            self.buttons.button(QDialogButtonBox.Ok).setEnabled(False)

    def _skills_for_growth(self, member, *, initial=False):
        cid = member.default.character_id
        growth = self.growth[cid].currentData()
        for skill, levels in member.skill_levels:
            combo = self.skills[cid, skill]
            previous = dict(member.default.skill_levels)[skill] if initial else combo.currentData()
            combo.clear()
            for level, stage, awake in levels:
                if growth is not None and stage <= growth[1] and awake <= member.default.awakening_level:
                    combo.addItem(str(level), level)
            # 成长下调不静默降技能：旧值不合法时要求重新明确选择。
            combo.setCurrentIndex(combo.findData(previous))

    def _accept_draft(self):
        try:
            members, slots = [], []
            for source in self.configuration.members:
                cid = source.default.character_id
                growth = self.growth[cid].currentData()
                if growth is None or any(self.skills[cid, skill].currentData() is None for skill, _ in source.skill_levels):
                    raise ValueError(self.text('teams.target.choose_values'))
                members.append(replace(source.default, character_level=growth[0], breakthrough_stage=growth[1],
                    likeability_level_10_enabled=self.likes[cid].isChecked(),
                    skill_levels=tuple((skill, self.skills[cid, skill].currentData()) for skill, _ in source.skill_levels)))
                slots.append((cid, self.layouts[cid].currentData()))
            self.target = make_configuration_target(self.configuration, members=members, slot_ids=slots,
                world=TargetWorldBonus(self.attack.value(), self.crit.value()/100))
        except ValueError as error:
            self.error.setText(str(error))
            return
        self.accept()

    def _fit(self):
        if self.isVisible():
            fit_dialog_to_available_screen(self, QSize(700, 650))

    def showEvent(self, event):
        super().showEvent(event)
        self._geometry_timer.start(0)
