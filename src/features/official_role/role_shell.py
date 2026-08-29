# 构建只读取官方静态库与账号 SQLite 指针的新角色页面。
"""Rebuilt character page using the old UI skeleton and official data sources."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.theme import themed_style
from src.features.official_role.controller import OfficialRoleController
from src.features.official_role.dependencies import OfficialRoleDependencies
from src.services.official_role_profile_service import (
    OfficialRoleProfileUpdate,
)
from src.services.world_bonus_settings_service import WorldBonusSettings
from src.ui.persistent_tab_order import bind_persistent_tab_order
from src.ui.widgets import (
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
    match_pinyin,
)
from .role_calculation import (
    _build_damage_formula_group,
    _build_margin_group,
    _clear_layout,
    _selected_combo_data,
    _selected_fork_stage,
    _selected_growth,
)
from .role_equipment import _build_drive_summary_group
from .role_growth import (
    _build_awakening_group,
    _build_base_group,
    _build_fork_group,
    _build_skill_group,
)
from .role_weights import _build_weight_group

__all__ = ["_page_my_role", "_refresh_my_role", "confirm_pending_my_role_changes"]

_WEIGHT_PROPERTY_CHOICES = (
    ("暴击率%", "CritBase"),
    ("暴击伤害%", "CritDamageBase"),
    ("伤害增加%", "DamageUpGeneralBase"),
    ("攻击力%", "AtkUp"),
    ("攻击力", "AtkAdd"),
    ("防御力", "DefAdd"),
    ("防御力%", "DefUp"),
    ("生命值%", "HPMaxUp"),
    ("生命值", "HPMaxAdd"),
    ("环合强度", "MagBase"),
    ("倾陷强度", "UnbalIntensityBase"),
)
_WEIGHT_LABEL_BY_PROPERTY = {property_id: label for label, property_id in _WEIGHT_PROPERTY_CHOICES}


def _mark_world_bonus_dirty(window) -> None:
    window._official_role_world_bonus_dirty = True
    window._my_role_dirty = True


def _set_world_bonus_controls(window, settings: WorldBonusSettings) -> None:
    attack = getattr(window, "official_role_world_attack", None)
    crit_damage = getattr(window, "official_role_world_crit_damage", None)
    if attack is None or crit_damage is None:
        return
    attack.blockSignals(True)
    crit_damage.blockSignals(True)
    attack.setValue(int(round(settings.yaodao_attack_add)))
    crit_damage.setValue(float(settings.quantao_crit_damage) * 100.0)
    attack.blockSignals(False)
    crit_damage.blockSignals(False)


def _build_world_bonus_card(window) -> QFrame:
    settings = _role_controller(window).load_world_bonus()
    card = QFrame()
    card.setObjectName("officialRoleWorldBonusCard")
    layout = QHBoxLayout(card)
    layout.setContentsMargins(10, 3, 10, 3)
    layout.setSpacing(6)
    title = QLabel("世界加成")
    title.setObjectName("officialRoleWorldBonusTitle")
    layout.addWidget(title)

    attack = NoWheelSpinBox()
    attack.setObjectName("officialRoleWorldAttack")
    attack.setRange(0, 20)
    attack.setSingleStep(2)
    attack.setSuffix(" 攻")
    attack.setToolTip("妖刀：正式大世界加成 AtkAdd；每级 +2，满级 +20")
    attack.setFixedWidth(72)
    crit_damage = NoWheelDoubleSpinBox()
    crit_damage.setObjectName("officialRoleWorldCritDamage")
    crit_damage.setRange(0.0, 4.0)
    crit_damage.setDecimals(1)
    crit_damage.setSingleStep(0.4)
    crit_damage.setSuffix("% 爆伤")
    crit_damage.setToolTip(
        "拳套：正式大世界加成 CritDamageBase；每级 +0.4%，满级 +4%"
    )
    crit_damage.setFixedWidth(96)
    layout.addWidget(QLabel("妖刀"))
    layout.addWidget(attack)
    layout.addWidget(QLabel("拳套"))
    layout.addWidget(crit_damage)
    window.official_role_world_attack = attack
    window.official_role_world_crit_damage = crit_damage
    _set_world_bonus_controls(window, settings)
    attack.valueChanged.connect(lambda _value: _mark_world_bonus_dirty(window))
    crit_damage.valueChanged.connect(
        lambda _value: _mark_world_bonus_dirty(window)
    )
    return card


def _role_controller(window) -> OfficialRoleController:
    dependencies = OfficialRoleDependencies.from_app_context(window.app_context)
    controller = getattr(window, "_official_role_controller", None)
    if (
        not isinstance(controller, OfficialRoleController)
        or controller.dependencies != dependencies
    ):
        controller = OfficialRoleController(dependencies)
        window._official_role_controller = controller
    return controller


def _populate_role_tab(window, scroll: QScrollArea, character_id: int) -> None:
    if scroll.property("loaded"):
        return
    detail = _role_controller(window).load_detail(character_id)
    editor = {
        "detail": detail,
        "marginal_property_weights": dict(detail.get("property_weights") or {}),
        "marginal_main_property_weights": dict(detail.get("main_property_weights") or {}),
        "equipment_context_key": ("saved" if detail["equipment_contexts"]["saved"]["available"] else "current"),
    }
    window._official_role_editors[character_id] = editor
    content = QWidget()
    form = QVBoxLayout(content)
    form.setSpacing(15)
    form.setContentsMargins(15, 15, 15, 15)
    form.addWidget(_build_base_group(window, character_id, detail, editor))
    form.addWidget(_build_awakening_group(window, character_id, detail, editor))
    form.addWidget(_build_skill_group(window, character_id, detail, editor))
    form.addWidget(_build_margin_group(window, character_id, detail, editor))
    form.addWidget(_build_fork_group(window, character_id, detail, editor))
    form.addWidget(_build_drive_summary_group(window, detail, editor))
    form.addWidget(_build_damage_formula_group(detail, editor))
    form.addWidget(_build_weight_group(window, character_id, detail, editor))
    form.addSpacing(100)
    form.addStretch()
    scroll.setWidget(content)
    scroll.setProperty("loaded", True)


def _save_profiles(window, *, show_message: bool = True) -> bool:
    dirty_ids = list(getattr(window, "_official_role_dirty_ids", set()))
    world_bonus_dirty = bool(
        getattr(window, "_official_role_world_bonus_dirty", False)
    )
    if not dirty_ids and not world_bonus_dirty:
        if show_message:
            QMessageBox.information(window, "保存", "当前没有需要保存的角色修改。")
        return True
    try:
        updates = []
        for character_id in dirty_ids:
            editor = window._official_role_editors.get(character_id)
            if not editor:
                continue
            detail = editor["detail"]
            growth = _selected_growth(editor)
            if growth is None:
                raise ValueError("角色等级不在官方成长数据范围内")
            fork_id = _selected_combo_data(editor["fork"])
            selected_awaken_effect_ids = tuple(
                effect_id
                for effect_id, check in editor["awakening_checks"].items()
                if check.isChecked()
            )
            updates.append(
                OfficialRoleProfileUpdate(
                    character_id=character_id,
                    character_level=int(growth[0]),
                    breakthrough_stage=int(growth[1]),
                    awakening_level=len(selected_awaken_effect_ids),
                    selected_awaken_effect_ids=selected_awaken_effect_ids,
                    likeability_level_10_enabled=editor[
                        "likeability_level_10"
                    ].isChecked(),
                    fork_id=fork_id,
                    fork_level=editor["fork_level"].value() if fork_id else None,
                    fork_breakthrough_stage=(
                        _selected_fork_stage(editor) if fork_id else None
                    ),
                    fork_refinement_level=(int(editor["refinement"].currentData() or 1) if fork_id else None),
                    # 兼容账号 schema；角色页计算不再读取这个历史指针。
                    selected_skill_id=detail["profile"].get("selected_skill_id"),
                    skill_levels=dict(editor["skill_levels"]),
                    ordinal=int(detail["profile"].get("ordinal") or 0),
                )
            )
        if updates:
            _role_controller(window).save_profiles(updates)
        if world_bonus_dirty:
            _role_controller(window).save_world_bonus(
                WorldBonusSettings(
                    yaodao_attack_add=float(
                        window.official_role_world_attack.value()
                    ),
                    quantao_crit_damage=float(
                        window.official_role_world_crit_damage.value()
                    )
                    / 100.0,
                )
            )
    except Exception as exc:
        QMessageBox.warning(window, "保存失败", str(exc))
        return False
    window._official_role_dirty_ids.clear()
    window._official_role_world_bonus_dirty = False
    window._my_role_dirty = False
    if show_message:
        QMessageBox.information(
            window,
            "保存",
            "角色养成指针与世界加成已保存到当前账号数据库。",
        )
    _refresh_my_role(window)
    return True


def _reload_current_role_tab(window, character_id: int) -> None:
    tabs = getattr(window, "official_role_tabs", None)
    if tabs is None:
        return
    index = next(
        (tab_index for tab_index in range(tabs.count()) if int(tabs.tabBar().tabData(tab_index)) == int(character_id)),
        -1,
    )
    if index < 0:
        return
    scroll = tabs.widget(index)
    old = scroll.takeWidget()
    if old is not None:
        old.deleteLater()
    scroll.setProperty("loaded", False)
    window._official_role_editors.pop(character_id, None)
    window._official_role_dirty_ids.discard(character_id)
    window._my_role_dirty = bool(window._official_role_dirty_ids) or bool(
        getattr(window, "_official_role_world_bonus_dirty", False)
    )
    _populate_role_tab(window, scroll, character_id)


def _reset_current_role(window) -> None:
    tabs = getattr(window, "official_role_tabs", None)
    if tabs is None or tabs.currentIndex() < 0:
        return
    character_id = int(tabs.tabBar().tabData(tabs.currentIndex()))
    answer = QMessageBox.question(
        window,
        "重置当前角色",
        "将当前角色的等级、觉醒、技能和弧盘恢复为公共模板。\n官方额外形状始终读取静态库，账号基础权重不会重置，是否继续？",
        QMessageBox.Yes | QMessageBox.Cancel,
        QMessageBox.Cancel,
    )
    if answer != QMessageBox.Yes:
        return
    try:
        _role_controller(window).reset_profile(character_id)
    except Exception as exc:
        QMessageBox.warning(window, "重置失败", str(exc))
        return
    _reload_current_role_tab(window, character_id)
    QMessageBox.information(window, "已重置", "当前角色与弧盘已恢复为公共模板。")


def _reset_all_roles(window) -> None:
    answer = QMessageBox.question(
        window,
        "重置全部角色",
        "将当前账号所有角色的等级、觉醒、技能和弧盘恢复为公共模板。\n官方额外形状始终读取静态库，账号基础权重不会重置，是否继续？",
        QMessageBox.Yes | QMessageBox.Cancel,
        QMessageBox.Cancel,
    )
    if answer != QMessageBox.Yes:
        return
    try:
        count = _role_controller(window).reset_all_profiles()
    except Exception as exc:
        QMessageBox.warning(window, "重置失败", str(exc))
        return
    window._official_role_dirty_ids.clear()
    window._my_role_dirty = bool(
        getattr(window, "_official_role_world_bonus_dirty", False)
    )
    _refresh_my_role(window)
    QMessageBox.information(window, "已重置", f"已将 {count} 个角色与弧盘恢复为公共模板。")


def _page_my_role(window) -> QWidget:
    page = QWidget()
    root = QVBoxLayout(page)
    root.setContentsMargins(20, 16, 20, 16)
    root.setSpacing(10)
    page.setStyleSheet(
        themed_style(
            """
        QLabel{font-size:14px}
        QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox{font-size:14px;padding:8px 11px;border-radius:7px}
        QPushButton{font-size:13px;padding:8px 15px;border-radius:7px}
        QTabBar::tab{font-size:13px;padding:10px 20px}
        QGroupBox{font-size:15px;border:1px solid #30363d;border-radius:10px;padding:24px;padding-top:36px}
        QFrame#officialRoleWorldBonusCard{border:1px solid #30363d;border-radius:8px}
        QLabel#officialRoleWorldBonusTitle{font-weight:bold;color:#58a6ff}
        """
        )
    )
    header = QHBoxLayout()
    search = QLineEdit()
    search.setObjectName("officialRoleSearch")
    search.setPlaceholderText("搜索角色（支持拼音）...")
    search.setClearButtonEnabled(True)
    header.addWidget(search, 1)
    reset_current = QPushButton("重置当前")
    reset_current.setObjectName("btnDanger")
    reset_current.setToolTip("将当前角色和弧盘恢复为公共模板；官方额外形状读取静态库，保留基础权重")
    reset_current.clicked.connect(lambda: _reset_current_role(window))
    reset_all = QPushButton("重置所有")
    reset_all.setObjectName("btnDanger")
    reset_all.setToolTip("将本账号所有角色和弧盘恢复为公共模板；官方额外形状读取静态库，保留基础权重")
    reset_all.clicked.connect(lambda: _reset_all_roles(window))
    save = QPushButton("保存")
    save.setObjectName("btnPrimary")
    save.clicked.connect(lambda: _save_profiles(window))
    blueprint = QPushButton("角色图纸")
    blueprint.setToolTip("查看角色套装形状与可用图纸方案")
    blueprint.clicked.connect(lambda: window._go("blueprint"))
    base_weights = QPushButton("基础权重")
    base_weights.setToolTip("编辑当前账号角色基础权重；官方额外形状只读静态库，自创角色额外形状可编辑")
    base_weights.clicked.connect(lambda: window._go("config"))
    header.addWidget(_build_world_bonus_card(window))
    header.addWidget(blueprint)
    header.addWidget(base_weights)
    header.addWidget(reset_current)
    header.addWidget(reset_all)
    header.addWidget(save)
    root.addLayout(header)

    area = QScrollArea()
    area.setWidgetResizable(True)
    content = QWidget()
    content_layout = QVBoxLayout(content)
    area.setWidget(content)
    root.addWidget(area, 1)
    window.my_role_form_area = area
    window.my_role_form_widget = content
    window.my_role_form_layout = content_layout
    window._official_role_page = page
    window.official_role_search = search
    window._official_role_dirty_ids = set()
    window._official_role_world_bonus_dirty = False
    window._official_role_editors = {}
    window._my_role_dirty = False
    _refresh_my_role(window)
    return page


def _refresh_my_role(window, *, restore_scroll_value: int | None = None) -> None:
    layout = getattr(window, "my_role_form_layout", None)
    if layout is None:
        return
    current_id = getattr(window, "_current_official_role_id", None)
    if not getattr(window, "_official_role_world_bonus_dirty", False):
        _set_world_bonus_controls(
            window,
            _role_controller(window).load_world_bonus(),
        )
    _clear_layout(layout)
    window._official_role_editors = {}
    roles = _role_controller(window).load_index()
    if not roles:
        layout.addWidget(QLabel("暂无官方角色数据。"))
        return

    search = getattr(window, "official_role_search", None)
    if not isinstance(search, QLineEdit):
        return
    tabs = QTabWidget()
    tabs.setObjectName("officialRoleTabs")
    tab_ids = {}
    for role in roles:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setProperty("loaded", False)
        character_id = int(role["character_id"])
        index = tabs.addTab(scroll, str(role.get("name_zh") or character_id))
        tabs.tabBar().setTabData(index, character_id)
        tab_ids[character_id] = index

    window._official_role_tab_order_binding = bind_persistent_tab_order(
        tabs,
        item_id_at=lambda index: int(tabs.tabBar().tabData(index)),
        save_order=lambda character_ids: _role_controller(window).save_tab_order(
            tuple(int(character_id) for character_id in character_ids)
        ),
        on_error=lambda exc: QMessageBox.warning(
            window,
            "保存角色顺序失败",
            str(exc),
        ),
    )

    def load_visible(index: int) -> None:
        if index < 0:
            return
        character_id = int(tabs.tabBar().tabData(index))
        window._current_official_role_id = character_id
        _populate_role_tab(window, tabs.widget(index), character_id)

    def filter_tabs(text: str = "") -> None:
        keyword = text.strip()
        for index in range(tabs.count()):
            tabs.setTabVisible(index, not keyword or match_pinyin(tabs.tabText(index), keyword))

    tabs.currentChanged.connect(load_visible)
    previous_filter = getattr(window, "_official_role_search_filter", None)
    previous_search = getattr(window, "_official_role_search_filter_widget", None)
    if previous_filter is not None and previous_search is search:
        try:
            search.textChanged.disconnect(previous_filter)
        except (RuntimeError, TypeError):
            pass
    search.textChanged.connect(filter_tabs)
    window._official_role_search_filter = filter_tabs
    window._official_role_search_filter_widget = search
    wanted_index = tab_ids.get(current_id, 0)
    tabs.setCurrentIndex(wanted_index)
    load_visible(tabs.currentIndex())
    window.official_role_tabs = tabs
    layout.addWidget(tabs)
    if restore_scroll_value is not None:

        def restore_scroll() -> None:
            current_scroll = tabs.currentWidget()
            if isinstance(current_scroll, QScrollArea):
                current_scroll.verticalScrollBar().setValue(int(restore_scroll_value))

        # The tab content computes its height after it is attached to the
        # page, so restore on the next event-loop turn instead of clamping to 0.
        QTimer.singleShot(0, restore_scroll)


def confirm_pending_my_role_changes(window) -> bool:
    if not getattr(window, "_my_role_dirty", False):
        return True
    answer = QMessageBox.question(
        window,
        "未保存角色状态",
        "角色养成指针或世界加成有未保存修改，是否先保存？",
        QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        QMessageBox.Save,
    )
    if answer == QMessageBox.Cancel:
        _role_controller(window).log_dirty_exit(
            "cancel", len(getattr(window, "_official_role_dirty_ids", set()))
        )
        return False
    if answer == QMessageBox.Save:
        _role_controller(window).log_dirty_exit(
            "save", len(getattr(window, "_official_role_dirty_ids", set()))
        )
        return _save_profiles(window, show_message=False)
    _role_controller(window).log_dirty_exit(
        "discard", len(getattr(window, "_official_role_dirty_ids", set()))
    )
    window._official_role_dirty_ids.clear()
    window._official_role_world_bonus_dirty = False
    _set_world_bonus_controls(
        window,
        _role_controller(window).load_world_bonus(),
    )
    window._my_role_dirty = False
    return True
