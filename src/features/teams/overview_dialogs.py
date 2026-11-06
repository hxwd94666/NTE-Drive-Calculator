# 队伍来源配置只提交明确草稿；详情展示冻结配装来源和缺口，不输出完整 UID。
from threading import Event

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel, QTextEdit, QVBoxLayout

from src.app.page_tasks import PageCommitLane
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.teams.overview import SOURCE_LABELS, SourceMode
from src.features.teams.report_match_controller import ReportMatchController


class TeamSourceDialog(QDialog):
    def __init__(self, *, service, team, names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("配装来源 · " + team.name)
        self.service, self.team, self.names = service, team, names
        self.selection, self._expected, self._closing = None, None, None
        self._token = Event()
        self._reader = ReportMatchController(self)
        self._writer = PageCommitLane(self)
        self._reader.result.connect(self._loaded)
        self._reader.error.connect(self._failed)
        self._reader.drained.connect(self._drained)
        self._writer.settled.connect(self._drained)
        root = QVBoxLayout(self)
        hint = QLabel("选择本队读取哪套配装。这里只读配置，不会同步或装配到游戏。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.mode = QComboBox()
        self.mode.setAccessibleName("队伍简报配装来源")
        for mode, label in SOURCE_LABELS.items():
            self.mode.addItem(label, mode)
        root.addWidget(self.mode)
        self.form = QFormLayout()
        root.addLayout(self.form)
        self.slots = {}
        self.error = QLabel("正在读取本队来源与槽位…")
        self.error.setTextFormat(Qt.PlainText)
        self.error.setWordWrap(True)
        root.addWidget(self.error)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText("保存来源")
        self.buttons.button(QDialogButtonBox.Cancel).setText("取消")
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        self.mode.currentIndexChanged.connect(self._mode_changed)
        root.addWidget(self.buttons)
        fit_dialog_to_available_screen(self, QSize(640, 480))
        self._reader.submit(lambda cancelled, publish: service.source_choices(team, cancelled=cancelled))

    def _loaded(self, value):
        self._expected, slots = value
        self.mode.setCurrentIndex(self.mode.findData(self._expected.mode))
        for key in self.team.character_ids:
            combo = QComboBox()
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
            combo.setAccessibleName(f"{self.names.get(key, key)} 的计算槽位")
            combo.addItem("未选择", None)
            for slot in slots:
                if slot["character_id"] == key:
                    combo.addItem(f"{slot['slot_name']} · 槽位 {slot['slot_id']}", slot["slot_id"])
            bound = dict(self._expected.bindings).get(key)
            if bound is not None and combo.findData(bound) < 0:
                combo.addItem(f"槽位 {bound}（已失效，须重新选择）", bound)
            combo.setCurrentIndex(max(0, combo.findData(bound)))
            self.slots[key] = combo
            label = QLabel(str(self.names.get(key, f"角色 {key}")))
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            self.form.addRow(label, combo)
        self.error.setText("缺少方案也可保存，缺项会显示在队伍简报中。")
        self.buttons.button(QDialogButtonBox.Save).setEnabled(True)
        self._mode_changed()

    def _mode_changed(self):
        for combo in self.slots.values():
            combo.setEnabled(self.mode.currentData() == SourceMode.SLOTS)

    def _save(self):
        if self._expected is None or self._writer.is_running():
            return
        mode = self.mode.currentData()
        bindings = tuple((key, combo.currentData()) for key, combo in self.slots.items()
                         if combo.currentData() is not None) if mode == SourceMode.SLOTS else ()
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        self.mode.setEnabled(False)
        for combo in self.slots.values():
            combo.setEnabled(False)
        service, team, expected, token = self.service, self.team, self._expected, self._token
        self._writer.submit(lambda: service.save_source(team, expected, mode=mode, bindings=bindings, cancelled=token.is_set),
                            self._saved, self._failed, self._failed)

    def _saved(self, value):
        self.selection = value
        self.done(QDialog.Accepted)

    def _failed(self, message):
        self.error.setText(message or "来源请求已取消，请重新打开")
        if self._closing is None:
            self.mode.setEnabled(True)
            self.buttons.button(QDialogButtonBox.Save).setEnabled(self._expected is not None)
            self._mode_changed()

    def is_running(self):
        return self._reader.is_running() or self._writer.is_running()

    def done(self, result):
        self._closing = result
        self._token.set()
        self._reader.close()
        if self.is_running():
            self.setEnabled(False)
        self._drained()

    def reject(self):
        self.done(QDialog.Rejected)

    def closeEvent(self, event):
        self.reject()
        event.ignore() if self.is_running() else event.accept()

    def _drained(self, *_):
        if self._closing is not None and not self.is_running():
            super().done(self._closing)


def member_overview_dialog(overview, parent=None):
    dialog = QDialog(parent)
    dialog.setWindowTitle("成员与配装来源 · " + overview.team.name)
    root = QVBoxLayout(dialog)
    text = QTextEdit()
    text.setReadOnly(True)
    lines = ["来源：" + SOURCE_LABELS[overview.source.mode],
             "查看各成员的配装和缺项；个人装备评分请到角色页查看。",
             "队伍待补齐：" + ("；".join(overview.issues) if overview.issues else "无")]
    for member in overview.members:
        lines += ["", member.name,
                  f"槽位 {member.slot_id or '—'} · 方案 {member.plan_id or '—'} · 来源快照 {member.snapshot_id or '—'}",
                  f"配装物品：{len(member.items)} 件",
                  "缺口：" + ("；".join(member.issues) if member.issues else "无")]
    text.setPlainText("\n".join(filter(lambda line: line is not None, lines)))
    root.addWidget(text)
    buttons = QDialogButtonBox(QDialogButtonBox.Close)
    buttons.rejected.connect(dialog.reject)
    root.addWidget(buttons)
    fit_dialog_to_available_screen(dialog, QSize(760, 650))
    return dialog
