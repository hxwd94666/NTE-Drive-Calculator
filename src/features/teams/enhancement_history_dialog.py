# 冻结增强历史按结果、来源、覆盖和关联分区展示；分页和取消仍由本窗口 owner 管理。
import json

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QListWidget, QScrollArea, QVBoxLayout, QWidget

from src.features.teams.compact_details import CompactReadOnlyText, CurrentContentTabs, InitialContentFit, reveal_expanded_details
from src.domain.teams.enhancement_presentation import team_enhancement_presentation
from src.features.teams.report_matching.report_match_controller import ReportMatchController
from src.features.teams.dialog_support import CompactFooter, ExplicitActionDialog, LocalizedDisclosure
from src.ui.common.text_catalog import TextActionButton, plain_label, resolve_text_catalog, set_button_text


class TeamEnhancementHistoryDialog(ExplicitActionDialog):
    def __init__(self, *, service, team, names=None, parent=None, catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(catalog)
        self.text = self.text_catalog.render
        self.setWindowTitle(self.text('teams.history.title', team=team.name))
        self._service, self._team, self._names = service, team, names or {}
        self._rows, self._offset, self._closing = [], 0, None
        self._reader = ReportMatchController(self)
        self._reader.result.connect(self._loaded)
        self._reader.error.connect(self._failed)
        self._reader.drained.connect(self._drained)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 14)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        root = QVBoxLayout(body)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        root.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
        self.status = plain_label(self.text('teams.history.loading'))
        self.status.setWordWrap(True)
        root.addWidget(self.status)
        self.list = QListWidget()
        self.list.setAccessibleName(self.text('teams.history.list'))
        self.list.setMaximumHeight(170)
        self.list.setMinimumHeight(40)
        self.list.setWordWrap(True)
        self.list.currentRowChanged.connect(self._selected)
        root.addWidget(self.list)
        self.headline = plain_label('')
        self.headline.setWordWrap(True)
        self.headline.setStyleSheet('font-size:17px;font-weight:600;')
        root.addWidget(self.headline)
        self.tabs = CurrentContentTabs()
        self.details, self.sources, self.coverage, self.association = (CompactReadOnlyText() for _ in range(4))
        for key, edit in (('before_after', self.details), ('sources', self.sources), ('coverage', self.coverage), ('association', self.association)):
            edit.setReadOnly(True)
            edit.setAccessibleName(self.text('teams.history.' + key))
            self.tabs.addTab(edit, self.text('teams.history.' + key))
        root.addWidget(self.tabs)
        self.technical = plain_label('')
        self.technical.setWordWrap(True)
        self.technical.setTextInteractionFlags(Qt.TextSelectableByMouse)
        technical_body = QWidget()
        tech_layout = QVBoxLayout(technical_body)
        tech_layout.setContentsMargins(0, 0, 0, 0)
        tech_layout.addWidget(self.technical)
        self.technical_section = LocalizedDisclosure(self.text('teams.history.diagnostics'), technical_body,
                                                     catalog=self.text_catalog)
        root.addWidget(self.technical_section)
        reveal_expanded_details(self.scroll, self.technical_section)
        self.more = TextActionButton(self.text('teams.history.more'))
        self.more.clicked.connect(self._load)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Close)
        set_button_text(self.buttons.button(QDialogButtonBox.Close), self.text('teams.history.close'))
        self.buttons.rejected.connect(self.reject)
        self.footer = CompactFooter(leading=self.more, buttons=self.buttons)
        outer.addWidget(self.footer)
        self._initial_fit = InitialContentFit(self, body=body, footer=self.footer, width=720)
        QTimer.singleShot(0, self, self._load)

    def _load(self):
        if self._closing is not None or self._offset is None or self._reader.is_running():
            return
        service, team, offset = self._service, self._team, self._offset
        self.more.setEnabled(False)
        self._reader.submit(lambda cancelled, publish: service.enhancement_history(team, offset=offset, cancelled=cancelled))

    def _loaded(self, result):
        rows, self._offset = result
        known = {row.result_id for row in self._rows}
        for row in rows:
            if row.result_id in known:
                continue
            self._rows.append(row)
            shown = team_enhancement_presentation(row.payload)
            historical = set(row.payload['character_ids']) != set(self._team.character_ids)
            self.list.addItem(f'{row.created_at_utc} · {shown.headline}'
                              + (' · ' + self.text('teams.history.historical') if historical else ''))
        # 少量记录不预留整屏列表，分页仍使用同一列表并保留当前选择。
        content_height = sum(max(0, self.list.sizeHintForRow(i)) for i in range(min(self.list.count(), 8)))
        self.list.setMaximumHeight(min(170, max(40, content_height + 2 * self.list.frameWidth())))
        self.status.setText(self.text('teams.history.count', count=len(self._rows)) if self._rows else self.text('teams.history.empty'))
        for widget in (self.list, self.headline, self.tabs, self.technical_section):
            widget.setVisible(bool(self._rows))
        if self.list.currentRow() < 0 and self._rows:
            self.list.setCurrentRow(0)
        self._initial_fit.ready()

    def _failed(self, message):
        self.status.setText(self.text('teams.history.failed', reason=message or self.text('teams.history.changed')))
        self._initial_fit.ready()

    def _selected(self, index):
        if not 0 <= index < len(self._rows):
            return
        row = self._rows[index]
        payload = row.payload
        shown = team_enhancement_presentation(payload)
        self.headline.setText(shown.headline)
        missing = self.text('teams.history.missing')
        self.details.setPlainText('\n'.join((shown.headline,
            self.text('teams.history.before', value=payload['baseline_value'] if payload['baseline_value'] is not None else missing),
            self.text('teams.history.after', value=payload['candidate_value'] if payload['candidate_value'] is not None else missing))))
        source = payload['provenance']
        lines = [shown.source, '', self.text('teams.history.members')]
        diagnostics = [self.text('teams.history.static', value=source.get('dataset_id') or missing),
                       self.text('teams.history.engine', model=source.get('model_id') or missing,
                                 engine=source.get('engine_version') or missing)]
        for member in row.members:
            lines.append(self.text('teams.history.member_slot', name=self._names.get(member['character_id'],
                self.text('teams.report.role', id=member['character_id'])),
                slot=member['slot_id'] if member['slot_id'] is not None else missing))
            diagnostics.append(self.text('teams.history.member', name=self._names.get(member['character_id'],
                self.text('teams.report.role', id=member['character_id'])), slot=member['slot_id'] if member['slot_id'] is not None else missing, plan=member['plan_id'] if member['plan_id'] is not None else missing, snapshot=member['source_snapshot_id'] if member['source_snapshot_id'] is not None else missing))
        self.sources.setPlainText('\n'.join(lines))
        self.coverage.setPlainText(shown.coverage + '\n\n' + shown.search)
        association = list(row.association)
        if row.joint_summary_json is not None:
            joint = json.loads(row.joint_summary_json)
            association.extend(('', self.text('teams.history.joint')))
            if joint['relative_gain'] is None:
                association.append(self.text('teams.history.joint_unknown'))
            else:
                association.append(self.text('teams.history.joint_value', gain=f"{joint['relative_gain']:+.2%}", value=f"{joint['normalized_candidate']:.6g}"))
            for other in row.joint_results:
                text = team_enhancement_presentation(other.payload)
                names = '、'.join(self._names.get(key, self.text('teams.report.role', id=key)) for key in other.payload['character_ids'])
                association.extend((self.text('teams.history.other', names=names), text.headline, text.coverage, text.search))
            if not row.joint_results:
                association.append(self.text('teams.history.other_missing'))
        self.association.setPlainText('\n'.join(association))
        self.technical.setText('\n'.join((*diagnostics, self.text('teams.history.result', value=row.result_id),
                                           self.text('teams.history.run', value=row.run_id))))

    def is_running(self):
        return self._reader.is_running()

    def done(self, result):
        self._closing = result
        self._reader.close()
        if self.is_running():
            self.setEnabled(False)
        self._drained()

    def reject(self):
        self.done(QDialog.Rejected)

    def closeEvent(self, event):
        self.reject()
        event.ignore() if self.is_running() else event.accept()

    def _drained(self):
        if self._closing is not None and not self.is_running():
            super().done(self._closing)
        else:
            self.more.setEnabled(self._offset is not None and not self.is_running())

    def showEvent(self, event):
        super().showEvent(event)
        self._initial_fit.shown()
