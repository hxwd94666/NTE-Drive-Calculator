# 角色页家具三项共用同一冻结设置，后台静默回填不触发编辑或保存。
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QAbstractSpinBox, QFrame, QHBoxLayout

from src.domain.official_role.models import WorldBonusSettings
from src.ui.common.text_catalog import plain_label, plain_tooltip, resolve_text_catalog
from src.ui.common.widgets import NoWheelDoubleSpinBox, NoWheelSpinBox


def world_bonus_controls(window):
    return tuple(control for name in ('attack', 'crit_damage', 'defense')
                 if (control := getattr(window, f'official_role_world_{name}', None)) is not None)


def set_world_bonus_controls(window, settings):
    for name, value in (('attack', settings.yaodao_attack_add),
                        ('crit_damage', settings.quantao_crit_damage * 100),
                        ('defense', settings.kaijia_defense_add)):
        control = getattr(window, f'official_role_world_{name}', None)
        if control is not None:
            blocker = QSignalBlocker(control)
            control.setValue(value if name == 'crit_damage' else int(round(value)))
            del blocker


def read_world_bonus_controls(window):
    defense = getattr(window, 'official_role_world_defense', None)
    return WorldBonusSettings(float(window.official_role_world_attack.value()),
        float(window.official_role_world_crit_damage.value()) / 100,
        float(defense.value()) if defense is not None else 30.)


def apply_world_bonus_display(window, settings):
    window._official_role_saved_world_bonus = settings
    if getattr(window, '_official_role_world_bonus_dirty', False):
        return
    set_world_bonus_controls(window, settings)
    if not getattr(window, '_my_role_dirty', False):
        return  # 干净页面由既有完整刷新重建，避免额外启动一轮马上作废的计算。
    from src.features.official_role.role_calculation import _refresh_role_calculations
    tabs = getattr(window, 'official_role_tabs', None)
    current = tabs.tabBar().tabData(tabs.currentIndex()) if tabs is not None and tabs.currentIndex() >= 0 else None
    for character_id, editor in getattr(window, '_official_role_editors', {}).items():
        detail = editor['detail']
        if detail.get('world_bonus') == settings.to_payload():
            continue
        detail['world_bonus'] = settings.to_payload()
        detail.pop('_role_damage_values', None)
        editor['furniture_recalc_pending'] = True
        if character_id == current:
            editor.pop('furniture_recalc_pending')
            _refresh_role_calculations(editor)


def build_world_bonus_card(window):
    text = resolve_text_catalog(getattr(window, 'text_catalog', None)).render
    prefix = 'roles.page.build_world_bonus_card'
    card = QFrame()
    card.setObjectName('officialRoleWorldBonusCard')
    layout = QHBoxLayout(card)
    layout.setContentsMargins(8, 0, 8, 0)
    layout.setSpacing(3)
    title = plain_label(text(f'{prefix}.title'))
    title.setObjectName('officialRoleWorldBonusTitle')
    title.setWordWrap(True)
    layout.addWidget(title)

    def changed(_value):
        window._official_role_world_bonus_dirty = True
        window._my_role_dirty = True

    for name, percent, maximum, step in (
        ('attack', False, 20, 2), ('crit_damage', True, 4, .4), ('defense', False, 30, 3),
    ):
        control = NoWheelDoubleSpinBox() if percent else NoWheelSpinBox()
        control.setObjectName('officialRoleWorld' + ''.join(word.title() for word in name.split('_')))
        control.setRange(0, maximum)
        control.setSingleStep(step)
        if percent:
            control.setDecimals(1)
        control.setButtonSymbols(QAbstractSpinBox.NoButtons)
        control.setToolTip(plain_tooltip(text(f'{prefix}.{name}.hint')))
        control.setAccessibleName(text(f'{prefix}.{name}_unit'))
        control.setMinimumWidth(50)
        control.setMaximumWidth(65)
        control.setMinimumHeight(29)
        control.setStyleSheet('padding:1px 4px')
        label = plain_label(text(f'{prefix}.{name}_unit'))
        label.setWordWrap(True)
        label.setBuddy(control)
        layout.addWidget(control)
        layout.addWidget(label)
        setattr(window, f'official_role_world_{name}', control)
        control.setEnabled(False)
        control.valueChanged.connect(changed)
        # 同值重输也属于明确手动设置；程序回填不触发 textEdited。
        control.lineEdit().textEdited.connect(changed)
    set_world_bonus_controls(window, WorldBonusSettings())
    return card
