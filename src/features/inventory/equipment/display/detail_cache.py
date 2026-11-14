# 当前账号和静态投影内有界复用最近三个角色详情，刷新或写入后丢弃旧控件。
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtWidgets import QVBoxLayout, QWidget

from src.app.theme import current_theme_name
from src.features.inventory.equipment.display.plan_renderer import _render_equip_role


def clear_equipment_detail_cache(window, *, keep_visible=False):
    layout = getattr(window, "equip_content_layout", None)
    visible = {layout.itemAt(index).widget() for index in range(layout.count())} if layout else set()
    for _key, _state, panel in getattr(window, "_equip_detail_cache", {}).values():
        if panel in visible and keep_visible:
            continue
        if layout is not None:
            layout.removeWidget(panel)
        panel.hide()
        panel.deleteLater()
    window._equip_detail_cache = OrderedDict()
    window._equip_detail_scope = None


def hide_equipment_detail(window):
    cached = {entry[2] for entry in getattr(window, "_equip_detail_cache", {}).values()}
    layout = window.equip_content_layout
    while layout.count():
        item = layout.takeAt(0)
        panel = item.widget()
        if panel is not None:
            panel.hide()
            if panel not in cached:
                panel.deleteLater()


def _detail_key(window, state):
    context = getattr(window, "app_context", None)
    account = getattr(context, "account", None)
    presentation = getattr(window, "equipment_presentation", None)
    slots = state.get("_role_slot_states") or (state,)
    return (
        getattr(context, "generation", None),
        getattr(account, "active_account_id", None),
        str(getattr(account, "user_database_path", "")),
        getattr(window, "_equipment_mode", "saved"),
        current_theme_name(),
        id(getattr(presentation, "scoring_engine", None)),
        repr(getattr(presentation, "roles_db", {})),
        repr(getattr(presentation, "stats_config", {})),
        tuple(
            (
                id(slot),
                slot.get("_allocation_locked"),
                slot.get("_game_imported"),
                slot.get("_game_existing_plan_locked"),
                id(slot.get("_game_saved_state")),
            )
            for slot in slots
        ),
    )


def show_equipment_role_detail(window, role_name, state, *, force_refresh=False):
    key = _detail_key(window, state)
    scope = key[:5]
    if getattr(window, "_equip_detail_scope", None) not in (None, scope):
        clear_equipment_detail_cache(window)
    window._equip_detail_scope = scope
    cache = getattr(window, "_equip_detail_cache", None)
    if cache is None:
        cache = window._equip_detail_cache = OrderedDict()
    prior = cache.get(role_name)
    if prior is not None and (force_refresh or prior[0] != key):
        cache.pop(role_name)
        window.equip_content_layout.removeWidget(prior[2])
        prior[2].hide()
        prior[2].deleteLater()
        prior = None
    hide_equipment_detail(window)
    if prior is None:
        panel = QWidget(window.equip_content)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        try:
            _render_equip_role(window, role_name, state, target_layout=layout)
        except BaseException:
            panel.deleteLater()
            raise
        # 保存状态引用防止对象身份复用；缓存不跨数据读取，也不成为业务事实源。
        cache[role_name] = (key, state, panel)
    else:
        panel = prior[2]
    cache.move_to_end(role_name)
    while len(cache) > 3:
        _old_key, _old_state, old_panel = cache.popitem(last=False)[1]
        old_panel.deleteLater()
    window.equip_content_layout.addWidget(panel)
    panel.show()
    window.equip_content_layout.addStretch(1)
