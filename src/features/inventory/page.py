# 背包与装备页面兼容入口；具体职责由子模块实现。
"""Compatibility facade for inventory feature controllers."""

from __future__ import annotations

from . import equipment_assembly_controller as _assembly
from . import equipment_display_controller as _display
from . import warehouse_controller as _warehouse

__all__ = [
    "_equipment_compare_signature",
    "_same_equipment_by_ocr",
    "_page_equipment",
    "_set_equipment_mode",
    "_refresh_equip",
    "_page_warehouse",
    "_refresh_warehouse",
    "_apply_warehouse_filters",
    "_on_warehouse_sync_state",
    "_on_warehouse_selection_changed",
    "_set_warehouse_selected_state",
    "_toggle_warehouse_item_state",
    "_save_warehouse_state_changes",
    "_show_warehouse_item_identification",
    "_update_warehouse_save_state",
    "_on_warehouse_manual_plan_ready",
    "_open_warehouse_state_manager",
    "_on_warehouse_state_plan_ready",
    "_on_warehouse_state_applied",
    "_on_warehouse_state_error",
    "_set_warehouse_management_busy",
    "_saved_plan_diff_text",
    "_show_saved_plan_diff_dialog",
    "_clear_all_equipment",
    "_delete_role_equipment",
    "_optimize_saved_equipment",
    "_import_game_loadout",
    "_import_all_game_loadouts",
    "_preview_assemble_role",
    "_preview_fast_assemble_all_roles",
    "_preview_automatic_assemble_all_roles",
]


for _method_name in __all__:
    for _controller in (_warehouse, _display, _assembly):
        _method = getattr(_controller, _method_name, None)
        if _method is not None:
            globals()[_method_name] = _method
            break
    else:
        raise ImportError(f"inventory controller missing compatibility method: {_method_name}")

from .equipment_plan_optimizer import (
    _active_sqlite_equipment_users as _active_sqlite_equipment_users,
    _replacement_assignments as _replacement_assignments,
)
from .equipment_plan_renderer import _render_equip_role as _render_equip_role

_assembly_report_dialog = _assembly._assembly_report_dialog
_return_to_equipment_after_assembly = _assembly._return_to_equipment_after_assembly
