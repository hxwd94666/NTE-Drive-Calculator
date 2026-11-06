# 目标编辑只操作冻结草稿；确认不保存或求解，图纸锚点来自已校验的保存方案而非界面猜测。
from dataclasses import replace

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
    QLabel, QScrollArea, QTabWidget, QVBoxLayout, QWidget,
)

from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.teams.axis_graduation_configuration import make_configuration_target
from src.domain.teams.axis_graduation_target import TargetWorldBonus


class GraduationTargetDialog(QDialog):
    def __init__(self, *, configuration, parent=None):
        super().__init__(parent)
        self.configuration, self.target = configuration, None
        self.setWindowTitle("设置毕业目标 · " + configuration.team.name)
        self.setModal(True)
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.timeout.connect(self._fit)
        outer = QVBoxLayout(self)
        hint = QLabel("目标装备沿用所选图纸的卡带与布局，词条在合法范围内优化，不受当前库存限制。"
            "觉醒和弧盘保持当前配置。确认后还需点击“建立基准”，这里不开始计算。")
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
            growth.setAccessibleName(f"{member.name}目标等级与突破")
            for level, stage in member.growth:
                growth.addItem(f"等级 {level} / 突破 {stage}", (level, stage))
            # Qt会将tuple用户数据转为QVariantList；按冻结档位值找默认行，不依赖跨类型findData比较。
            growth.setCurrentIndex(next((n for n, point in enumerate(member.growth)
                if point == (member.default.character_level, member.default.breakthrough_stage)), -1))
            self.growth[cid] = growth
            form.addRow("目标成长", growth)
            like = QCheckBox("好感十级")
            like.setChecked(member.default.likeability_level_10_enabled)
            self.likes[cid] = like
            form.addRow(like)
            fixed = QLabel(f"觉醒 {member.default.awakening_level} · 弧盘沿用当前配置")
            fixed.setToolTip(f"已选觉醒效果：{member.default.selected_awaken_effect_ids}\n弧盘：{member.default.fork}")
            fixed.setTextFormat(Qt.PlainText)
            fixed.setWordWrap(True)
            form.addRow(fixed)
            for skill, _levels in member.skill_levels:
                combo = QComboBox()
                combo.setAccessibleName(f"{member.name}目标基础技能 {skill}")
                combo.setPlaceholderText("请选择满足成长前置的技能等级")
                self.skills[cid, skill] = combo
                form.addRow(skill, combo)
            layout = QComboBox()
            layout.setAccessibleName(f"{member.name}固定图纸模板")
            layout.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            layout.setMinimumContentsLength(12)
            for template in configuration.templates:
                if template.character_id != cid:
                    continue
                layout.addItem(template.label + (" · " + template.reason if template.reason else " · 完整落位"), template.slot_id)
                if template.layout is None:
                    layout.model().item(layout.count()-1).setEnabled(False)
            layout.setCurrentIndex(layout.findData(member.preferred_slot_id))
            layout.setPlaceholderText("缺少完整落位方案，请先保存计算图纸")
            self.layouts[cid] = layout
            form.addRow("目标图纸（可与当前配装不同）", layout)
            scroll.setWidget(content)
            tabs.addTab(scroll, member.name)
            self._skills_for_growth(member, initial=True)
            growth.currentIndexChanged.connect(lambda _n, actor=member: self._skills_for_growth(actor))
        world = QFormLayout()
        self.attack = QDoubleSpinBox()
        self.attack.setRange(0, 20)
        self.attack.setValue(configuration.world.yaodao_attack_add)
        self.attack.setAccessibleName("目标妖刀家具攻击加成")
        self.crit = QDoubleSpinBox()
        self.crit.setRange(0, 4)
        self.crit.setDecimals(2)
        self.crit.setSuffix("%")
        self.crit.setValue(configuration.world.quantao_crit_damage * 100)
        self.crit.setAccessibleName("目标拳套家具暴伤加成百分比")
        world.addRow("妖刀家具攻击（平值）", self.attack)
        world.addRow("拳套家具暴伤", self.crit)
        outer.addLayout(world)
        self.error = QLabel()
        self.error.setTextFormat(Qt.PlainText)
        self.error.setWordWrap(True)
        outer.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText("确认目标")
        self.buttons.accepted.connect(self._accept_draft)
        self.buttons.rejected.connect(self.reject)
        outer.addWidget(self.buttons)
        if any(self.layouts[cid].currentData() is None for cid in configuration.team.character_ids):
            self.error.setText("部分成员还没有完整图纸。请先保存计算配装，再重新设置目标。")
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
                    raise ValueError("请明确选择每名成员的目标成长及技能等级")
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
