# 编排倒带角色槽位草稿、账号偏好与可失效的只读任务。
from __future__ import annotations

from src.ui.common.enum_text import ui_enum_text
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
from concurrent.futures import CancelledError
from dataclasses import dataclass
import threading

from PySide6.QtWidgets import QDialog, QMessageBox

from src.app.workers import WorkerThread
from src.domain.rewind.rewind_loadout import references_for_roles
from src.features.toolbox.rewind.role_picker import _RoleSelectionDialog
from src.services.rewind.recommendation import RewindShapeAnalysis


_LIVE_READ_WORKERS = set()


@dataclass(frozen=True, slots=True)
class RewindUiCatalog:
    roles: tuple
    owned_shape_counts: tuple


class RewindSelectionUiMixin:
    def _initialize_selection_lifecycle(self):
        self._selection_generation = self.operation_generation() if self.operation_generation else None
        self._closed = False
        self._catalog_token = object()
        self._catalog_loading = False
        self._read_cancel = threading.Event()
        self._generated_analysis = None
        self._recommendation_invalidated = False
        self._last_input_signature = None
        self._auto_slot_refs = {}
        self._manual_plan_draft = False
        self.destroyed.connect(lambda *_: self._cancel_selection())

    def _check_selection_context(self):
        if self._closed or self._read_cancel.is_set() or (
            self.operation_generation and self.operation_generation() != self._selection_generation
        ):
            raise CancelledError("倒带推荐已取消或账号上下文已变化")

    def _attach_read_worker(self, worker):
        # The task outlives a closed modal until its cancellation checkpoint;
        # it must not be destroyed while its QThread is still running.
        _LIVE_READ_WORKERS.add(worker)
        worker.finished.connect(lambda: _LIVE_READ_WORKERS.discard(worker))
        worker.finished.connect(worker.deleteLater)

    def done(self, code):
        self._cancel_selection()
        if self._defer_rewind_done(code):
            return
        super().done(code)

    def _cancel_selection(self):
        self._closed = True
        self._read_cancel.set()
        self._catalog_token = None
        self._analysis_token = None

    def _load_roles_async(self):
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        try:
            self._check_selection_context()
        except CancelledError:
            return
        self._catalog_loading = True
        self._generate_button.setEnabled(False)
        self._target_summary.setText(_ui_text('rewind.selection.load_roles_async.target_summary'))
        self._main_summary.setText(_ui_text('rewind.selection.load_roles_async.main_summary'))
        token = self._catalog_token
        def checkpoint():
            self._check_selection_context()
            if token is not self._catalog_token:
                raise CancelledError("角色目录读取已失效")

        worker = WorkerThread(target=lambda: self._load_role_and_inventory_catalog(checkpoint), parent=None)
        self._roles_worker = worker
        worker.result_ready.connect(lambda result: self._on_roles_loaded(result) if token is self._catalog_token else None)
        worker.error.connect(lambda error: self._on_roles_load_error(error) if token is self._catalog_token else None)
        worker.finished.connect(lambda: setattr(self, "_roles_worker", None)
                                if not self._closed and self._roles_worker is worker else None)
        self._attach_read_worker(worker)
        worker.start()

    def _load_role_and_inventory_catalog(self, checkpoint):
        checkpoint()
        identity = self._service.static_identity()
        roles = self._service.list_target_roles(checkpoint=checkpoint)
        checkpoint()
        counts = self._service.load_owned_shape_counts()
        checkpoint()
        if identity != self._service.static_identity():
            raise ValueError("静态资料已变化，请关闭并重新打开倒带推荐。")
        return RewindUiCatalog(roles, counts)

    def _on_roles_loaded(self, result):
        try:
            self._check_selection_context()
        except CancelledError:
            return
        if isinstance(result, RewindUiCatalog):
            roles, counts = result.roles, result.owned_shape_counts
        else:
            roles, counts = result, ()
        self._roles = tuple(roles)
        self._catalog_loading = False
        self._generate_button.setEnabled(True)
        self._role_names = {role.character_id: role.name for role in self._roles}
        self._selected_slots_by_strategy = {
            key: references_for_roles(self._roles, saved)
            for key, saved in self._saved_slot_ids_by_strategy.items()
        }
        self._auto_slot_refs = references_for_roles(self._roles, {})
        self._install_dual_roles()
        self._last_input_signature = self._input_signature()
        self._set_replacement_inventory_counts(dict(counts))
        self._target_button.setEnabled(True)
        self._main_button.setEnabled(True)
        self._update_role_summaries()

    def _on_roles_load_error(self, message):
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        try:
            self._check_selection_context()
        except CancelledError:
            return
        self._catalog_loading = False
        self._generate_button.setEnabled(True)
        self._target_summary.setText(_ui_text('rewind.selection.on_roles_load_error.target_summary'))
        self._main_summary.setText(_ui_text('rewind.selection.on_roles_load_error.main_summary'))
        self._target_summary.setToolTip(message)

    def _choose_target_roles(self):
        self._choose_roles(False)

    def _choose_main_roles(self):
        self._choose_roles(True)

    def _choose_roles(self, main):
        if not self._roles:
            return
        try:
            self._check_selection_context()
        except CancelledError:
            return
        strategy = "focused" if main else "balanced"
        dialog = _RoleSelectionDialog(self, title=self.text_catalog.render("selector.rewind_main_title" if main else "selector.rewind_target_title"),
            description=self.text_catalog.render("selector.rewind_description"),
            roles=self._roles, selected_character_ids=self._main_character_ids if main else self._target_character_ids,
            selected_slots=self._selected_slots_by_strategy[strategy], asset_root=getattr(self._service, "asset_root", None), catalog=self.text_catalog)
        if dialog.exec() == QDialog.Accepted:
            try:
                self._check_selection_context()
            except CancelledError:
                return
            self._selected_slots_by_strategy[strategy] = dialog.selected_slots()
            chosen = set(dialog.selected_character_ids())
            if main:
                self._main_character_ids = chosen
            else:
                self._target_character_ids = chosen
            self._save_preferences()
            self._update_role_summaries()

    def _input_signature(self):
        return (tuple(sorted(self._target_character_ids)), tuple(sorted(self._main_character_ids)),
                tuple((key, tuple(sorted((identifier, ref.slot_id if ref else None, ref.plan_id if ref else None)
                                        for identifier, ref in slots.items())))
                      for key, slots in sorted(self._selected_slots_by_strategy.items())),
                self._strategy_key, self._target_threshold_mode, self._target_grade, self._target_custom_percent,
                self._dual_signature())

    def _save_preferences(self):
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        try:
            self._check_selection_context()
        except CancelledError:
            return False
        signature = self._input_signature()
        if self._last_input_signature is not None and signature != self._last_input_signature:
            self._invalidate_recommendation()
        self._last_input_signature = signature
        preferences = self._capture_preferences()
        try:
            updated = self._service.save_preferences(preferences, checkpoint=self._check_selection_context)
            self._remember_preferences(updated)
            self._saved_slot_ids_by_strategy = {key: self._slot_preferences(key) for key in ('balanced', 'focused')}
            return True
        except Exception as error:
            QMessageBox.warning(self, _ui_text('rewind.selection.save_preferences.qmessagebox'), self._preference_error_text(error))
            return False

    def _slot_preferences(self, strategy):
        saved = dict(self._saved_slot_ids_by_strategy[strategy])
        selected = self._main_character_ids if strategy == "focused" else self._target_character_ids
        slots = self._selected_slots_by_strategy[strategy]
        explicit = selected | set(saved)
        explicit.update(key for key, ref in slots.items() if ref != self._auto_slot_refs.get(key))
        saved.update({key: ref.slot_id if ref else None for key, ref in slots.items() if key in explicit})
        return saved

    def _begin_manual_draft(self):
        # Explicitly clearing all eight candidates starts a new manual draft,
        # rather than relabelling an old generated recommendation as current.
        self._analysis_token = None
        self._generated_analysis = None
        self._recommendation_invalidated = False
        self._manual_plan_draft = True
        if self._strategy_key == 'dual_target':
            self._dual_manual_draft = True

    def _invalidate_recommendation(self):
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        self._analysis_token = None
        if self._generated_analysis is not None or (
            self._strategy_key == 'dual_target' and self._dual_preferences.get('plan_origin') == 'dual_target'
            and not self._dual_manual_draft
        ):
            self._recommendation_invalidated = True
        self._save_plan_button.setEnabled(False)
        self._sync_dual_overlap_button()
        self._generate_button.setEnabled(True)
        self._generate_button.setText(_ui_text('rewind.selection.invalidate_recommendation.generate_button'))

    def _update_role_summaries(self):
        if not self._roles and self._catalog_loading:
            return
        for ids, label in ((self._target_character_ids, self._target_summary), (self._main_character_ids, self._main_summary)):
            text = self._role_summary(ids, resolve_text_catalog(self.text_catalog).render('rewind.roles.empty'))
            label.setText(text)
            label.setToolTip(self._role_summary(ids, resolve_text_catalog(self.text_catalog).render('rewind.roles.empty'), full=True))

    def _role_summary(self, character_ids, empty_text, *, full=False):
        names = []
        for identifier in sorted(character_ids):
            names.append(self._role_names.get(identifier, str(identifier)))
        if not names:
            return empty_text
        if full or len(names) <= 3:
            return "、".join(names)
        return resolve_text_catalog(self.text_catalog).render("rewind.roles.more", names="、".join(names[:3]), count=len(names))

    def _refresh_analysis(self):
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        try:
            self._check_selection_context()
        except CancelledError:
            return
        if self._catalog_loading:
            return
        if self._strategy_key != "dual_target" and self._target_threshold_mode == "custom" and self._target_custom_percent is None:
            QMessageBox.warning(self, _ui_text('rewind.selection.refresh_analysis.qmessagebox'), plain_tooltip(_ui_text('rewind.selection.refresh_analysis.qmessagebox.body')))
            return
        dual_request = None
        if self._strategy_key == "dual_target":
            try:
                dual_request = self._dual_request()
            except ValueError as error:
                QMessageBox.warning(self, self.text_catalog.render("rewind.dual.invalid_input"), ui_enum_text(self.text_catalog, str(error)))
                return
        token = object()
        self._analysis_token = token
        self._last_input_signature = self._input_signature()
        if self._generated_analysis is not None:
            self._recommendation_invalidated = True
        self._save_plan_button.setEnabled(False)
        self._generate_button.setEnabled(False)
        self._generate_button.setText(_ui_text('rewind.selection.refresh_analysis.generate_button'))
        self._render_loading()
        target_ids, primary_ids = tuple(sorted(self._target_character_ids)), tuple(sorted(self._main_character_ids))
        strategy, grade = self._strategy_key, self._target_grade
        custom = self._target_custom_percent if self._target_threshold_mode == "custom" else None
        active = set(primary_ids if strategy == "focused" else target_ids)
        slots = tuple(ref for key, ref in self._selected_slots_by_strategy.get(strategy, {}).items() if key in active and ref)

        def checkpoint():
            self._check_selection_context()
            if token is not self._analysis_token:
                raise CancelledError("推荐输入已变化")

        def analyze():
            if dual_request is not None:
                return self._service.analyze_dual_target_shortfalls(
                    dual_request, checkpoint=checkpoint, text_catalog=getattr(self, 'text_catalog', None))
            return self._service.analyze_for_targets(
            target_character_ids=target_ids, primary_character_ids=primary_ids, selected_slots=slots,
            strategy=strategy, selection_limit=8, target_grade=grade, target_custom_percent=custom,
            checkpoint=checkpoint)
        worker = WorkerThread(target=analyze, parent=None)
        self._analysis_worker = worker
        worker.result_ready.connect(lambda result: self._on_analysis_ready(token, result))
        worker.error.connect(lambda error: self._on_analysis_error(token, error))
        self._attach_read_worker(worker)
        worker.start()

    def _on_analysis_ready(self, token, analysis):
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        if token is not self._analysis_token or not isinstance(analysis, RewindShapeAnalysis):
            return
        try:
            self._check_selection_context()
            self._validate_analysis(analysis)
        except Exception as error:
            self._on_analysis_error(token, str(error))
            return
        self._generate_button.setEnabled(True)
        self._generate_button.setText(_ui_text('rewind.selection.on_analysis_ready.generate_button'))
        self._generated_analysis = analysis
        self._recommendation_invalidated = False
        self._last_input_signature = self._input_signature()
        # 提示（超出八种形状或全部达标）也照常显示八槽：空槽可点开手选弹窗，按冻结余量自行挑选。
        self._render_plans(analysis)

    def _on_analysis_error(self, token, message):
        _ui_text = resolve_text_catalog(getattr(self, 'text_catalog', None)).render
        if token is not self._analysis_token or self._closed:
            return
        try:
            self._check_selection_context()
        except CancelledError:
            return
        # 服务校验原文是固定说明表，展示时经冻结目录投影；已本地化的多行问题原样显示。
        message = ui_enum_text(self.text_catalog, message)
        self._generate_button.setEnabled(True)
        self._generate_button.setText(_ui_text('rewind.selection.on_analysis_error.generate_button'))
        self._save_plan_button.setEnabled(False)
        QMessageBox.warning(self, _ui_text('rewind.selection.on_analysis_error.qmessagebox'), message)
        self._render_message(resolve_text_catalog(self.text_catalog).render('rewind.results.failed'), message)
