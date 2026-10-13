# 构建只读取官方静态库与账号 SQLite 指针的新角色页面。
"""Rebuilt character page using the old UI skeleton and official data sources."""

from __future__ import annotations

from time import perf_counter

from PySide6.QtCore import QTimer
from shiboken6 import isValid
from PySide6.QtWidgets import (
    QAbstractSpinBox,
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
from src.features.official_role.sync_controller import RoleSyncResultText
from src.services.official_role.profiles import (
    OfficialRoleProfileUpdate,
)
from src.domain.official_role.models import WorldBonusSettings
from src.ui.persistent_tab_order import bind_persistent_tab_order
from src.utils.logger import logger
from src.utils.perf import log_perf
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
from .role_awakening import _build_awakening_group
from .role_growth import (
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
    # Persisted values are loaded together with the index, never during UI construction.
    settings = WorldBonusSettings()
    card = QFrame()
    card.setObjectName("officialRoleWorldBonusCard")
    card.setFixedHeight(35)
    layout = QHBoxLayout(card)
    layout.setContentsMargins(8, 0, 8, 0)
    layout.setSpacing(3)
    title = QLabel("家具加成")
    title.setObjectName("officialRoleWorldBonusTitle")
    layout.addWidget(title)

    attack = NoWheelSpinBox()
    attack.setObjectName("officialRoleWorldAttack")
    attack.setRange(0, 20)
    attack.setSingleStep(2)
    attack.setButtonSymbols(QAbstractSpinBox.NoButtons)
    attack.setToolTip("妖刀家具加成：每级攻击力 +2，满级 +20")
    attack.setFixedWidth(50)
    attack.setFixedHeight(29)
    attack.setStyleSheet("padding:1px 4px")
    crit_damage = NoWheelDoubleSpinBox()
    crit_damage.setObjectName("officialRoleWorldCritDamage")
    crit_damage.setRange(0.0, 4.0)
    crit_damage.setDecimals(1)
    crit_damage.setSingleStep(0.4)
    crit_damage.setButtonSymbols(QAbstractSpinBox.NoButtons)
    crit_damage.setToolTip("拳套家具加成：每级暴击伤害 +0.4%，满级 +4%")
    crit_damage.setFixedWidth(50)
    crit_damage.setFixedHeight(29)
    crit_damage.setStyleSheet("padding:1px 4px")
    layout.addWidget(attack)
    attack_unit = QLabel("攻")
    attack_unit.setObjectName("officialRoleWorldAttackUnit")
    layout.addWidget(attack_unit)
    layout.addWidget(crit_damage)
    crit_damage_unit = QLabel("%爆伤")
    crit_damage_unit.setObjectName("officialRoleWorldCritDamageUnit")
    layout.addWidget(crit_damage_unit)
    window.official_role_world_attack = attack
    window.official_role_world_crit_damage = crit_damage
    _set_world_bonus_controls(window, settings)
    attack.setEnabled(False)
    crit_damage.setEnabled(False)
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
        or controller._closed
        or controller.dependencies != dependencies
    ):
        if isinstance(controller, OfficialRoleController):
            controller.close()
        controller = OfficialRoleController(dependencies, window if isinstance(window, QWidget) else None)
        notify = getattr(window, "on_configuration_changed", None)
        if callable(notify):
            controller.changed.connect(lambda: notify() if controller.dependencies == OfficialRoleDependencies.from_app_context(window.app_context) else None)
        window._official_role_controller = controller
    return controller


def _populate_role_tab(window, scroll: QScrollArea, character_id: int) -> None:
    if scroll.property("loaded") or getattr(scroll, "_detail_requested", False):
        return
    controller = _role_controller(window)
    scroll._detail_requested = True
    scroll.setEnabled(False)
    if scroll.widget() is None:
        scroll.setWidget(QLabel("正在加载角色资料…", scroll))

    def apply(result) -> None:
        if not isValid(scroll) or getattr(window, "_official_role_controller", None) is not controller:
            return
        if controller.dependencies != OfficialRoleDependencies.from_app_context(window.app_context):
            return
        scroll._detail_requested = False
        detail, source_key = result
        detail["_view_source_key"] = source_key
        if character_id in getattr(window, "_official_role_dirty_ids", set()):
            scroll.setEnabled(True)
            return
        _render_role_tab(window, scroll, character_id, detail)

    def failed(error: str) -> None:
        if not isValid(scroll) or getattr(window, "_official_role_controller", None) is not controller:
            return
        scroll._detail_requested = False
        scroll.setToolTip(f"角色资料加载失败：{error}；重新进入角色页可重试。")
        if isinstance(scroll.widget(), QLabel):
            scroll.widget().setText("角色资料加载失败，请重新进入角色页重试。")

    def discarded() -> None:
        if isValid(scroll):
            scroll._detail_requested = False

    controller.request_detail(character_id, apply, failed, discarded)


def _render_role_tab(window, scroll: QScrollArea, character_id: int, detail: dict) -> None:
    """Construct one hidden section per event-loop turn, then publish a complete editor."""
    token = object()
    scroll._role_build_token = token
    context = getattr(window, "app_context", None)
    dependencies = OfficialRoleDependencies.from_app_context(context) if context is not None else None
    editor = {
        "detail": detail,
        "marginal_property_weights": dict(detail.get("property_weights") or {}),
        "marginal_main_property_weights": dict(detail.get("main_property_weights") or {}),
        "equipment_context_key": ("saved" if detail["equipment_contexts"]["saved"]["available"] else "current"),
    }
    # Keep the lazily-built page a hidden child throughout construction.  A
    # parentless QWidget is a transient top-level window on Windows and can be
    # painted as a small popup while the heavy role form is being assembled.
    content = QWidget(scroll.viewport())
    content.hide()
    form = QVBoxLayout(content)
    form.setSpacing(15)
    form.setContentsMargins(15, 15, 15, 15)
    sections = [
        lambda: _build_base_group(window, character_id, detail, editor),
        lambda: _build_awakening_group(window, character_id, detail, editor),
        lambda: _build_skill_group(window, character_id, detail, editor),
    ]
    limited_catalog = detail.get("catalog_scope") in {"role_page", "reference"}
    if not limited_catalog:
        sections.append(lambda: _build_margin_group(window, character_id, detail, editor))
    sections.append(lambda: _build_fork_group(window, character_id, detail, editor))
    if not limited_catalog:
        sections.extend((
            lambda: _build_drive_summary_group(window, detail, editor),
            lambda: _build_damage_formula_group(detail, editor),
            lambda: _build_weight_group(window, character_id, detail, editor),
        ))

    def build_next() -> None:
        expired = (dependencies is not None
                   and dependencies != OfficialRoleDependencies.from_app_context(window.app_context))
        expired = expired or getattr(window, "_current_official_role_id", character_id) != character_id
        expired = expired or getattr(getattr(window, "_official_role_controller", None), "_closed", False)
        if expired or not isValid(scroll) or not isValid(content) or getattr(scroll, "_role_build_token", None) is not token:
            if isValid(content):
                content.deleteLater()
            return
        try:
            if sections:
                started = perf_counter()
                form.addWidget(sections.pop(0)())
                log_perf(logger, "role.section_apply", elapsed_ms=(perf_counter() - started) * 1000)
                QTimer.singleShot(0, build_next)
                return
            form.addSpacing(100)
            form.addStretch()
            window._official_role_editors[character_id] = editor
            scroll.setWidget(content)
            scroll.setProperty("loaded", True)
            scroll.setEnabled(True)
            scroll.setUpdatesEnabled(True)
            content.show()
            restore = getattr(scroll, "_restore_scroll_value", None)
            if restore is not None:
                QTimer.singleShot(0, lambda: scroll.verticalScrollBar().setValue(int(restore)) if isValid(scroll) else None)
                scroll._restore_scroll_value = None
        except Exception as exc:
            content.deleteLater()
            scroll.setUpdatesEnabled(True)
            scroll.setToolTip(f"角色页面准备失败：{exc}；重新进入角色页可重试。")
            logger.warning(f"角色页面准备失败：{exc}")

    QTimer.singleShot(0, build_next)


def _submit_role_change(window, work, committed, *, completion=None):
    controller = _role_controller(window)
    if controller.is_writing():
        return False
    page = getattr(window, "official_role_page_view", None)
    if page is not None:
        page.setEnabled(False)

    def current():
        return (controller is getattr(window, "_official_role_controller", None)
                and controller.dependencies == OfficialRoleDependencies.from_app_context(window.app_context))

    def unlock():
        if current() and page is not None:
            page.setEnabled(True)

    def done(value):
        unlock()
        if current():
            committed(value)
            if completion is not None:
                completion(True)
        elif completion is not None:
            completion(False)

    def failed(error):
        unlock()
        if current():
            QMessageBox.warning(window, "操作未完成", f"编辑已保留；请核对已保存内容后重试。\n{error}")
            if completion is not None:
                completion(False)
        elif completion is not None:
            completion(False)

    def application_failed(error):
        unlock()
        if current():
            QMessageBox.warning(window, "提交后刷新失败", f"数据已提交，请重新进入角色页；不要重复保存。\n{error}")
            if completion is not None:
                completion(False)

    return controller.submit_change(work, done, failed, application_failed)


def _save_profiles(window, *, show_message: bool = True, completion=None) -> bool:
    controller = _role_controller(window)
    if controller.is_writing():
        return False
    dirty_ids = list(getattr(window, "_official_role_dirty_ids", set()))
    world_bonus_dirty = bool(
        getattr(window, "_official_role_world_bonus_dirty", False)
    )
    if not dirty_ids and not world_bonus_dirty:
        window._my_role_dirty = False
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
                    awakening_level=editor["awakening_level"].value(),
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
        settings = (
                WorldBonusSettings(
                    yaodao_attack_add=float(
                        window.official_role_world_attack.value()
                    ),
                    quantao_crit_damage=float(
                        window.official_role_world_crit_damage.value()
                    )
                    / 100.0,
                ) if world_bonus_dirty else None
        )
    except Exception as exc:
        QMessageBox.warning(window, "保存失败", str(exc))
        return False
    def work():
        if updates:
            controller.save_profiles(updates)
        if settings is not None:
            controller.save_world_bonus(settings)

    def committed(_value):
        window._official_role_dirty_ids.clear()
        window._official_role_world_bonus_dirty = False
        window._my_role_dirty = False
        if settings is not None:
            window._official_role_saved_world_bonus = settings
        _refresh_my_role(window, force=True)
        if show_message:
            QMessageBox.information(window, "保存", "角色养成指针与家具加成已保存到当前账号数据库。")

    return _submit_role_change(window, work, committed, completion=completion)


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
    existing = scroll.widget()
    if existing is not None:
        existing.hide()
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
    controller = _role_controller(window)
    if controller.is_writing():
        return
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
    def committed(_value):
        _reload_current_role_tab(window, character_id)
        QMessageBox.information(window, "已重置", "当前角色与弧盘已恢复为公共模板。")
    _submit_role_change(window, lambda: controller.reset_profile(character_id), committed)


def _reset_all_roles(window) -> None:
    controller = _role_controller(window)
    if controller.is_writing():
        return
    answer = QMessageBox.question(
        window,
        "重置全部角色",
        "将当前账号所有角色的等级、觉醒、技能和弧盘恢复为公共模板。\n官方额外形状始终读取静态库，账号基础权重不会重置，是否继续？",
        QMessageBox.Yes | QMessageBox.Cancel,
        QMessageBox.Cancel,
    )
    if answer != QMessageBox.Yes:
        return
    def committed(count):
        window._official_role_dirty_ids.clear()
        window._my_role_dirty = bool(getattr(window, "_official_role_world_bonus_dirty", False))
        _refresh_my_role(window, force=True)
        QMessageBox.information(window, "已重置", f"已将 {count} 个角色与弧盘恢复为公共模板。")
    _submit_role_change(window, controller.reset_all_profiles, committed)


def _page_my_role(window) -> QWidget:
    page = QWidget()
    window.official_role_page_view = page
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
    sync = QPushButton("同步状态")
    sync.setObjectName("officialRoleSyncPrimary")
    sync.setStyleSheet(themed_style(
        "QPushButton#officialRoleSyncPrimary{background:#1f6feb;color:#fff;"
        "border:1px solid #58a6ff;font-weight:600}"
        "QPushButton#officialRoleSyncPrimary:hover{background:#388bfd}"
        "QPushButton#officialRoleSyncPrimary:disabled{background:#1f3f6e;"
        "color:#8b949e;border-color:#315f9e}"
    ))
    header_button_height = save.sizeHint().height()
    save.setFixedHeight(header_button_height)
    sync.setFixedHeight(header_button_height)
    header.addWidget(sync)
    header.addWidget(save)
    root.addLayout(header)
    sync_result = RoleSyncResultText(page)
    sync_result.hide()
    root.addWidget(sync_result)

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
    sync_controller = getattr(window, "character_profile_sync_controller", None)
    if sync_controller is not None:
        sync_controller.attach_controls(sync, (area, reset_current, reset_all, save,
                                               window.official_role_world_attack,
                                               window.official_role_world_crit_damage), result_text=sync_result)
    else:
        from src.features.input_operation_entry import show_input_unavailable
        sync.clicked.connect(lambda: show_input_unavailable(
            window, "同步角色状态", "角色状态同步服务尚未就绪，请检查组件连接。"))
    window.official_role_search = search
    window._official_role_dirty_ids = set()
    window._official_role_world_bonus_dirty = False
    window._official_role_editors = {}
    window._my_role_dirty = False
    _refresh_my_role(window)
    return page


def _refresh_my_role(window, *, restore_scroll_value: int | None = None, force: bool = False) -> None:
    if getattr(window, "my_role_form_layout", None) is None:
        return
    if getattr(window, "_my_role_dirty", False) and not force:
        return  # Background notifications never overwrite an unsaved editor.
    controller = _role_controller(window)
    tabs = getattr(window, "official_role_tabs", None)

    def apply(result) -> None:
        if getattr(window, "_official_role_controller", None) is not controller:
            return
        if controller.dependencies != OfficialRoleDependencies.from_app_context(window.app_context):
            return
        if getattr(window, "_my_role_dirty", False) and not force:
            if tabs is not None:
                tabs.setEnabled(True)
            return
        roles, settings, key = result
        page = getattr(window, "official_role_page_view", None)
        if page is not None:
            page.setEnabled(True)
        if not force and key == getattr(window, "_official_role_source_key", None) and tabs is not None:
            if tabs.currentWidget() is not None:
                _populate_role_tab(window, tabs.currentWidget(), int(tabs.tabBar().tabData(tabs.currentIndex())))
            return
        window._official_role_saved_world_bonus = settings
        if not getattr(window, "_official_role_world_bonus_dirty", False):
            _set_world_bonus_controls(window, settings)
        sync = getattr(window, "character_profile_sync_controller", None)
        for control in (window.official_role_world_attack, window.official_role_world_crit_damage):
            control.setEnabled(not (sync is not None and sync.is_running()))
        window._official_role_source_key = key
        _apply_role_index(window, roles, restore_scroll_value=restore_scroll_value)

    def failed(error: str) -> None:
        if getattr(window, "_official_role_controller", None) is controller:
            window._official_role_source_key = None
            window.my_role_form_area.setToolTip(f"角色目录加载失败：{error}；重新进入角色页可重试。")
            logger.warning(f"角色目录加载失败：{error}")

    controller.request_index(apply, failed)


def _apply_role_index(window, roles, *, restore_scroll_value: int | None = None) -> None:
    layout = window.my_role_form_layout
    signature = tuple((int(role["character_id"]), str(role.get("name_zh") or role["character_id"])) for role in roles)
    tabs = getattr(window, "official_role_tabs", None)
    if tabs is not None and getattr(window, "_official_role_index_signature", None) == signature:
        for index in range(tabs.count()):
            scroll = tabs.widget(index)
            scroll._detail_requested = False
            scroll._role_build_token = None
            scroll.setProperty("loaded", False)
            scroll.setEnabled(False)
        window._official_role_editors = {}
        tabs.setEnabled(True)
        current_scroll = tabs.currentWidget()
        if current_scroll is not None:
            current_scroll._restore_scroll_value = restore_scroll_value
            _populate_role_tab(window, current_scroll, int(tabs.tabBar().tabData(tabs.currentIndex())))
        return
    current_id = getattr(window, "_current_official_role_id", None)
    _clear_layout(layout)
    window._official_role_editors = {}
    if not roles:
        window.official_role_tabs = None
        layout.addWidget(QLabel("暂无官方角色数据。"))
        return

    search = getattr(window, "official_role_search", None)
    if not isinstance(search, QLineEdit):
        return
    tabs = QTabWidget(window.my_role_form_widget)
    tabs.setObjectName("officialRoleTabs")
    tab_ids = {}
    for role in roles:
        scroll = QScrollArea(tabs)
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
    window.official_role_tabs = tabs
    window._official_role_index_signature = signature
    layout.addWidget(tabs)
    filter_tabs(search.text())
    current_scroll = tabs.currentWidget()
    if current_scroll is not None:
        current_scroll._restore_scroll_value = restore_scroll_value
    load_visible(tabs.currentIndex())

def confirm_pending_my_role_changes(window, *, completion=None) -> bool:
    if not getattr(window, "_my_role_dirty", False):
        return True
    answer = QMessageBox.question(
        window,
        "未保存角色状态",
        "角色养成指针或家具加成有未保存修改，是否先保存？",
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
        _save_profiles(window, show_message=False, completion=completion)
        return not getattr(window, "_my_role_dirty", False)
    _role_controller(window).log_dirty_exit(
        "discard", len(getattr(window, "_official_role_dirty_ids", set()))
    )
    window._official_role_dirty_ids.clear()
    window._official_role_world_bonus_dirty = False
    saved_bonus = getattr(window, "_official_role_saved_world_bonus", None)
    if saved_bonus is not None:
        _set_world_bonus_controls(window, saved_bonus)
    window._official_role_source_key = None
    window._my_role_dirty = False
    return True
