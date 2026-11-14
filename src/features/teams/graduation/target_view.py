# 毕业目标按冻结字段展示版本、预设和角色养成；完整参数折叠保留，切换预设只影响本窗展示。
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.features.teams.dialog_support import LocalizedDisclosure
from src.features.teams.widgets import ResponsiveGrid
from src.features.teams.graduation.flow_text import FlowReadOnlyText
from src.ui.common.image_scaling import asset_pixmap
from src.ui.common.text_catalog import plain_label, plain_tooltip, resolve_text_catalog
from src.ui.roles.role_portrait import custom_role_portrait, temporary_role_portrait


class GraduationTargetView(QWidget):
    def __init__(self, *, roles=None, catalog=None, parent=None):
        super().__init__(parent)
        self._roles = dict(roles or {})
        self._catalog = resolve_text_catalog(catalog)
        self.text = self._catalog.render
        self._payload = None
        self._signature = None
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)
        root.setAlignment(Qt.AlignTop)
        heading = QHBoxLayout()
        self.version = plain_label('')
        self.version.setStyleSheet('font-size:16px;font-weight:700;')
        self.version.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        heading.addWidget(self.version, 1)
        self.preset = QComboBox()
        self.preset.setAccessibleName(self.text('teams.target_view.preset_accessible'))
        self.preset.setToolTip(plain_tooltip(self.text('teams.target_view.preset_hint')))
        self.preset.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.preset.setMinimumContentsLength(8)
        self.preset.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.preset.setMaximumWidth(240)
        self.preset.currentIndexChanged.connect(self._show_preset)
        heading.addWidget(self.preset)
        root.addLayout(heading)
        self.summary = FlowReadOnlyText()
        self.summary.setStyleSheet(themed_style('color:#58a6ff;font-size:14px;font-weight:600;'))
        root.addWidget(self.summary)
        self.notice = FlowReadOnlyText(self.text('teams.target_view.notice'))
        self.notice.setStyleSheet(themed_style('color:#8b949e;font-size:12px;'))
        root.addWidget(self.notice)
        self.members = QWidget()
        self.member_layout = QVBoxLayout(self.members)
        self.member_layout.setContentsMargins(0, 0, 0, 0)
        self.member_layout.setSpacing(0)
        root.addWidget(self.members)
        self.world = FlowReadOnlyText()
        root.addWidget(self.world)
        self.details = FlowReadOnlyText()
        self.details.setStyleSheet(themed_style('color:#8b949e;font-size:12px;'))
        self.details_section = LocalizedDisclosure(self.text('teams.target_view.details'), self.details,
                                                  catalog=self._catalog)
        root.addWidget(self.details_section)
        self.set_target(None, '')

    def set_target(self, payload, description):
        # 已验证目标的独立展示副本；不从中文诊断反解析字段，也不回写目标或历史。
        frozen = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True) if payload is not None else None
        signature = (frozen, description)
        if signature == self._signature:
            return
        self._signature = signature
        previous = self.preset.currentData()
        self._payload = json.loads(frozen) if frozen is not None else None
        self.details.setText(description)
        self.details_section.setVisible(bool(description))
        self.preset.blockSignals(True)
        self.preset.clear()
        if self._payload is not None:
            for index, preset in enumerate(self._payload['presets'], 1):
                self.preset.addItem(self.text('teams.target_view.preset', index=index), preset['preset_id'])
            current = self.preset.findData(previous)
            self.preset.setCurrentIndex(max(0, current))
        self.preset.blockSignals(False)
        self.preset.setVisible(self.preset.count() > 1)
        self._show_preset()

    def _show_preset(self, *_args):
        while self.member_layout.count():
            item = self.member_layout.takeAt(0)
            if item.widget() is not None:
                item.widget().hide()
                item.widget().deleteLater()
        for widget in (self.summary, self.world, self.members):
            widget.setVisible(self._payload is not None)
        if self._payload is None:
            self.version.setText(self.text('teams.target_view.legacy' if self.details.text() else 'teams.target_view.no_target'))
            self.summary.clear()
            self.world.clear()
            return
        payload = self._payload
        preset = next(row for row in payload['presets'] if row['preset_id'] == self.preset.currentData())
        self.version.setText(self.text('teams.target_view.version', revision=payload['revision']))
        self.summary.setText(self.text('teams.target_view.summary', members=len(preset['members']),
            cores=len(preset['layouts']), drives=sum(len(row['pieces']) for row in preset['layouts'])))
        layouts = {row['character_id']: row for row in preset['layouts']}
        rows = [self._member(member, layouts[member['character_id']]) for member in preset['members']]
        self.member_layout.addWidget(ResponsiveGrid(rows, columns=2, minimum=280))
        world = payload['world_bonus']
        self.world.setText(self.text('teams.target_view.world', attack=f"{world['yaodao_attack_add']:g}",
            crit=f"{100 * world['quantao_crit_damage']:g}"))
        self.updateGeometry()

    def _member(self, member, layout):
        row = QWidget()
        outer = QVBoxLayout(row)
        outer.setContentsMargins(0, 10, 0, 0)
        outer.setSpacing(6)
        header = QHBoxLayout()
        role = self._roles.get(member['character_id'])
        name = role.name if role else self.text('teams.target_view.role', id=member['character_id'])
        avatar = QLabel()
        avatar.setFixedSize(32, 32)
        avatar.setAccessibleName(self.text('selector.portrait', role=name))
        dpr = self.devicePixelRatioF()
        if role and role.icon_path and not role.custom:
            picture = (temporary_role_portrait(role.icon_path, 32, dpr) if role.icon_note
                       else asset_pixmap(role.icon_path, 32, dpr))
        else:
            picture = None
        avatar.setPixmap(picture if picture is not None and not picture.isNull() else custom_role_portrait(32, dpr))
        if role and role.icon_note:
            avatar.setToolTip(plain_tooltip(role.icon_note))
        header.addWidget(avatar)
        title = FlowReadOnlyText(name)
        title.setStyleSheet('font-size:14px;font-weight:600;')
        header.addWidget(title, 1)
        growth = FlowReadOnlyText(self.text('teams.target_view.growth', level=member['character_level'],
            stage=member['breakthrough_stage'], awake=member['awakening_level']))
        growth.setStyleSheet(themed_style('color:#58a6ff;font-weight:600;'))
        header.addWidget(growth, 2)
        outer.addLayout(header)
        fork = member['fork']
        fork_text = self.text('teams.target_view.fork', level=fork['level'], stage=fork['breakthrough_stage'],
                              refinement=fork['refinement_level']) if fork else self.text('teams.target_view.no_fork')
        like = self.text('teams.target_view.like_on' if member['likeability_level_10_enabled'] else 'teams.target_view.like_off')
        details = FlowReadOnlyText(fork_text + ' · ' + like)
        outer.addWidget(details)
        levels = ' / '.join(str(skill['level']) for skill in member['skill_levels'])
        set_text = (self.text('teams.target_view.set_count', count=layout['minimum_set_count'])
                    if layout['minimum_set_count'] else self.text('teams.target_view.no_set_limit'))
        values = FlowReadOnlyText(self.text('teams.target_view.member_summary', levels=levels,
            drives=len(layout['pieces']), suit=set_text))
        values.setToolTip(plain_tooltip('\n'.join(f"{skill['skill_id']}: {skill['level']}" for skill in member['skill_levels'])))
        values.setStyleSheet(themed_style('color:#8b949e;font-size:12px;'))
        outer.addWidget(values)
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet(themed_style('color:#30363d;'))
        outer.addWidget(line)
        return row
