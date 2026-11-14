# 队伍条目按阵容与状态就近分组；窄屏重排同一批控件，保留身份、焦点和后台结果。
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QMenu, QSizePolicy,
    QToolButton, QWidget,
)

from src.app.theme import themed_style
from src.ui.common.text_catalog import ElidedTextLabel, TextActionButton, plain_tooltip, resolve_text_catalog, set_button_text
from src.domain.teams.models import TeamRole
from src.features.teams.cards import MemberSlots, set_role


class TeamCard(QWidget):
    """只拥有队伍展示；所有操作把稳定 team_id 发回页面，不自行读库或计算。"""

    def __init__(self, team, roles, *, actions, parent=None, catalog=None):
        super().__init__(parent)
        catalog = resolve_text_catalog(catalog)
        text = catalog.render
        self.setObjectName('teamCard')
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.setStyleSheet(themed_style(
            'QWidget#teamCard{background:#161b22;border:1px solid #30363d;border-radius:8px;}'))
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(16, 12, 16, 12)
        self.grid.setHorizontalSpacing(24)
        self.grid.setVerticalSpacing(8)
        self._wide = None
        self.names = [team.name]
        self.heading = QWidget(self)
        heading = QHBoxLayout(self.heading)
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(10)
        name = QLabel(team.name)
        name.setTextFormat(Qt.PlainText)
        name.setWordWrap(True)
        name.setStyleSheet('font-size:16px;font-weight:700;')
        heading.addWidget(name)
        count = QLabel(text('teams.card.member_count', count=len(team.character_ids)))
        count.setStyleSheet(themed_style('color:#8b949e;font-size:12px;'))
        heading.addWidget(count)
        heading.addStretch()
        self.members = MemberSlots(self, compact=True, catalog=catalog)
        for index, button in enumerate(self.members.buttons):
            role = None
            if index < len(team.character_ids):
                key = team.character_ids[index]
                role = roles.get(key, TeamRole(key, text('teams.editor.unavailable_role', id=key)))
                self.names.append(role.name)
            set_role(button, role)
            button.setAccessibleName(text('teams.card.edit_member', team=team.name, member=role.name if role else text('teams.card.add_member')))
            button.clicked.connect(lambda checked=False, key=team.team_id: actions['edit'].emit(key))

        # 两行共享四列，摘要与入口始终一一对齐；窄屏只重排外层阵容区域。
        self.right = QWidget(self)
        right = QGridLayout(self.right)
        right.setContentsMargins(0, 0, 0, 0)
        right.setHorizontalSpacing(8)
        right.setVerticalSpacing(8)
        for column in range(4):
            right.setColumnStretch(column, 1)
        self.labels = {}
        for key, label_key in (('source', 'teams.card.source_loading'),
                               ('axis_graduation', 'teams.card.graduation_unread'),
                               ('enhancement', 'teams.card.enhancement_loading')):
            label = ElidedTextLabel(text(label_key))
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(False)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            label.setStyleSheet(themed_style('color:#c9d1d9;font-size:12px;'))
            column = len(self.labels)
            label.setAlignment(Qt.AlignCenter)
            right.addWidget(label, 0, column)
            self.labels[key] = label
        self.labels['axis_graduation'].setToolTip(plain_tooltip(text('teams.card.graduation_hint')))

        action_buttons = []
        for column, (label_key, key, tip_key) in enumerate((('teams.menu.loadouts', 'loadouts', 'teams.card.loadouts_hint'),
                                        ('teams.card.graduation', 'graduation', 'teams.card.graduation_hint'),
                                        ('teams.menu.history', 'enhancement_details', 'teams.card.history_hint'))):
            label = text(label_key)
            button = TextActionButton(label)
            button.setStyleSheet('padding:6px 12px;')
            button.setAccessibleName(f'{team.name}：{label}')
            button.setToolTip(plain_tooltip(text(tip_key)))
            button.clicked.connect(lambda checked=False, target=key: actions[target].emit(team.team_id))
            right.addWidget(button, 1, column)
            action_buttons.append(button)
        more = QToolButton(self)
        set_button_text(more, text('teams.card.more'))
        more.setAccessibleName(f'{team.name}：' + text('teams.card.more'))
        more.setFocusPolicy(Qt.StrongFocus)
        more.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        more.setPopupMode(QToolButton.InstantPopup)
        more.setStyleSheet(themed_style(
            'QToolButton{background:transparent;color:#c9d1d9;border:1px solid #30363d;'
            'border-radius:6px;padding:6px 10px;}'
            'QToolButton:hover{background:#21262d;}QToolButton:focus{border-color:#58a6ff;}'))
        menu = QMenu(more)
        for label_key, key in (('teams.menu.edit', 'edit'), ('teams.menu.delete', 'delete')):
            if key == 'delete':
                menu.addSeparator()
            action = menu.addAction(catalog.render(label_key))
            action.triggered.connect(lambda checked=False, target=key: actions[target].emit(team.team_id))
        more.setMenu(menu)
        right.addWidget(more, 1, 3)
        calculate = TextActionButton(text('teams.calculate.title'))
        calculate.setObjectName('btnPrimary')
        calculate.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        calculate.setAccessibleName(f'{team.name}：' + text('teams.calculate.title'))
        calculate.setToolTip(plain_tooltip(text('teams.calculate.hint')))
        calculate.clicked.connect(lambda checked=False: actions['calculate'].emit(team.team_id))
        right.addWidget(calculate, 0, 3, Qt.AlignCenter)
        QWidget.setTabOrder(self.members.buttons[-1], calculate)
        for previous, following in zip([calculate, *action_buttons], [*action_buttons, more]):
            QWidget.setTabOrder(previous, following)
        self._reflow(False)

    def _reflow(self, wide):
        if self._wide == wide:
            return
        self._wide = wide
        # 移动现有布局项而不重建控件；账号代次和来源状态仍由原 controller 管理。
        while self.grid.count():
            self.grid.takeAt(0)
        if wide:
            # 阵容列消费自然宽度，剩余空间归状态列，避免缩紧头像后留下大段中间空白。
            self.grid.setColumnStretch(0, 0)
            self.grid.setColumnStretch(1, 1)
            self.grid.addWidget(self.heading, 0, 0)
            self.grid.addWidget(self.members, 1, 0)
            self.grid.addWidget(self.right, 0, 1, 2, 1)
        else:
            self.grid.setColumnStretch(0, 1)
            self.grid.setColumnStretch(1, 0)
            self.grid.addWidget(self.heading, 0, 0, 1, 2)
            self.grid.addWidget(self.members, 1, 0, 1, 2)
            self.grid.addWidget(self.right, 2, 0, 1, 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow(self.width() >= 900)
