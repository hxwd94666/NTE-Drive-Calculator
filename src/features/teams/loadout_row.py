# 以紧凑头像行展示成员所选槽位与冻结缺项，窄窗口重排不重建选择身份。
from collections import Counter
from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.ui.image_scaling import asset_pixmap
from src.ui.role_portrait import custom_role_portrait, temporary_role_portrait
from src.ui.text_catalog import ElidedTextLabel, plain_label, plain_tooltip


class TeamLoadoutRow(QWidget):
    def __init__(self, character_id, role, *, catalog, parent=None):
        super().__init__(parent)
        self.character_id = character_id
        self.text = catalog.render
        self.name = role.name if role else self.text("teams.loadout.unknown_role", id=character_id)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 12, 0, 12)
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
        self.choice.currentTextChanged.connect(lambda value: self.choice.setToolTip(plain_tooltip(value)))
        self.game_label = plain_label(self.text("teams.loadout.source_game"))
        self.game_label.setWordWrap(True)
        self.count = plain_label("")
        self.count.setWordWrap(True)
        self.count.setStyleSheet(themed_style("color:#8b949e;font-size:12px;"))
        plan.addWidget(self.choice)
        plan.addWidget(self.game_label)
        plan.addWidget(self.count)
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
        self.choice.addItem(self.text("teams.loadout.unselected"), None)
        own = tuple(slot for slot in choices if slot["character_id"] == self.character_id)
        names = Counter(str(slot["slot_name"]) for slot in own)
        for slot in own:
            name = str(slot["slot_name"])
            label = (
                self.text("teams.loadout.slot_disambiguated", name=name, id=slot["slot_id"])
                if names[name] > 1
                else name
            )
            self.choice.addItem(label, slot["slot_id"])
        if selected is not None and self.choice.findData(selected) < 0:
            self.choice.addItem(self.text("teams.loadout.invalid_slot", id=selected), selected)
        self.choice.setCurrentIndex(max(0, self.choice.findData(selected)))
        self.choice.blockSignals(False)
        self.choice.setToolTip(plain_tooltip(self.choice.currentText()))

    def set_calculation(self, calculation):
        self.choice.setVisible(calculation)
        self.game_label.setVisible(not calculation)

    def pending(self, *, loading=True):
        self.count.setText(self.text("teams.loadout.awaiting_check"))
        self.status.setText(self.text("teams.loadout.loading" if loading else "teams.loadout.awaiting_check"))
        self.status.setToolTip("")
        self.status.setStyleSheet(themed_style("color:#8b949e;font-size:12px;"))

    def show_member(self, member):
        self.count.setText(
            self.text("teams.loadout.item_count", count=len(member.items))
            if member.items
            else self.text("teams.loadout.no_equipment")
        )
        self.status.setText(self.text("teams.loadout.missing") if member.issues else self.text("teams.loadout.ready"))
        self.status.setStyleSheet(
            themed_style("color:#d29922;font-size:12px;" if member.issues else "color:#7ee787;font-size:12px;")
        )
        self.status.setToolTip(plain_tooltip("\n".join(member.issues)))
        self.status.setAccessibleDescription("\n".join(member.issues))

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
