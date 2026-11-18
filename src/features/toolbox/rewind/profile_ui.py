# 倒带窗口只投影当前策略，切换先保存旧草稿，再恢复独立偏好及八槽，不共享可变状态。
from __future__ import annotations

from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import replace

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QMessageBox

from src.ui.common.enum_text import ui_enum_text
from src.domain.rewind.rewind_loadout import read_slot_preferences, references_for_roles
from src.features.toolbox.rewind.execution_dialog import RewindExecutionOptions
from src.domain.rewind.strategy_preferences import parse_ten_limits
from src.features.toolbox.rewind.role_selection import preference_custom_percent


class RewindProfileUiMixin:
    def _capture_preferences(self):
        saved_ids = {key: self._slot_preferences(key) for key in ('balanced', 'focused')}
        fields = dict(strategy=self._strategy_key, _revision=self._profile_revision,
            target_character_ids=sorted(self._target_character_ids), main_character_ids=sorted(self._main_character_ids),
            slot_selection_version=2, selected_slots_by_strategy={
                key: {str(i): slot for i, slot in value.items()} for key, value in saved_ids.items()},
            target_grade=self._target_grade, target_threshold_mode=self._target_threshold_mode,
            target_custom_percent=self._target_custom_percent, groups=self._dual_group_preferences(),
            rewind_qualities=list(self._rewind_options.qualities),
            rewind_drive_customization=self._rewind_options.drive_customization,
            rewind_ten_limits=dict(self._rewind_options.ten_limits))
        if hasattr(self, '_editable_slots'):
            fields['editable_rewind_slots'] = [None if slot is None else dict(
                shape_id=slot.shape.shape_id, quality_gap=slot.quality_gap) for slot in self._editable_slots]
            fields['editable_origin'] = ('manual' if self._manual_plan_draft else
                                         'saved' if self._serialize_rewind_slots() == list(self._saved_rewind_slots)
                                         and self._slots_complete() else
                                         'generated' if self._generated_analysis is not None else 'manual')
        return fields

    def _remember_preferences(self, preferences):
        self._preferences = deepcopy(preferences)
        self._profile_revision = preferences['_revision']
        self._dual_preferences = self._preferences

    def _preference_error_text(self, error):
        key = 'rewind.storage.' + str(error)
        return self.text_catalog.render(key) if key in self.text_catalog.messages else ui_enum_text(self.text_catalog, str(error))

    def _switch_strategy_profile(self, key):
        try:
            self._check_selection_context()
            if not self._save_preferences():
                return
            preferences = self._service.load_preferences(key)
            self._service.select_strategy(key, checkpoint=self._check_selection_context)
            self._check_selection_context()
        except CancelledError:
            return
        except Exception as error:
            QMessageBox.warning(self, self.text_catalog.render('rewind.selection.save_preferences.qmessagebox'),
                                self._preference_error_text(error))
            return
        self._strategy_key = key
        self._remember_preferences(preferences)
        self._target_character_ids = set(preferences.get('target_character_ids', ()))
        self._main_character_ids = set(preferences.get('main_character_ids', ()))
        self._saved_slot_ids_by_strategy = {mode: read_slot_preferences(preferences, strategy=mode)
                                           for mode in ('balanced', 'focused')}
        self._selected_slots_by_strategy = {mode: references_for_roles(self._roles, slots)
                                           for mode, slots in self._saved_slot_ids_by_strategy.items()}
        self._target_grade = preferences.get('target_grade', 'S')
        self._target_threshold_mode = preferences.get('target_threshold_mode', 'grade')
        self._target_custom_percent = preference_custom_percent(preferences.get('target_custom_percent'))
        self._rewind_options = RewindExecutionOptions(
            qualities=tuple(preferences.get('rewind_qualities', ('gold',))),
            drive_customization=preferences.get('rewind_drive_customization', 'none'),
            ten_limits=parse_ten_limits(preferences.get('rewind_ten_limits')))
        self._saved_rewind_shape_ids = tuple(preferences.get('saved_rewind_shape_ids', ()))
        self._saved_rewind_slots = tuple(preferences.get('saved_rewind_slots', ()))
        self._analysis_token = self._generated_analysis = None
        self._recommendation_invalidated = False
        self._manual_plan_draft = False
        self._initialize_dual_target()
        self._install_dual_roles()
        controls = [self._custom_percent_input, *self._dual_grades.values(), *self._dual_percent.values()]
        blockers = [QSignalBlocker(control) for control in controls]
        try:
            for grade, button in self._grade_buttons.items():
                button.setChecked(self._target_threshold_mode == 'grade' and grade == self._target_grade)
            self._custom_target_button.setChecked(self._target_threshold_mode == 'custom')
            self._custom_percent_input.setValue(self._target_custom_percent or 0.)
            self._custom_percent_input.setSuffix('%' if self._target_custom_percent is not None else '')
            self._set_custom_percent_controls_enabled(self._target_threshold_mode == 'custom')
            for group, state in self._dual_groups.items():
                combo = self._dual_grades[group]
                combo.setCurrentIndex(combo.findData('custom' if state['mode'] == 'custom' else state['grade']))
                self._dual_percent[group].setValue(state['custom_percent'] or 50.)
                self._dual_percent[group].setVisible(state['mode'] == 'custom')
        finally:
            for blocker in blockers:
                blocker.unblock()
        self._sync_strategy_panels()
        for mode, button in self._strategy_buttons.items():
            button.setChecked(mode == key)
        self._initialize_rewind_slots(self._saved_rewind_shape_ids, self._saved_rewind_slots)
        self._restore_profile_draft()
        self._update_role_summaries()
        self._last_input_signature = self._input_signature()

    def _restore_profile_draft(self):
        draft = self._preferences.get('editable_rewind_slots')
        if not isinstance(draft, list) or len(draft) != 8 or self._preferences.get('editable_origin') == 'saved':
            return
        candidates = {row.shape.shape_id: row for row in self._replacement_candidates}
        self._editable_slots = [replace(candidates[row['shape_id']], quality_gap=row['quality_gap'])
                                if isinstance(row, dict) and row.get('shape_id') in candidates else None for row in draft]
        self._manual_plan_draft = self._preferences.get('editable_origin') == 'manual'
        self._dual_manual_draft = self._manual_plan_draft
        self._recommendation_invalidated = self._preferences.get('editable_origin') == 'generated'
        self._render_rewind_slots(self.text_catalog.render('rewind.slots.custom'),
                                  self.text_catalog.render('rewind.slots.adjust'))
