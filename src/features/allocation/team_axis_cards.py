# 队伍增强卡片只保存展示草稿，候选来自共用正式匹配入口。
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from src.domain.teams.models import TeamRole
from src.features.teams.cards import MemberSlots, set_role
from src.integrations.bundled_resources import bundled_game_ui_asset_root
from src.integrations.game_assets.catalog import GameUiAssetCatalog


def build_team_axis_card(team, names):
    card = QWidget()
    card.setObjectName("axisTeamCard")
    layout = QVBoxLayout(card)
    check = QCheckBox(team.name)
    check.setAccessibleName(f"选择队伍 {team.name}")
    layout.addWidget(check)
    slots = MemberSlots(card)
    assets = GameUiAssetCatalog(bundled_game_ui_asset_root())
    for index, button in enumerate(slots.buttons):
        if index < len(team.character_ids):
            key = team.character_ids[index]
            set_role(button, TeamRole(key, names.get(key, f"角色 {key}"), str(assets.character_icon(key) or "")))
        button.clicked.connect(check.toggle)
    layout.addWidget(slots)
    reason = QLabel()
    reason.setTextFormat(Qt.PlainText)
    reason.setWordWrap(True)
    layout.addWidget(reason)
    config = QWidget()
    form = QVBoxLayout(config)
    report = QLabel("尚未选择战报范围")
    report.setTextFormat(Qt.PlainText)
    report.setWordWrap(True)
    form.addWidget(report)
    actions = QHBoxLayout()
    choose = QPushButton("选择匹配战报")
    choose.setAccessibleName(f"为{team.name}选择匹配战报")
    anchor = QComboBox()
    anchor.setAccessibleName(f"{team.name}的角色养成来源")
    anchor.addItem("战报原始配置", "original")
    actions.addWidget(choose)
    actions.addWidget(anchor)
    form.addLayout(actions)
    layout.addWidget(config)
    return dict(team=team, card=card, check=check, slots=slots, config=config, reason=reason,
                report=report, anchor=anchor, choose=choose, match=None)
