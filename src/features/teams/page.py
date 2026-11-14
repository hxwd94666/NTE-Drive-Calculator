# 展示本账号独立队伍列表，成员选择与重命名共用编辑弹窗。
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from src.domain.names.name_search import match_pinyin
from src.domain.allocation.damage_display import team_gain_ratio
from src.features.teams.team_card import TeamCard
from src.domain.teams.overview import SourceMode
from src.ui.common.text_catalog import ElidedTextLabel, plain_tooltip, resolve_text_catalog
from src.domain.teams.enhancement_presentation import team_enhancement_presentation


class TeamDevelopmentNotice(QWidget):
    """队伍专属单行提示；窄窗省略但保留完整提示，不挤掉账号操作。"""
    def __init__(self, parent=None, *, catalog=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 0, 0, 0)
        self.label = ElidedTextLabel(resolve_text_catalog(catalog).render('teams.page.development_notice'), self)
        self.label.setObjectName('statusBadge')
        self.label.setProperty('tone', 'error')
        self.label.setWordWrap(False)
        layout.addWidget(self.label)


class TeamPage(QWidget):
    create_requested = Signal()
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    loadouts_requested = Signal(str)
    enhancement_details_requested = Signal(str)
    graduation_requested = Signal(str)
    calculation_requested = Signal(str)

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
        title = QLabel(self.text("teams.page.heading"))
        title.setStyleSheet("font-size:20px;font-weight:700")
        header.addWidget(title)
        header.addStretch()
        self._header_actions = QWidget()
        buttons = QHBoxLayout(self._header_actions)
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(8)
        create = QPushButton(self.text("teams.page.create"))
        create.setObjectName("btnNew")
        create.clicked.connect(self.create_requested.emit)
        buttons.addWidget(create)
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.text("teams.page.search"))
        self.search.setAccessibleName(self.text("teams.page.search_accessible"))
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
        self.empty = QLabel(self.text("teams.page.empty"))
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
        actions = dict(edit=self.edit_requested, delete=self.delete_requested, graduation=self.graduation_requested,
                       calculate=self.calculation_requested,
                       loadouts=self.loadouts_requested,
                       enhancement_details=self.enhancement_details_requested)
        for team in self._teams:
            card = TeamCard(team, self._roles, actions=actions, parent=self.content, catalog=self.text_catalog)
            self._overview_labels[team.team_id] = card.labels
            self._cards.append((card, card.names))
            self._card_keys[card] = team.team_id
            self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)
        self.status.setText(self.text("teams.page.count", count=len(self._teams)))
        self._filter()

    def role_presentations(self):
        return dict(self._roles)

    def show_overview(self, overview):
        if overview.team not in self._teams:
            return
        labels = self._overview_labels[overview.team.team_id]
        gaps = tuple(dict.fromkeys((*overview.issues, *(issue for member in overview.members for issue in member.issues))))
        source = self.text("teams.card.source_calc_short" if overview.source.mode == SourceMode.CALC else "teams.card.source_game_short")
        labels["source"].setText(self.text("teams.loadout.overview", source=source, ready=overview.ready_count))
        labels["source"].setToolTip(plain_tooltip("；".join(gaps) + "\n" + self.text("teams.loadout.overview_hint")))
        saved = overview.latest_enhancement
        if saved is None:
            labels["enhancement"].setText(self.text("teams.card.enhancement_empty"))
            labels["enhancement"].setToolTip(plain_tooltip(self.text("teams.card.history_hint")))
        else:
            shown = team_enhancement_presentation(saved.payload)
            if saved.payload["result_kind"] == "baseline":
                headline = self.text("teams.card.enhancement_baseline")
            else:
                gain = team_gain_ratio(saved.payload["baseline_value"], saved.payload["candidate_value"])
                key = ("teams.card.enhancement_zero" if gain == 0 else
                       "teams.card.enhancement_tiny_gain" if 0 < gain < .00005 else
                       "teams.card.enhancement_tiny_loss" if -.00005 < gain < 0 else None)
                headline = self.text(key) if key else self.text("teams.card.enhancement_value", gain=f"{gain:+.2%}")
            labels["enhancement"].setText(headline)
            labels["enhancement"].setToolTip(plain_tooltip("\n".join((shown.headline, shown.coverage,
                shown.source, shown.search, "；".join(saved.association), self.text("teams.card.history_hint")))))

    def visible_team_ids(self):
        return tuple(self._card_keys[card] for card, _ in self._cards if not card.visibleRegion().isEmpty())

    def show_overview_error(self, team_id, message):
        labels = self._overview_labels.get(team_id)
        if labels:
            labels["source"].setText(self.text("teams.card.source_unverified"))
            labels["source"].setToolTip(plain_tooltip(self.text("teams.card.read_retry", reason=message)))
            # 同一批简报同时读取增强记录，读取失败不能把上轮增强当作本轮已核对。
            labels["enhancement"].setText(self.text("teams.card.enhancement_unverified"))
            labels["enhancement"].setToolTip(plain_tooltip(self.text("teams.card.read_retry", reason=message)))

    def show_axis_graduation(self, team, display):
        if team in self._teams:
            status = display.summary_status
            if display.ratio_percent is None:
                key = "teams.card.graduation_unset" if status == "unset" else "teams.card.graduation_unknown"
                headline = self.text(key)
            else:
                suffix_key = {
                    "benchmark_stale": "teams.card.graduation_rebuild",
                    "current_stale": "teams.card.graduation_stale",
                    "component_pending": "teams.card.graduation_component",
                    "validity_unverified": "teams.card.graduation_unverified",
                }.get(status, "teams.card.graduation_historical" if display.historical else "teams.card.graduation_fixed")
                headline = self.text("teams.card.graduation_value", percent=f"{display.ratio_percent:.2f}",
                                     state=self.text(suffix_key))
            self._overview_labels[team.team_id]["axis_graduation"].setText(headline)
            self._overview_labels[team.team_id]["axis_graduation"].setToolTip(
                plain_tooltip("\n".join((display.headline, display.damage, display.scope,
                             self.text("teams.card.graduation_hint")))))

    def _filter(self):
        query = self.search.text().strip()
        count = 0
        for card, names in self._cards:
            visible = not query or any(match_pinyin(name, query) for name in names)
            card.setVisible(visible)
            count += int(visible)
        self.empty.setVisible(count == 0)
        self.empty.setText(self.text("teams.page.no_match") if self._teams else self.text("teams.page.empty"))
