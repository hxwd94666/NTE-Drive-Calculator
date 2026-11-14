# 兼顾模式独立目标与槽位草稿；复用倒带窗口的取消、代次和 worker 生命周期。
from __future__ import annotations

from concurrent.futures import CancelledError

from PySide6.QtWidgets import QComboBox, QDialog, QFrame, QHBoxLayout, QSizePolicy, QVBoxLayout, QWidget

from src.domain.rewind.dual_target import DualTargetRequest, GroupTarget
from src.domain.rewind.rewind_loadout import positive_id, references_for_roles
from src.domain.rewind.rewind_shape_recommendation import GRADE_RATIOS
from src.features.toolbox.rewind.dual_role_picker import DualRoleSelectionDialog
from src.features.toolbox.rewind.role_selection import preference_custom_percent
from src.features.toolbox.rewind.percent_input import RewindPercentInput
from src.ui.common.text_catalog import ElidedTextLabel, TextActionButton


class RewindDualTargetUiMixin:
    def _initialize_dual_target(self):
        self._dual_preferences = self._preferences
        raw_groups = self._dual_preferences.get('groups')
        raw_groups = raw_groups if isinstance(raw_groups, dict) else {}
        self._dual_groups, self._dual_slot_ids = {}, {}
        for key, default in [('improve', 'A'), ('push', 'SSS')]:
            raw = raw_groups.get(key)
            raw = raw if isinstance(raw, dict) else {}
            raw_slots = raw.get('slots')
            raw_slots = raw_slots if isinstance(raw_slots, dict) else {}
            self._dual_slot_ids[key] = {identifier: slot_id if type(slot_id) is int and slot_id > 0 else None
                for value, slot_id in raw_slots.items() if (identifier := positive_id(value)) is not None}
            self._dual_groups[key] = dict(slots={identifier: None for identifier in self._dual_slot_ids[key]},
                grade=raw.get('grade') if isinstance(raw.get('grade'), str) and raw['grade'] in GRADE_RATIOS else default,
                mode='custom' if raw.get('mode') == 'custom' else 'grade',
                custom_percent=preference_custom_percent(raw.get('custom_percent')))
            if self._dual_groups[key]['mode'] == 'custom' and self._dual_groups[key]['custom_percent'] is None:
                self._dual_groups[key]['custom_percent'] = 50.
        self._dual_manual_draft = self._dual_preferences.get('plan_origin') == 'manual'
        if self._strategy_key == 'dual_target':
            self._saved_rewind_shape_ids = tuple(self._dual_preferences.get('saved_rewind_shape_ids', ()))
            self._saved_rewind_slots = tuple(self._dual_preferences.get('saved_rewind_slots', ()))
            if self._dual_preferences.get('plan_origin') == 'dual_target' and not self._dual_request_matches_saved():
                self._recommendation_invalidated = True

    def _build_dual_controls(self):
        text = self.text_catalog.render
        panel = QWidget()
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self._dual_summaries, self._dual_buttons, self._dual_grades, self._dual_percent = {}, {}, {}, {}
        for key in ('improve', 'push'):
            frame = QFrame()
            frame.setObjectName('rewindSelectionCard')
            box = QVBoxLayout(frame)
            box.setContentsMargins(12, 10, 12, 10)
            row = QHBoxLayout()
            title = ElidedTextLabel(text('rewind.dual.' + key), frame)
            title.setObjectName('rewindGroupTitle')
            font = title.font()
            if font.pixelSize() > 0:
                font.setPixelSize(font.pixelSize() * 2)
            else:
                font.setPointSizeF(font.pointSizeF() * 2)
            font.setBold(True)
            title.setFont(font)
            row.addWidget(title, 1)
            button = TextActionButton(text('rewind.dual.choose_roles'))
            button.setObjectName('btnAction')
            button.setEnabled(bool(self._roles))
            button.clicked.connect(lambda _checked=False, group=key: self._choose_dual_roles(group))
            row.addWidget(button)
            box.addLayout(row)
            summary = ElidedTextLabel('')
            box.addWidget(summary)
            target = QHBoxLayout()
            caption = ElidedTextLabel(text('rewind.dual.drive_target'))
            grade = QComboBox()
            grade.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            grade.setMinimumContentsLength(5)
            grade.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            for value in GRADE_RATIOS:
                grade.addItem(value, value)
            grade.addItem(text('rewind.dual.custom'), 'custom')
            state = self._dual_groups[key]
            grade.setCurrentIndex(grade.findData('custom' if state['mode'] == 'custom' else state['grade']))
            caption.setBuddy(grade)
            target.addWidget(caption)
            target.addWidget(grade, 1)
            # 自选目标可能一开始就可见；先归属卡片，避免 setVisible(True) 闪出独立原生窗口。
            percent = RewindPercentInput(frame)
            percent.setRange(1., 100.)
            percent.setDecimals(1)
            percent.setSuffix('%')
            percent.setValue(state['custom_percent'] or 50.)
            percent.setAccessibleName(text('rewind.dual.custom_target', group=text('rewind.dual.' + key)))
            percent.setVisible(state['mode'] == 'custom')
            target.addWidget(percent)
            grade.currentIndexChanged.connect(lambda _index, group=key: self._dual_target_changed(group))
            percent.valueChanged.connect(lambda _value, group=key: self._dual_target_changed(group))
            box.addLayout(target)
            layout.addWidget(frame, 1)
            self._dual_summaries[key], self._dual_buttons[key] = summary, button
            self._dual_grades[key], self._dual_percent[key] = grade, percent
        self._update_dual_summaries()
        return panel

    def _install_dual_roles(self):
        for key, state in self._dual_groups.items():
            refs = references_for_roles(self._roles, self._dual_slot_ids[key])
            state['slots'] = {identifier: refs.get(identifier) for identifier in state['slots']}
            self._dual_buttons[key].setEnabled(True)
        self._update_dual_summaries()
        if (self._strategy_key == 'dual_target' and self._dual_preferences.get('plan_origin') == 'dual_target'
                and not self._dual_request_matches_saved()):
            self._invalidate_recommendation()

    def _update_dual_summaries(self):
        for key, label in self._dual_summaries.items():
            label.setText(self.text_catalog.render('rewind.dual.selected', count=len(self._dual_groups[key]['slots'])))

    def _choose_dual_roles(self, active):
        if not self._roles:
            return
        try:
            self._check_selection_context()
        except CancelledError:
            return
        dialog = DualRoleSelectionDialog(self, roles=self._roles,
            groups={key: state['slots'] for key, state in self._dual_groups.items()}, active_group=active,
            asset_root=getattr(self._service, 'asset_root', None), catalog=self.text_catalog)
        try:
            if dialog.exec() == QDialog.Accepted:
                self._check_selection_context()
                for key, slots in dialog.selected_groups().items():
                    self._dual_groups[key]['slots'] = slots
                    self._dual_slot_ids[key] = {identifier: ref.slot_id if ref else None for identifier, ref in slots.items()}
                self._save_dual_draft()
                self._update_dual_summaries()
        except CancelledError:
            return
        finally:
            dialog.deleteLater()

    def _dual_target_changed(self, key):
        state = self._dual_groups[key]
        value = self._dual_grades[key].currentData()
        state['mode'] = 'custom' if value == 'custom' else 'grade'
        if value != 'custom':
            state['grade'] = value
        state['custom_percent'] = self._dual_percent[key].value()
        self._dual_percent[key].setVisible(value == 'custom')
        self._save_dual_draft()

    def _dual_signature(self):
        return tuple((key, state['grade'], state['mode'], state['custom_percent'],
            tuple(sorted((identifier, ref.slot_id if ref else None, ref.plan_id if ref else None)
                         for identifier, ref in state['slots'].items()))) for key, state in sorted(self._dual_groups.items()))

    def _save_dual_draft(self):
        return self._save_preferences()

    def _dual_group_preferences(self):
        return {key: dict(grade=state['grade'], mode=state['mode'], custom_percent=state['custom_percent'],
            slots={str(identifier): ref.slot_id if ref else self._dual_slot_ids[key].get(identifier)
                   for identifier, ref in state['slots'].items()}) for key, state in self._dual_groups.items()}

    def _dual_request_matches_saved(self):
        saved = self._dual_preferences.get('saved_request')
        if not isinstance(saved, dict):
            return False
        for key, current in self._dual_group_preferences().items():
            before = saved.get(key)
            if not isinstance(before, dict) or before.get('slots') != current['slots'] or before.get('mode') != current['mode']:
                return False
            target = 'custom_percent' if current['mode'] == 'custom' else 'grade'
            if before.get(target) != current[target]:
                return False
        return True

    def _dual_request(self):
        groups = []
        for key in ('improve', 'push'):
            state = self._dual_groups[key]
            if any(ref is None for ref in state['slots'].values()):
                raise ValueError(self.text_catalog.render('rewind.dual.missing_slot', group=self.text_catalog.render('rewind.dual.' + key)))
            groups.append(GroupTarget(tuple(ref for _, ref in sorted(state['slots'].items())), state['grade'],
                state['custom_percent'] if state['mode'] == 'custom' else None))
        return DualTargetRequest(*groups)

    def _sync_strategy_panels(self):
        dual = self._strategy_key == 'dual_target'
        self._legacy_role_panel.setVisible(not dual)
        self._legacy_grade_panel.setVisible(not dual)
        self._dual_panel.setVisible(dual)
        self._dual_notice.setVisible(dual)

    def _validate_analysis(self, analysis):
        if analysis.dual_guard is not None:
            self._service.validate_dual_target_guard(analysis.dual_guard, checkpoint=self._check_selection_context)
        else:
            self._service.validate_selection(analysis.selected_slots, analysis.static_identity)

    def _save_dual_pool(self, analysis, shape_ids):
        from src.services.rewind.dual_recommendation import parse_guard
        guard = analysis.dual_guard if analysis else None
        if guard is None and not self._dual_manual_draft and self._dual_preferences.get('plan_origin') == 'dual_target':
            guard = parse_guard(self._dual_preferences.get('saved_guard'))
        fields = dict(self._capture_preferences(), saved_rewind_shape_ids=shape_ids, saved_rewind_slots=self._serialize_rewind_slots(),
                      plan_origin='dual_target' if guard else 'manual', saved_request=None)
        if analysis is not None:
            request = analysis.dual_target.request
            # 与八槽一次提交的是预览内冻结目标，不用当前草稿补齐推荐来源。
            frozen = {key: dict(grade=group.grade, mode='custom' if group.custom_percent is not None else 'grade',
                custom_percent=group.custom_percent, slots={str(ref.character_id): ref.slot_id for ref in group.slots})
                for key, group in zip(('improve', 'push'), (request.improve, request.push))}
            fields.update(groups=frozen, saved_request=frozen)
        elif guard is not None:
            fields['saved_request'] = self._dual_preferences.get('saved_request')
        updated = self._service.save_dual_target_preferences(fields,
            guard=guard, checkpoint=self._check_selection_context, analysis=analysis)
        self._remember_preferences(updated)

