# 以紧凑头像行展示成员所选槽位与冻结缺项，窄窗口重排不重建选择身份。
from src.ui.common.tooltip_policy import bind_elided_combo_tooltip
from collections import Counter
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.ui.common.image_scaling import asset_pixmap
from src.ui.roles.role_portrait import custom_role_portrait, temporary_role_portrait
from src.ui.common.text_catalog import ElidedTextLabel, plain_label, plain_tooltip


class TeamLoadoutRow(QWidget):
    def __init__(self, character_id, role, *, catalog, parent=None):
        super().__init__(parent)
        self.character_id = character_id
        self._calculation, self._own = True, ()
        self.text = catalog.render
        self.name = role.name if role else self.text("teams.loadout.unknown_role", id=character_id)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 8, 0, 8)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(4)
        self.person = QWidget(self)
        person = QHBoxLayout(self.person)
        person.setContentsMargins(0, 0, 0, 0)
        person.setSpacing(10)
        avatar = QLabel(self.person)
        avatar.setFixedSize(40, 40)
        avatar.setAccessibleName(self.text("selector.portrait", role=self.name))
        dpr = self.devicePixelRatioF()
        if role and role.icon_note and not role.custom:
            portrait = temporary_role_portrait(role.icon_path, 40, dpr)
        else:
            portrait = asset_pixmap(role.icon_path, 40, dpr) if role and role.icon_path and not role.custom else None
        avatar.setPixmap(portrait if portrait is not None and not portrait.isNull() else custom_role_portrait(40, dpr))
        if role and role.icon_note:
            avatar.setToolTip(plain_tooltip(role.icon_note))
        person.addWidget(avatar)
        name = ElidedTextLabel(self.name)
        name.setStyleSheet("font-weight:600;")
        person.addWidget(name, 1)
        self.plan = QWidget(self)
        plan = QVBoxLayout(self.plan)
        plan.setContentsMargins(0, 0, 0, 0)
        plan.setSpacing(4)
        self.choice = QComboBox(self.plan)
        self.choice.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.choice.setMinimumContentsLength(8)
        self.choice.setMinimumWidth(0)
        self.choice.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.choice.setAccessibleName(self.text("teams.loadout.slot_accessible", role=self.name))
        bind_elided_combo_tooltip(self.choice)
        self.game_label = plain_label(self.text("teams.loadout.source_game"))
        self.game_label.setWordWrap(False)
        self.description = ElidedTextLabel('')
        self.description.setStyleSheet(themed_style('color:#8b949e;font-size:12px;'))
        plan.addWidget(self.choice)
        plan.addWidget(self.game_label)
        plan.addWidget(self.description)
        self.status = plain_label("")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self._wide = None
        self.set_calculation(True)
        self.pending()
        self._reflow(False)

    def populate(self, choices, selected):
        self.choice.blockSignals(True)
        self.choice.clear()
        own = tuple(slot for slot in choices if slot["character_id"] == self.character_id)
        # 唯一已保存槽位没有歧义；多个槽位（即使只有一个有数据）仍由用户明确选择。
        single = len(own) == 1 and own[0].get("current_plan_id") is not None
        if single and selected is None:
            selected = own[0]["slot_id"]
        if not single:
            self.choice.addItem(self.text("teams.loadout.unselected"), None)
        self._own = own
        names = Counter(str(slot["slot_name"]) for slot in own)
        for slot in own:
            name = str(slot["slot_name"])
            label = (
                self.text("teams.loadout.slot_disambiguated", name=name, id=slot["slot_id"])
                if names[name] > 1
                else name
            )
            if slot.get("current_plan_id") is None:
                label = self.text("teams.loadout.empty_option", name=label)
            self.choice.addItem(label, slot["slot_id"])
        if selected is not None and self.choice.findData(selected) < 0:
            self.choice.addItem(self.text("teams.loadout.invalid_slot", id=selected), selected)
        self.choice.setCurrentIndex(max(0, self.choice.findData(selected)))
        self.choice.blockSignals(False)
        bind_elided_combo_tooltip(self.choice)

    def set_calculation(self, calculation):
        self._calculation = calculation
        self.choice.setVisible(calculation)
        self.game_label.setVisible(not calculation)

    def pending(self, *, loading=True):
        self.description.setText(self.text("teams.loadout.awaiting_check"))
        self.status.setText(self.text("teams.loadout.loading" if loading else "teams.loadout.awaiting_check"))
        self.status.setToolTip("")
        self.status.setStyleSheet(themed_style("color:#8b949e;font-size:12px;"))

    def show_member(self, member):
        count = (self.text("teams.loadout.item_count", count=len(member.items))
                 if member.items else self.text("teams.loadout.no_equipment"))
        self.status.setText(self.text("teams.loadout.missing") if member.issues else self.text("teams.loadout.ready"))
        self.status.setStyleSheet(
            themed_style("color:#d29922;font-size:12px;" if member.issues else "color:#7ee787;font-size:12px;")
        )
        self.status.setToolTip(plain_tooltip("\n".join(member.issues)))
        self.status.setAccessibleDescription("\n".join(member.issues))
        selected = self.choice.currentData()
        slot = next((row for row in self._own if row['slot_id'] == selected), None)
        key = None
        if self._calculation:
            if selected is None:
                key = 'pick_slot' if self._own else 'no_saved'
                help_key = 'choose_help' if self._own else 'no_slots_help'
            elif slot is None:
                key, help_key = 'invalid', 'choose_help'
            elif slot.get('current_plan_id') is None:
                key, help_key = 'empty_slot', 'save_help'
            else:
                help_key = 'single_help' if len(self._own) == 1 else 'multiple_help'
            if key:
                self.status.setText(self.text('teams.loadout.' + key))
            help_text = self.text('teams.loadout.' + help_key, **({'count': len(self._own)} if help_key == 'multiple_help' else {}))
        else:
            help_text = member.issues[0] if member.issues else ''
        if member.issues and key is None and self._calculation:
            help_text = member.issues[0]
        self.description.setText(self.text('teams.loadout.detail_line', count=count, reason=help_text) if help_text else count)
        self.description.setToolTip(plain_tooltip('\n'.join((count, help_text, *member.issues))))
        self._fit_content()

    def _fit_content(self):
        # 嵌套 HFW 可能低估带内边距的输入高度；按当前字体/主题和换行宽度保全整行。
        self.choice.setMinimumHeight(self.choice.minimumSizeHint().height())
        plan = self.plan.layout()
        self.plan.setMinimumHeight(max(plan.minimumSize().height(), plan.totalHeightForWidth(self.plan.width())))
        self.setMinimumHeight(max(self.grid.minimumSize().height(), self.grid.totalHeightForWidth(self.width())))

    def _reflow(self, wide):
        if wide == self._wide:
            return
        self._wide = wide
        while self.grid.count():
            self.grid.takeAt(0)
        self.grid.setColumnStretch(0, 1)
        self.grid.setColumnStretch(1, 2)
        self.grid.setColumnStretch(2, 1 if wide else 0)
        self.grid.addWidget(self.person, 0, 0, 1 if wide else 2, 1)
        self.grid.addWidget(self.plan, 0, 1)
        self.grid.addWidget(self.status, 0 if wide else 1, 2 if wide else 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow(self.width() >= 540)
        self._fit_content()
