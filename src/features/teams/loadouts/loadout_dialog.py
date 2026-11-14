# 合并队伍来源设置与成员预览；读取草稿不落库，显式保存才提交，关闭等待本 owner 退场。
from threading import Event

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLayout,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.app.page_tasks import PageCommitLane
from src.features.teams.dialog_support import CompactFooter, ExplicitActionDialog, LocalizedDisclosure
from src.app.theme import current_style_sheet, themed_style
from src.app.window_geometry import fit_dialog_to_available_screen
from src.domain.teams.overview import SourceMode
from src.features.teams.loadouts.loadout_row import TeamLoadoutRow
from src.features.teams.report_matching.report_match_controller import ReportMatchController
from src.ui.common.text_catalog import ElidedTextLabel, TextActionButton, plain_label, resolve_text_catalog, set_button_text


class _ContentScroll(QScrollArea):
    def __init__(self, parent):
        super().__init__(parent)
        self.viewport().installEventFilter(self)

    def setWidget(self, widget):
        super().setWidget(widget)
        widget.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (watched is self.viewport() and event.type() in (QEvent.Resize, QEvent.Show)) or (
            watched is self.widget() and event.type() == QEvent.LayoutRequest
        ):
            body = self.widget()
            if body is not None and body.layout() is not None:
                body.setMinimumHeight(max(body.minimumSizeHint().height(),
                                          body.layout().totalHeightForWidth(self.viewport().width())))
        return super().eventFilter(watched, event)


class TeamLoadoutDialog(ExplicitActionDialog):
    def __init__(self, *, service, team, roles, catalog=None, parent=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(catalog)
        self.text = self.text_catalog.render
        self.setWindowTitle(self.text("teams.loadout.title", team=team.name))
        self.setStyleSheet(current_style_sheet())
        self.service, self.team = service, team
        self.selection, self._expected, self._closing = None, None, None
        self._preview_valid = False
        self._requested = None
        self._token = Event()
        self._reader = ReportMatchController(self)
        self._writer = PageCommitLane(self)
        self._reader.result.connect(self._read)
        self._reader.error.connect(self._failed)
        self._reader.drained.connect(self._drained)
        self._writer.settled.connect(self._drained)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(12)
        heading = QHBoxLayout()
        title = plain_label(self.text("teams.loadout.heading"))
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        title.setStyleSheet("font-size:17px;font-weight:700;")
        heading.addWidget(title, 1)
        self.ready_count = ElidedTextLabel("")
        self.ready_count.setMinimumWidth(100)
        self.ready_count.setMaximumWidth(180)
        heading.addWidget(self.ready_count)
        root.addLayout(heading)
        team_name = plain_label(team.name)
        team_name.setWordWrap(True)
        team_name.setStyleSheet(themed_style("color:#8b949e;font-size:12px;"))
        root.addWidget(team_name)
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        source = QHBoxLayout()
        for mode, key in (
            (SourceMode.CALC, "teams.loadout.source_calc"),
            (SourceMode.GAME, "teams.loadout.source_game"),
        ):
            button = TextActionButton(self.text(key))
            button.setCheckable(True)
            button.setProperty("sourceMode", mode.value)
            button.setStyleSheet(
                themed_style("QPushButton:checked{background:#0d1f35;color:#58a6ff;border:1px solid #58a6ff;}")
            )
            self.mode_group.addButton(button)
            self.mode_buttons[mode] = button
            source.addWidget(button, 1)
            button.clicked.connect(self._request_preview)
            button.setEnabled(False)
        self.mode_buttons[SourceMode.CALC].setChecked(True)
        root.addLayout(source)
        hint = plain_label(self.text("teams.loadout.readonly_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet(themed_style("color:#8b949e;font-size:12px;"))
        root.addWidget(hint)
        scroll = _ContentScroll(self)
        self.scroll = scroll
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget(scroll)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(0)
        layout.setSizeConstraint(QLayout.SetNoConstraint)
        self.error = plain_label("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet(themed_style("color:#f85149;"))
        self.error.hide()
        layout.addWidget(self.error)
        self.rows, self.slots = {}, {}
        for key in team.character_ids:
            row = TeamLoadoutRow(key, roles.get(key), catalog=self.text_catalog, parent=body)
            self.rows[key], self.slots[key] = row, row.choice
            self.slots[key].currentIndexChanged.connect(self._request_preview)
            layout.addWidget(row)
            line = QFrame(body)
            line.setFixedHeight(1)
            line.setStyleSheet(themed_style("background:#30363d;border:0;"))
            layout.addWidget(line)
        self.diagnostics = plain_label("")
        self.diagnostics.setWordWrap(True)
        self.diagnostics.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.diagnostics.setStyleSheet(themed_style("color:#8b949e;font-size:12px;"))
        self.diagnostics_section = LocalizedDisclosure(self.text('teams.loadout.diagnostics'), self.diagnostics,
                                                       parent=body, catalog=self.text_catalog)
        self.diagnostics_toggle = self.diagnostics_section.toggle
        self.diagnostics_toggle.toggled.connect(lambda _: QTimer.singleShot(0, self, self._fit_members))
        layout.addWidget(self.diagnostics_section)
        layout.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        self.state = ElidedTextLabel(self.text("teams.loadout.loading"))
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        save = self.buttons.button(QDialogButtonBox.Save)
        set_button_text(save, self.text("common.save"))
        save.setObjectName("btnNew")
        save.setStyleSheet(
            themed_style("QPushButton#btnNew:disabled{background:#161b22;color:#6e7681;border-color:#30363d;}")
        )
        save.setEnabled(False)
        save.setAutoDefault(False)
        cancel = self.buttons.button(QDialogButtonBox.Cancel)
        set_button_text(cancel, self.text("common.cancel"))
        cancel.setDefault(True)
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        root.addWidget(CompactFooter(status=self.state, buttons=self.buttons))
        fit_dialog_to_available_screen(self, QSize(820, 560))
        self._reader.submit(lambda cancelled, publish: ("choices", service.source_choices(team, cancelled=cancelled)))

    def _fit_members(self):
        if self._closing is not None or not self.isVisible():
            return
        self.layout().activate()
        body = self.scroll.widget()
        for row in self.rows.values():
            row._fit_content()
        body.layout().activate()
        content_height = max(body.minimumSizeHint().height(), body.layout().totalHeightForWidth(self.scroll.viewport().width()))
        chrome_height = self.height() - self.scroll.height()
        fit_dialog_to_available_screen(self, QSize(self.width(), max(480, content_height + chrome_height + 8)))

    def source_mode(self):
        return SourceMode(self.mode_group.checkedButton().property("sourceMode"))

    def showEvent(self, event):
        super().showEvent(event)
        fit_dialog_to_available_screen(self)

    def _draft(self):
        bindings = (
            tuple(
                sorted(
                    (key, combo.currentData()) for key, combo in self.slots.items() if combo.currentData() is not None
                )
            )
            if self.source_mode() == SourceMode.CALC
            else ()
        )
        return self.source_mode(), bindings

    def _read(self, value):
        if self._closing is not None:
            return
        if value[0] == "choices":
            self._expected, choices = value[1]
            self.mode_buttons[self._expected.mode].setChecked(True)
            for key, row in self.rows.items():
                row.populate(choices, dict(self._expected.bindings).get(key))
            for button in self.mode_buttons.values():
                button.setEnabled(True)
            self._request_preview()
        elif value[1] == self._requested:
            overview = value[2]
            self._preview_valid = True
            QTimer.singleShot(0, self, self._fit_members)
            self.ready_count.setText(self.text("teams.loadout.ready_count", ready=overview.ready_count, total=4))
            lines = []
            for member in overview.members:
                self.rows[member.character_id].show_member(member)
                lines.extend(
                    (
                        member.name,
                        self.text(
                            "teams.loadout.identities",
                            slot=member.slot_id or "—",
                            plan=member.plan_id or "—",
                            snapshot=member.snapshot_id or "—",
                        ),
                        self.text(
                            "teams.loadout.issues",
                            issues="；".join(member.issues) or self.text("teams.loadout.no_issues"),
                        ),
                        "",
                    )
                )
            if overview.issues:
                lines.append(self.text("teams.loadout.issues", issues="；".join(overview.issues)))
            self.diagnostics.setText("\n".join(lines))
            self._update_save()

    def _request_preview(self, *_):
        if self._expected is None or self._closing is not None or self._writer.is_running():
            return
        mode, bindings = self._draft()
        self._preview_valid = False
        self.error.clear()
        self.error.hide()
        self.ready_count.setText(self.text("teams.loadout.awaiting_check"))
        self.diagnostics.setText(self.text("teams.loadout.loading"))
        for row in self.rows.values():
            row.set_calculation(mode == SourceMode.CALC)
            row.pending()
        self._requested = (mode, bindings)
        service, team, expected, request = self.service, self.team, self._expected, self._requested
        self._reader.submit(
            lambda cancelled, publish: (
                "preview",
                request,
                service.preview_source(team, expected, mode=mode, bindings=bindings, cancelled=cancelled),
            )
        )
        self._update_save()

    def _update_save(self):
        changed = self._expected is not None and self._draft() != (self._expected.mode, self._expected.bindings)
        self.state.setText(self.text("teams.loadout.unsaved" if changed else "teams.loadout.unchanged"))
        self.buttons.button(QDialogButtonBox.Save).setEnabled(bool(changed and self._preview_valid))

    def _save(self):
        if not self._preview_valid or self._expected is None or self._writer.is_running():
            return
        mode, bindings = self._draft()
        service, team, expected, token = self.service, self.team, self._expected, self._token
        self._reader.cancel()
        self.buttons.button(QDialogButtonBox.Save).setEnabled(False)
        for control in (*self.mode_buttons.values(), *self.slots.values()):
            control.setEnabled(False)
        self._writer.submit(
            lambda: service.save_source(team, expected, mode=mode, bindings=bindings, cancelled=token.is_set),
            self._saved,
            self._failed,
            self._failed,
        )

    def _saved(self, value):
        self.selection = value
        self.done(QDialog.Accepted)

    def _failed(self, message):
        if self._closing is None:
            self.error.setText(
                self.text("teams.loadout.read_failed", reason=str(message or self.text("teams.loadout.awaiting_check")))
            )
            self.error.show()
            self.scroll.ensureWidgetVisible(self.error)
            self.state.setText(self.text("teams.loadout.awaiting_check"))
            for control in (*self.mode_buttons.values(), *self.slots.values()):
                control.setEnabled(self._expected is not None)
            self._preview_valid = False
            for row in self.rows.values():
                row.pending(loading=False)
            self.diagnostics.setText(self.text("teams.loadout.awaiting_check"))
            self.buttons.button(QDialogButtonBox.Save).setEnabled(False)

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
