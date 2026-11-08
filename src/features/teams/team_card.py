# 队伍条目按阵容与状态就近分组；窄屏重排同一批控件，保留身份、焦点和后台结果。
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy,
    QToolButton, QVBoxLayout, QWidget,
)

from src.app.theme import themed_style
from src.ui.text_catalog import resolve_text_catalog
from src.domain.teams.models import TeamRole
from src.features.teams.cards import MemberSlots, set_role


class TeamCard(QWidget):
    """只拥有队伍展示；所有操作把稳定 team_id 发回页面，不自行读库或计算。"""

    def __init__(self, team, roles, *, actions, parent=None, catalog=None):
        super().__init__(parent)
        catalog = resolve_text_catalog(catalog)
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
        count = QLabel(f'{len(team.character_ids)}/4 人')
        count.setStyleSheet(themed_style('color:#8b949e;font-size:12px;'))
        heading.addWidget(count)
        heading.addStretch()
        self.members = MemberSlots(self, compact=True)
        for index, button in enumerate(self.members.buttons):
            role = None
            if index < len(team.character_ids):
                key = team.character_ids[index]
                role = roles.get(key, TeamRole(key, f'角色 {key}（暂不可用）'))
                self.names.append(role.name)
            set_role(button, role)
            button.setAccessibleName(f'编辑队伍 {team.name}，' + (role.name if role else '添加成员'))
            button.clicked.connect(lambda checked=False, key=team.team_id: actions['edit'].emit(key))

        self.summary = QWidget(self)
        summary = QGridLayout(self.summary)
        summary.setContentsMargins(0, 2, 0, 2)
        summary.setHorizontalSpacing(12)
        self.labels = {}
        for key, text in (('axis_graduation', '毕业率：尚未读取历史'), ('enhancement', '增强：正在读取…'),
                          ('source', '配装：正在读取…')):
            label = QLabel(text)
            label.setTextFormat(Qt.PlainText)
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            label.setStyleSheet(themed_style('color:#c9d1d9;font-size:12px;'))
            column = len(self.labels)
            summary.addWidget(label, 0, column)
            summary.setColumnStretch(column, 2 if key == 'source' else 1)
            self.labels[key] = label
        self.labels['axis_graduation'].setToolTip(
            '固定战报轴下，当前伤害相对理论目标的比例。实战可执行性另行提示。')

        self.actions = QWidget(self)
        self.actions.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        toolbar = QHBoxLayout(self.actions)
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)
        for text, key, tip in (('匹配战报', 'match', '查找相同四人阵容的战报'),
                               ('战报增强', 'enhancement', '进入增强配置，不会立即计算'),
                               ('毕业率', 'graduation', '比较固定战报轴下的当前伤害与理论目标')):
            button = QPushButton(text)
            button.setStyleSheet('padding:6px 12px;')
            button.setAccessibleName(f'{team.name}：{text}')
            button.setToolTip(tip)
            button.clicked.connect(lambda checked=False, target=key: actions[target].emit(team.team_id))
            toolbar.addWidget(button, 1)
        more = QToolButton(self)
        more.setText('更多')
        more.setAccessibleName(f'{team.name}：更多操作')
        more.setFocusPolicy(Qt.StrongFocus)
        more.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        more.setPopupMode(QToolButton.InstantPopup)
        more.setStyleSheet(themed_style(
            'QToolButton{background:transparent;color:#c9d1d9;border:1px solid #30363d;'
            'border-radius:6px;padding:6px 10px;}'
            'QToolButton:hover{background:#21262d;}QToolButton:focus{border-color:#58a6ff;}'))
        menu = QMenu(more)
        for label_key, key in (('teams.menu.edit', 'edit'), ('teams.menu.loadouts', 'loadouts'),
                               ('teams.menu.history', 'enhancement_details'), ('teams.menu.delete', 'delete')):
            if key == 'delete':
                menu.addSeparator()
            action = menu.addAction(catalog.render(label_key))
            action.triggered.connect(lambda checked=False, target=key: actions[target].emit(team.team_id))
        more.setMenu(menu)
        toolbar.addWidget(more, 1)
        # 状态按 1:1:2 给较长的配装说明更多空间；操作行仍等宽铺满。
        self.right = QWidget(self)
        right = QVBoxLayout(self.right)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(8)
        right.addWidget(self.summary, 1)
        right.addWidget(self.actions)
        self.matches = QLabel(self)
        self.matches.setTextFormat(Qt.PlainText)
        self.matches.setWordWrap(True)
        self.matches.setStyleSheet(themed_style('color:#8b949e;font-size:12px;'))
        self.matches.hide()
        self.labels['matches'] = self.matches
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
            self.grid.addWidget(self.matches, 2, 0, 1, 2)
        else:
            self.grid.setColumnStretch(0, 1)
            self.grid.setColumnStretch(1, 0)
            self.grid.addWidget(self.heading, 0, 0, 1, 2)
            self.grid.addWidget(self.members, 1, 0, 1, 2)
            self.grid.addWidget(self.right, 2, 0, 1, 2)
            self.grid.addWidget(self.matches, 3, 0, 1, 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow(self.width() >= 900)
