# 展示本账号独立队伍列表，成员选择与重命名共用编辑弹窗。
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from src.domain.name_search import match_pinyin
from src.features.teams.team_card import TeamCard
from src.domain.teams.overview import SourceMode
from src.ui.text_catalog import plain_tooltip, resolve_text_catalog
from src.domain.teams.enhancement_presentation import team_enhancement_presentation


class TeamPage(QWidget):
    create_requested = Signal()
    refresh_requested = Signal()
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    match_requested = Signal(str)
    enhancement_requested = Signal(str)
    loadouts_requested = Signal(str)
    enhancement_details_requested = Signal(str)
    graduation_requested = Signal(str)

    def __init__(self, parent=None, *, catalog=None):
        super().__init__(parent)
        self.text_catalog = resolve_text_catalog(catalog)
        self.text = self.text_catalog.render
        self._teams = ()
        self._roles = {}
        self._cards = []
        self._overview_labels = {}
        self._card_keys = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)
        self._header = QGridLayout()
        self._header.setContentsMargins(0, 0, 0, 0)
        self._header.setSpacing(12)
        root.addLayout(self._header)
        self._heading = QWidget()
        header = QHBoxLayout(self._heading)
        header.setContentsMargins(0, 0, 0, 0)
        title = QLabel("我的队伍")
        title.setStyleSheet("font-size:20px;font-weight:700")
        header.addWidget(title)
        header.addStretch()
        refresh = QPushButton("刷新")
        refresh.setToolTip("更新配装与已保存结果，不开始计算")
        refresh.setAccessibleName("刷新当前账号队伍简报")
        refresh.clicked.connect(self.refresh_requested.emit)
        self._header_actions = QWidget()
        buttons = QHBoxLayout(self._header_actions)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        buttons.addWidget(refresh)
        create = QPushButton("＋ 新建队伍")
        create.setObjectName("btnNew")
        create.clicked.connect(self.create_requested.emit)
        buttons.addWidget(create)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索队伍名称或成员（支持拼音）…")
        self.search.setAccessibleName("搜索队伍")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.status = QLabel()
        self.status.setTextFormat(Qt.PlainText)
        self.status.setWordWrap(True)
        header.insertWidget(1, self.status)
        self.status.setStyleSheet("font-size:12px;")
        self._header_wide = None
        self._reflow_header(False)
        scroll = QScrollArea()
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.content = QWidget()
        self.cards_layout = QVBoxLayout(self.content)
        self.cards_layout.setContentsMargins(0, 0, 8, 0)
        self.cards_layout.setSpacing(12)
        self.cards_layout.addStretch()
        scroll.setWidget(self.content)
        root.addWidget(scroll, 1)
        self.empty = QLabel("还没有队伍，点击右上角「新建队伍」开始配置。")
        self.empty.setWordWrap(True)
        self.cards_layout.insertWidget(0, self.empty)

    def _reflow_header(self, wide):
        if self._header_wide == wide:
            return
        self._header_wide = wide
        while self._header.count():
            self._header.takeAt(0)
        for column in range(3):
            self._header.setColumnStretch(column, 0)
        self._header.setColumnStretch(0, 1)
        self._header.addWidget(self._heading, 0, 0)
        if wide:
            self.search.setMinimumWidth(260)
            self.search.setMaximumWidth(340)
            self._header.addWidget(self.search, 0, 1)
            self._header.addWidget(self._header_actions, 0, 2)
        else:
            self.search.setMinimumWidth(0)
            self.search.setMaximumWidth(16777215)
            self._header.addWidget(self._header_actions, 0, 1)
            self._header.addWidget(self.search, 1, 0, 1, 2)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow_header(self.width() >= 900)

    def clear_account(self):
        self.search.clear()
        self.show_teams((), ())
        self.status.clear()

    def show_teams(self, teams, roles):
        self._teams = tuple(teams)
        self._roles = {role.character_id: role for role in roles}
        for widget, _ in self._cards:
            self.cards_layout.removeWidget(widget)
            widget.hide()
            widget.deleteLater()
        self._cards.clear()
        self._card_keys.clear()
        self._overview_labels.clear()
        actions = dict(edit=self.edit_requested, delete=self.delete_requested, match=self.match_requested,
                       enhancement=self.enhancement_requested, graduation=self.graduation_requested,
                       loadouts=self.loadouts_requested,
                       enhancement_details=self.enhancement_details_requested)
        for team in self._teams:
            card = TeamCard(team, self._roles, actions=actions, parent=self.content, catalog=self.text_catalog)
            self._overview_labels[team.team_id] = card.labels
            self._cards.append((card, card.names))
            self._card_keys[card] = team.team_id
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.status.setText(f"{len(self._teams)} 支队伍")
        self._filter()

    def role_presentations(self):
        return dict(self._roles)

    def show_overview(self, overview):
        if overview.team not in self._teams:
            return
        labels = self._overview_labels[overview.team.team_id]
        gaps = tuple(dict.fromkeys((*overview.issues, *(issue for member in overview.members for issue in member.issues))))
        source = self.text("teams.loadout.source_calc" if overview.source.mode == SourceMode.CALC else "teams.loadout.source_game")
        labels["source"].setText(self.text("teams.loadout.overview", source=source, ready=overview.ready_count)
                                 + (self.text("teams.loadout.overview_gap") if gaps else ""))
        labels["source"].setToolTip(plain_tooltip("；".join(gaps) + "\n" + self.text("teams.loadout.overview_hint")))
        saved = overview.latest_enhancement
        if saved is None:
            labels["enhancement"].setText("增强：暂无已保存结果")
        else:
            shown = team_enhancement_presentation(saved.payload)
            labels["enhancement"].setText("增强：" + shown.headline + " · " + shown.coverage)
            labels["enhancement"].setToolTip("\n".join((shown.source, shown.search, "；".join(saved.association),
                                                       "在更多 → 增强记录中查看详情。")))

    def visible_team_ids(self):
        return tuple(self._card_keys[card] for card, _ in self._cards if not card.visibleRegion().isEmpty())

    def show_overview_error(self, team_id, message):
        labels = self._overview_labels.get(team_id)
        if labels:
            labels["source"].setText("简报待刷新 · " + message)
            labels["source"].setToolTip("本次读取未完成，原结果可能已过期。点击刷新重试。\n" + message)

    def show_match_counts(self, team, counts):
        if team not in self._teams:
            return
        matched, read = counts
        self._overview_labels[team.team_id]["matches"].show()
        self._overview_labels[team.team_id]["matches"].setText(
            f"已找到 {matched} 个匹配范围 · 已读取 {read} 个" if matched is not None
            else f"匹配数量未确认 · 已读取 {read} 个范围")

    def show_axis_graduation(self, team, display):
        if team in self._teams:
            self._overview_labels[team.team_id]["axis_graduation"].setText(
                display.headline)
            self._overview_labels[team.team_id]["axis_graduation"].setToolTip(
                "\n".join((display.headline, display.damage, display.scope,
                             "固定轴假设；实战可执行性未证实。详情见毕业率。")))

    def _filter(self):
        query = self.search.text().strip()
        count = 0
        for card, names in self._cards:
            visible = not query or any(match_pinyin(name, query) for name in names)
            card.setVisible(visible)
            count += int(visible)
        self.empty.setVisible(count == 0)
        self.empty.setText("没有匹配的队伍" if self._teams else "还没有队伍，点击右上角「新建队伍」开始配置。")
