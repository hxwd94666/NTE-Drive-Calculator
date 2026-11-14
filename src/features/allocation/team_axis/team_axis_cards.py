# 队伍选择卡压缩阵容展示；卡片空白和说明可勾选，内部动作不冒泡、不提交业务。
from src.ui.common.tooltip_policy import bind_elided_combo_tooltip
from PySide6.QtCore import QEvent, QSize, Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from src.app.theme import current_theme_name, themed_style
from src.domain.teams.models import TeamRole
from src.domain.teams.report_matching import MatchStatus
from src.features.teams.cards import role_button, set_role
from src.features.teams.report_matching.report_match_presentation import candidate_label, candidate_parts, scope_label, status_label
from src.integrations.runtime.bundled_resources import bundled_game_ui_asset_root
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.ui.common.text_catalog import ElidedTextLabel, TextActionButton, plain_tooltip, resolve_text_catalog


class _RosterRow(QWidget):
    """选队阵容固定横排；这里只复用公共头像按钮，不接入成员编辑页的自动换行。"""

    def __init__(self, parent, catalog):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.buttons = [role_button(parent=self, compact=True, catalog=catalog) for _ in range(4)]
        for button in self.buttons:
            button.setMinimumSize(62, 66)
            button.setMaximumWidth(66)
            button.setIconSize(QSize(40, 40))
            button.setStyleSheet(themed_style(
                'QToolButton{background:transparent;color:#c9d1d9;border:1px solid transparent;'
                'border-radius:6px;padding:2px;font-size:11px;font-weight:500;}'
                'QToolButton:hover{background:#21262d;border-color:#30363d;}'
                'QToolButton:focus{border-color:#58a6ff;}QToolButton:disabled{color:#6e7681;}'))
            row.addWidget(button)


class TeamAxisCard(QWidget):
    def __init__(self, team, names, *, catalog):
        super().__init__()
        text = catalog.render
        self._text, self._catalog, self._team = text, catalog, team
        self.setObjectName('axisTeamCard')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self._wide, self._pressed = None, None
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        self.roster = layout
        self.roster.setHorizontalSpacing(12)
        self.roster.setVerticalSpacing(6)
        self.header = QWidget(self)
        header = QHBoxLayout(self.header)
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self.check = QCheckBox()
        self.check.setFixedSize(22, 24)
        self.check.setAccessibleName(text('teams.selection.choose_accessible', team=team.name))
        self.title = ElidedTextLabel(team.name)
        self.title.setStyleSheet('font-weight:600;font-size:13px;')
        self.title.setMinimumWidth(70)
        self.title.setMaximumWidth(96)
        header.addWidget(self.check)
        header.addWidget(self.title, 0, Qt.AlignVCenter)
        self.slots = _RosterRow(self, catalog)
        assets = GameUiAssetCatalog(bundled_game_ui_asset_root())
        for index, button in enumerate(self.slots.buttons):
            if index < len(team.character_ids):
                key = team.character_ids[index]
                icon = str(assets.character_icon(key) or '')
                role = TeamRole(key, names.get(key, text('teams.report.role', id=key)), icon, custom=not bool(icon))
                set_role(button, role)
                button.setAccessibleName(text('teams.selection.role_accessible', team=team.name, role=role.name))
            button.clicked.connect(self.check.toggle)
        self.config = QWidget(self)
        form = QHBoxLayout(self.config)
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self.report = ElidedTextLabel(text('teams.selection.report_empty'))
        self.report.setStyleSheet('font-size:13px;font-weight:600;')
        self.report.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.report_details = ElidedTextLabel('')
        self.report_details.setStyleSheet(themed_style('color:#8b949e;font-size:11px;'))
        self.report_details.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.report_status = QLabel(text('teams.selection.report_unmatched'))
        self.report_status.setTextFormat(Qt.PlainText)
        self.report_status.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.report_status.setAlignment(Qt.AlignCenter)
        self.report_status.setStyleSheet(themed_style('font-size:11px;color:#f85149;'))
        self.report_details.hide()
        self.choose = TextActionButton(text('teams.selection.choose_report'))
        self.choose.setAutoDefault(False)
        self.choose.setObjectName('btnPrimary')
        self.choose.setMinimumSize(116, 36)
        self.choose.setMaximumWidth(176)
        self.choose.setStyleSheet('font-size:12px;padding:7px 12px;')
        self.choose.setAccessibleName(text('teams.selection.report_accessible', team=team.name))
        self.anchor = QComboBox()
        self.anchor.setAccessibleName(text('teams.selection.source_accessible', team=team.name))
        self.anchor.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.anchor.setMinimumContentsLength(8)
        self.anchor.setMinimumWidth(0)
        self.anchor.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self.anchor.addItem(text('teams.selection.source_original'), 'original')
        bind_elided_combo_tooltip(self.anchor)
        form.addWidget(self.anchor)
        self.anchor.currentIndexChanged.connect(self._source_changed)
        self.anchor.model().rowsInserted.connect(self._source_changed)
        self.anchor.model().rowsRemoved.connect(self._source_changed)
        self._source_changed()
        self.reason = QLabel()
        self.reason.setTextFormat(Qt.PlainText)
        self.reason.setWordWrap(True)
        self.reason.setStyleSheet(themed_style('color:#d29922;font-size:12px;'))
        self.members = QWidget(self)
        members = QHBoxLayout(self.members)
        members.setContentsMargins(0, 0, 0, 0)
        members.setSpacing(8)
        members.addWidget(self.header)
        members.addWidget(self.slots, 0, Qt.AlignLeft)
        self.report_panel = QWidget(self)
        self.report_panel.setObjectName('axisReportPanel')
        self.report_panel.setStyleSheet(themed_style('QWidget#axisReportPanel{border-left:1px solid #30363d;}'))
        reports = QHBoxLayout(self.report_panel)
        reports.setContentsMargins(14, 0, 0, 0)
        reports.setSpacing(12)
        info = QWidget(self.report_panel)
        report_info = QVBoxLayout(info)
        report_info.setContentsMargins(0, 0, 0, 0)
        report_info.setSpacing(3)
        report_info.addWidget(self.report)
        report_info.addWidget(self.report_details)
        report_info.addWidget(self.config)
        report_info.addWidget(self.reason)
        reports.addWidget(info, 1)
        # 状态独占信息与动作之间的一列，与按钮一起按整行上下居中，不受元数据行数影响。
        reports.addWidget(self.report_status, 0, Qt.AlignCenter)
        reports.addWidget(self.choose, 0, Qt.AlignVCenter)
        # 只监听被动区域；组合框和按钮自行处理输入，点击选择范围绝不反向取消本队。
        for widget in (self, self.members, self.report_panel, self.header, info, self.title,
                       self.slots, self.config, self.report, self.report_details,
                       self.report_status, self.reason):
            widget.installEventFilter(self)
        self.set_state(False, '')
        self._reflow(0)

    def set_state(self, selected, reason):
        self.reason.setText(reason)
        self.reason.setVisible(bool(reason))
        self.check.setAccessibleDescription(reason)
        self.config.setAccessibleDescription(reason)
        light = current_theme_name() == 'light'
        border = ('#0969da' if light else '#58a6ff') if selected else ('#d0d7de' if light else '#30363d')
        background = ('#eef5ff' if selected else '#ffffff') if light else ('#12223a' if selected else '#111720')
        # 左侧强调条始终占同样宽度，勾选和取消不会改变行高、头像位置或列表滚动。
        self.setStyleSheet(
            f'QWidget#axisTeamCard{{border:1px solid {border};border-left:3px solid {border};'
            f'border-radius:8px;background:{background};}}')

    def set_report(self, result):
        text = self._text
        _, scene, saved = candidate_parts(result.candidate, text)
        self.report.setText(text('teams.selection.report_headline', scene=scene, scope=scope_label(result.candidate.range, text)))
        self.report_details.setText(text('teams.selection.report_metadata', record=result.candidate.range.record_id, time=saved))
        self.report_details.show()
        self.report_status.setText(status_label(result.status, text))
        # 无结果和明确不匹配用红字；待核验、证据不足仍保留未知语义，不能冒充核验结论。
        color = ('#3fb950' if result.status == MatchStatus.MATCHED else '#f85149'
                 if result.status in (MatchStatus.DIFFERENT, MatchStatus.CONFLICT, MatchStatus.ERROR) else '#d29922')
        self.report_status.setStyleSheet(themed_style(f'font-size:11px;color:{color};'))
        self.report_status.show()
        detail = candidate_label(result.candidate, catalog=self._catalog) + '\n' + status_label(result.status, text)
        if result.reason:
            detail += '\n' + result.reason
        self.report.setToolTip(plain_tooltip(detail))
        self.report.setAccessibleDescription(detail)
        self.choose.setText(text('teams.selection.change_report'))
        self.choose.setAccessibleName(text('teams.selection.report_accessible', team=self._team.name))

    def _reflow(self, width):
        # 空间不足时整体下移战报区，四人仍是一行，不在子控件内重复决定列数。
        mode = 'wide' if width >= 720 else 'stacked'
        if mode == self._wide:
            return
        self._wide = mode
        while self.roster.count():
            self.roster.takeAt(0)
        # 左侧只放队伍身份和成员，战报动作、范围、来源及冲突原因全部留在右侧。
        # 阵容按四个头像的自然宽度靠左，剩余宽度留给可换行的战报说明。
        self.roster.setColumnStretch(0, 0)
        self.roster.setColumnStretch(1, 1 if mode != 'stacked' else 0)
        if mode != 'stacked':
            self.roster.addWidget(self.members, 0, 0, Qt.AlignTop)
            self.roster.addWidget(self.report_panel, 0, 1, Qt.AlignVCenter)
        else:
            self.roster.addWidget(self.members, 0, 0)
            self.roster.addWidget(self.report_panel, 1, 0)

    def _source_changed(self, *_):
        multiple = self.anchor.count() > 1
        self.anchor.setVisible(multiple)
        # 默认原始来源由稳定 ID 保留，不额外展示重复文字；只有实际存在编辑来源时才让用户选。
        self.config.setVisible(multiple)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow(self.width())

    def eventFilter(self, watched, event):
        if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
            self._pressed = watched
            return True
        if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
            activate = self._pressed is watched and watched.rect().contains(event.position().toPoint())
            self._pressed = None
            if activate and self.check.isEnabled():
                self.check.toggle()
            return True
        return super().eventFilter(watched, event)


def build_team_axis_card(team, names, *, catalog=None):
    card = TeamAxisCard(team, names, catalog=resolve_text_catalog(catalog))
    return dict(team=team, card=card, check=card.check, slots=card.slots, config=card.config,
                reason=card.reason, report=card.report, anchor=card.anchor, choose=card.choose, match=None)
