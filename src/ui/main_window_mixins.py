# 显式声明主窗口从各功能模块获得的方法。
"""Explicit MainWindow mixins for extracted feature methods."""

from __future__ import annotations

from src.features.allocation import results_view as allocation_results_view
from src.features.allocation import runner as allocation_runner
from src.features.blueprints import page as blueprints_page
from src.features.identification import controller as identification_controller
from src.features.identification import dialogs as identification_dialogs
from src.features.inventory import page as inventory_page
from src.features.onboarding import guide as onboarding_guide
from src.features.role import page as role_page
from src.features.scanning import controller as scanning_controller


class OnboardingGuideMixin:
    _guide_image_files = onboarding_guide._guide_image_files
    _maybe_show_quick_start = onboarding_guide._maybe_show_quick_start
    _show_quick_start = onboarding_guide._show_quick_start


class InventoryPageMixin:
    _equipment_compare_signature = inventory_page._equipment_compare_signature
    _same_equipment_by_ocr = inventory_page._same_equipment_by_ocr
    _page_equipment = inventory_page._page_equipment
    _refresh_equip = inventory_page._refresh_equip
    _saved_plan_diff_text = inventory_page._saved_plan_diff_text
    _show_saved_plan_diff_dialog = inventory_page._show_saved_plan_diff_dialog
    _clear_all_equipment = inventory_page._clear_all_equipment
    _delete_role_equipment = inventory_page._delete_role_equipment
    _import_to_my_role = inventory_page._import_to_my_role
    _import_all_to_my_roles = inventory_page._import_all_to_my_roles
    _save_eq = inventory_page._save_eq


class BlueprintPageMixin:
    _page_blueprint = blueprints_page._page_blueprint
    _refresh_blueprints = blueprints_page._refresh_blueprints
    _compute_blueprints = blueprints_page._compute_blueprints
    _render_blueprints = blueprints_page._render_blueprints
    _draw_blueprints = blueprints_page._draw_blueprints
    _filter_blueprints = blueprints_page._filter_blueprints


class AllocationRunnerMixin:
    _run_allocation = allocation_runner._run_allocation
    _start_allocation_worker = allocation_runner._start_allocation_worker
    _confirm_unsaved_allocation_before_recompute = allocation_runner._confirm_unsaved_allocation_before_recompute
    _on_done = allocation_runner._on_done
    _on_exec_error = allocation_runner._on_exec_error
    _save_alloc = allocation_runner._save_alloc
    _archive_pending_screenshots = allocation_runner._archive_pending_screenshots


class AllocationResultsMixin:
    _section_label = allocation_results_view._section_label
    _render_results = allocation_results_view._render_results
    _calc_grade = allocation_results_view._calc_grade
    _show_plan_diff_dialog = allocation_results_view._show_plan_diff_dialog
    _build_plan_diff_dialog = allocation_results_view._build_plan_diff_dialog
    _diff_item_card = allocation_results_view._diff_item_card
    _diff_item_score_info = allocation_results_view._diff_item_score_info
    _plan_diff_text = allocation_results_view._plan_diff_text
    _sync_role_drive_replacement = allocation_results_view._sync_role_drive_replacement
    _sync_role_tape_replacement = allocation_results_view._sync_role_tape_replacement
    _stat_w = allocation_results_view._stat_w
    _stat_c = allocation_results_view._stat_c
    _weighted_score = allocation_results_view._weighted_score
    _quality_coef = allocation_results_view._quality_coef
    _canonical_stat_name = allocation_results_view._canonical_stat_name
    _stat_number_value = allocation_results_view._stat_number_value
    _item_value = allocation_results_view._item_value
    _add_stat_total = allocation_results_view._add_stat_total
    _fallback_tape_main_value = allocation_results_view._fallback_tape_main_value
    _extra_shape_area = allocation_results_view._extra_shape_area
    _equipment_bonus_rows = allocation_results_view._equipment_bonus_rows
    _get_my_role_entry = allocation_results_view._get_my_role_entry
    _role_base_bonus_rows = allocation_results_view._role_base_bonus_rows
    _merge_bonus_row_lists = allocation_results_view._merge_bonus_row_lists
    _bonus_rows_for_mode = allocation_results_view._bonus_rows_for_mode
    _bonus_summary_mode_label = allocation_results_view._bonus_summary_mode_label
    _make_bonus_mode_switch = allocation_results_view._make_bonus_mode_switch
    _clear_layout_widgets = allocation_results_view._clear_layout_widgets
    _format_bonus_value = allocation_results_view._format_bonus_value
    _bonus_summary_widget = allocation_results_view._bonus_summary_widget
    _role_stat_priority_stats = allocation_results_view._role_stat_priority_stats
    _sort_bonus_aligned_rows = allocation_results_view._sort_bonus_aligned_rows
    _role_bonus_summary_panel = allocation_results_view._role_bonus_summary_panel
    _refresh_bonus_summary_panel = allocation_results_view._refresh_bonus_summary_panel
    _aligned_bonus_comparison_rows = allocation_results_view._aligned_bonus_comparison_rows
    _has_bonus_delta = allocation_results_view._has_bonus_delta
    _bonus_row_widget = allocation_results_view._bonus_row_widget
    _bonus_placeholder_row_widget = allocation_results_view._bonus_placeholder_row_widget
    _bonus_spacer_row = allocation_results_view._bonus_spacer_row
    _bonus_comparison_column = allocation_results_view._bonus_comparison_column
    _bonus_delta_row_widget = allocation_results_view._bonus_delta_row_widget
    _bonus_delta_column = allocation_results_view._bonus_delta_column
    _bonus_comparison_widget = allocation_results_view._bonus_comparison_widget
    _show_bonus_summary_dialog = allocation_results_view._show_bonus_summary_dialog
    _show_bonus_comparison_dialog = allocation_results_view._show_bonus_comparison_dialog
    _score_drive_dict = allocation_results_view._score_drive_dict
    _score_tape_dict = allocation_results_view._score_tape_dict
    _equip_card = allocation_results_view._equip_card


class IdentificationControllerMixin:
    _page_identify = identification_controller._page_identify
    _refresh_identify_options = identification_controller._refresh_identify_options
    _on_identify_type_changed = identification_controller._on_identify_type_changed
    _get_tape_main_stats_pool = identification_controller._get_tape_main_stats_pool
    _set_combo_data = identification_controller._set_combo_data
    _make_combo_searchable = identification_controller._make_combo_searchable
    _combo_data_or_resolved_text = identification_controller._combo_data_or_resolved_text
    _identify_quality = identification_controller._identify_quality
    _clear_identify_input = identification_controller._clear_identify_input
    _clear_identify_results = identification_controller._clear_identify_results
    _delete_layout = identification_controller._delete_layout
    _set_identify_busy = identification_controller._set_identify_busy
    _identify_paths_from_text = identification_controller._identify_paths_from_text
    _refresh_identify_previews = identification_controller._refresh_identify_previews
    _show_identify_preview_image = identification_controller._show_identify_preview_image
    _remove_identify_preview_path = identification_controller._remove_identify_preview_path
    _identify_start = identification_controller._identify_start
    _start_identify_capture_mode = identification_controller._start_identify_capture_mode
    _capture_identify_foreground = identification_controller._capture_identify_foreground
    _add_identify_capture_path = identification_controller._add_identify_capture_path
    _finish_identify_capture_mode = identification_controller._finish_identify_capture_mode
    _identify_choose_file = identification_controller._identify_choose_file
    _identify_from_clipboard = identification_controller._identify_from_clipboard
    _identify_from_image_path = identification_controller._identify_from_image_path
    _parse_identify_images = identification_controller._parse_identify_images
    _on_identify_items_loaded = identification_controller._on_identify_items_loaded
    _load_identify_item_to_form = identification_controller._load_identify_item_to_form
    _identify_from_manual = identification_controller._identify_from_manual
    _apply_identify_manual_fields = identification_controller._apply_identify_manual_fields
    _manual_tokens = identification_controller._manual_tokens
    _manual_value = identification_controller._manual_value
    _resolve_stat_name = identification_controller._resolve_stat_name
    _parse_manual_stats = identification_controller._parse_manual_stats
    _start_identify_item = identification_controller._start_identify_item
    _start_identify_items = identification_controller._start_identify_items
    _get_identify_blueprints = identification_controller._get_identify_blueprints
    _run_identify_item = identification_controller._run_identify_item
    _run_identify_items = identification_controller._run_identify_items
    _render_identify_result = identification_controller._render_identify_result
    _render_identify_result_page = identification_controller._render_identify_result_page
    _set_identify_result_page = identification_controller._set_identify_result_page
    _identify_result_row = identification_controller._identify_result_row
    _on_identify_error = identification_controller._on_identify_error


class IdentificationDialogsMixin:
    _choose_identify_image_options = identification_dialogs._choose_identify_image_options
    _confirm_identify_tape_main_stats = identification_dialogs._confirm_identify_tape_main_stats


class ScanningControllerMixin:
    _page_execute = scanning_controller._page_execute
    _on_scan_change = scanning_controller._on_scan_change
    _on_priority_changed = scanning_controller._on_priority_changed
    _open_scan_post_action_manager = scanning_controller._open_scan_post_action_manager
    _do_exec = scanning_controller._do_exec
    _scan_lifecycle = scanning_controller._scan_lifecycle
    _is_scope_image = scanning_controller._is_scope_image
    _prepare_incremental_parse = scanning_controller._prepare_incremental_parse
    _matching_scope_files = scanning_controller._matching_scope_files
    _unique_path = scanning_controller._unique_path
    _move_to_failed = scanning_controller._move_to_failed
    _delete_paths = scanning_controller._delete_paths
    _next_full_scan_index = scanning_controller._next_full_scan_index
    _rename_incremental_successes = scanning_controller._rename_incremental_successes
    _move_first_full_scan_to_tail = scanning_controller._move_first_full_scan_to_tail
    _postprocess_vision_files = scanning_controller._postprocess_vision_files
    _start_vision_processing = scanning_controller._start_vision_processing
    _on_vision_progress = scanning_controller._on_vision_progress
    _on_vision_done = scanning_controller._on_vision_done
    _on_vision_error = scanning_controller._on_vision_error
    _on_vision_cancel = scanning_controller._on_vision_cancel
    _on_vision_canceled = scanning_controller._on_vision_canceled
    _start_scan = scanning_controller._start_scan
    _start_gamepad_scan = scanning_controller._start_gamepad_scan
    _on_gamepad_scan_done = scanning_controller._on_gamepad_scan_done
    _on_gamepad_parse_progress = scanning_controller._on_gamepad_parse_progress
    _on_gamepad_parse_done = scanning_controller._on_gamepad_parse_done
    _on_gamepad_post_actions_ready = scanning_controller._on_gamepad_post_actions_ready
    _register_scan_hotkeys = scanning_controller._register_scan_hotkeys
    _hotkey_to_vk = scanning_controller._hotkey_to_vk
    _win_hotkey_loop = scanning_controller._win_hotkey_loop
    _hotkey_poll_loop = scanning_controller._hotkey_poll_loop
    _unregister_scan_hotkeys = scanning_controller._unregister_scan_hotkeys
    _on_hk_stop = scanning_controller._on_hk_stop
    _on_hk_capture = scanning_controller._on_hk_capture
    _on_hk_finish = scanning_controller._on_hk_finish
    _on_gamepad_pipeline_done = scanning_controller._on_gamepad_pipeline_done
    _on_gamepad_error = scanning_controller._on_gamepad_error
    _on_scan_done = scanning_controller._on_scan_done
    _on_scan_error = scanning_controller._on_scan_error


class RolePageMixin:
    _page_my_role = role_page._page_my_role
    _refresh_my_role = role_page._refresh_my_role


class FeatureMainWindowMixin(
    OnboardingGuideMixin,
    InventoryPageMixin,
    BlueprintPageMixin,
    AllocationRunnerMixin,
    AllocationResultsMixin,
    IdentificationControllerMixin,
    IdentificationDialogsMixin,
    ScanningControllerMixin,
    RolePageMixin,
):
    """Combined feature surface for MainWindow."""
