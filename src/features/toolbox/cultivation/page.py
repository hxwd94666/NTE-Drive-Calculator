# 提供工具页内嵌的养成计算器整页容器与返回导航。
"""Full-page shell for the cultivation calculator inside the toolbox."""

from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path
from time import monotonic

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.app.page_tasks import delete_when_page_tasks_finish
from src.app.theme import themed_style
from src.features.toolbox.cultivation.calculator import (
    CultivationCalculatorContent,
)
from src.features.toolbox.cultivation.batch_page import CultivationBatchContent
from src.features.toolbox.cultivation.history_binding import CultivationHistoryDraftBinding
from src.features.toolbox.cultivation.history_controller import CultivationHistoryController, HistoryOperationResult
from src.features.toolbox.cultivation.history_view import CultivationHistoryView
from src.services.progression.history_service import CultivationHistoryService
from src.services.progression.history_adapters import PreparedHistoryRestore, prepare_history_restore
from src.services.progression.planner_service import CultivationPlannerService
from src.services.progression.owned_material_import import ImportedOwnedMaterials
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.integrations.bundled_resources import bundled_game_ui_asset_root
from src.utils.cultivation_trace import trace_cultivation


class CultivationCalculatorPage(QWidget):
    """Own one session draft while the user moves within the toolbox."""

    back_requested = Signal()

    def __init__(
        self,
        service: CultivationPlannerService,
        parent: QWidget | None = None,
        *,
        context_identity: Callable[[], object] | None = None,
        asset_root: str | Path | None = None,
        material_importer: Callable[[], ImportedOwnedMaterials] | None = None,
        history_service: CultivationHistoryService | None = None,
    ) -> None:
        super().__init__(parent)
        self._context_identity = context_identity
        self._initial_identity = context_identity() if context_identity is not None else None
        self._material_importer = material_importer
        self._service = service
        self._asset_root = asset_root
        self._history_service = history_service
        self._history_controller = (CultivationHistoryController(
            history_service, context_identity=context_identity or (lambda: None), parent=self,
        ) if history_service is not None else None)
        self._history_view: CultivationHistoryView | None = None
        self._restore_request = 0
        self.setObjectName("cultivationCalculatorPage")
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 8, 10, 10)
        root.setSpacing(8)
        self._header = self._build_header()
        root.addWidget(self._header)
        self._single_available = False
        self._batch_available = False

        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("cultivationCalculatorPageScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.mode_stack = _CurrentPageStack(self.scroll)
        self.mode_stack.setObjectName("cultivationCalculatorModeStack")
        self.calculator = CultivationCalculatorContent(
            service, self.mode_stack, context_identity=context_identity, asset_root=asset_root,
            history_binding=self._new_history_binding("single"),
        )
        self.batch_calculator = CultivationBatchContent(
            service,
            context_identity=context_identity,
            parent=self.mode_stack,
            asset_root=asset_root,
            history_binding=self._new_history_binding("batch"),
        )
        for content in (self.calculator, self.batch_calculator):
            content.owned_materials.set_import_available(material_importer is not None)
            content.owned_materials.import_requested.connect(self._import_owned_materials)
        self.material_scope.currentIndexChanged.connect(self._set_material_scope)
        self.mode_stack.addWidget(self.calculator)
        self.mode_stack.addWidget(self.batch_calculator)
        self.calculator.plan_available.connect(
            lambda available: self._set_plan_available("single", available)
        )
        self.calculator.calculation_completed.connect(self._queue_single_material_scroll)
        self.batch_calculator.plan_available.connect(
            lambda available: self._set_plan_available("batch", available)
        )
        self.calculator.layout_changed.connect(self._queue_scroll_extent)
        self.batch_calculator.layout_changed.connect(self._queue_scroll_extent)
        self.batch_calculator.result_view_requested.connect(
            self._show_batch_result
        )
        self._scroll_extent_timer = QTimer(self)
        self._scroll_extent_timer.setSingleShot(True)
        self._scroll_extent_timer.timeout.connect(self._sync_scroll_extent)
        self._batch_transition_generation = 0
        self._single_scroll_generation = 0
        self._batch_transition_operation = 0
        self._batch_transition_active = False
        self._batch_retired_serial = 0
        self._batch_retired_pending: set[int] = set()
        self._batch_pending_wheel = 0.0
        self._batch_auto_scroll_requested = False
        self._batch_extent_sample: tuple[int, int] | None = None
        self._batch_stable_samples = 0
        self._batch_probe_queued = False
        self.batch_calculator.result_replaced.connect(self._begin_batch_result_transition)
        self.scroll.setWidget(self.mode_stack)
        self.scroll.viewport().installEventFilter(self)
        self.scroll.verticalScrollBar().installEventFilter(self)
        self._last_scroll_trace = 0.0
        self._wheel_trace_count = 0
        self.scroll.verticalScrollBar().valueChanged.connect(self._scroll_value_changed)
        self._body_stack = QStackedWidget(self)
        self._body_stack.addWidget(self.scroll)
        if self._history_service is not None and self._history_controller is not None:
            history_assets = GameUiAssetCatalog(asset_root if asset_root is not None else bundled_game_ui_asset_root())
            self._history_view = CultivationHistoryView(
                self._history_service, self._history_controller, self._body_stack,
                icon_lookup=history_assets.progression_item_icon,
            )
            self._history_view.back_requested.connect(self._leave_history)
            self._history_view.load_requested.connect(self._load_history)
            self._history_controller.completed.connect(self._history_operation_completed)
            self._body_stack.addWidget(self._history_view)
        root.addWidget(self._body_stack, 1)
        self._set_mode("single")
        self._sync_scroll_extent()

    def _new_history_binding(self, mode: str) -> CultivationHistoryDraftBinding | None:
        if self._history_service is None or self._history_controller is None:
            return None
        binding = CultivationHistoryDraftBinding(self._history_service, self._history_controller, mode=mode, parent=self)
        binding.saved.connect(self._history_saved)
        return binding

    def _history_saved(self, summary: object) -> None:
        if self._history_view is not None:
            self._history_view.notify_saved(summary)

    def _show_history(self) -> None:
        if self._history_view is not None:
            self._cancel_batch_result_transition()
            self._single_scroll_generation += 1
            self._header.hide()
            self._body_stack.setCurrentWidget(self._history_view)
            self._history_view.refresh()

    def _leave_history(self) -> None:
        # Returning to editing cancels a pending restore instead of overwriting later edits.
        self._restore_request = 0
        if self._history_view is not None:
            self._history_view.set_restore_busy(False)
        self._body_stack.setCurrentWidget(self.scroll)
        self._header.show()
        self._sync_scroll_extent()

    def _load_history(self, record: object) -> None:
        if self._history_controller is None or self._restore_request:
            return
        self._restore_request = self._history_controller.submit("restore", partial(prepare_history_restore, self._service, record))
        if self._history_view is not None:
            self._history_view.set_restore_busy(True)
            self._history_view.set_message("正在核对历史配置；原草稿暂时保持不变。")

    def _history_operation_completed(self, outcome: object) -> None:
        if (not isinstance(outcome, HistoryOperationResult) or outcome.operation != "restore"
                or outcome.request_id != self._restore_request):
            return
        self._restore_request = 0
        if self._history_view is not None:
            self._history_view.set_restore_busy(False)
        if outcome.error_code is not None:
            if self._history_view is not None:
                self._history_view.set_message(outcome.message or "历史加载请求已过期。")
            return
        if isinstance(outcome.value, PreparedHistoryRestore):
            self._replace_from_history(outcome.value)

    def _replace_from_history(self, prepared: PreparedHistoryRestore) -> None:
        if prepared.mode not in {"single", "batch"}:
            return
        try:
            if self._context_identity is not None and self._context_identity() != self._initial_identity:
                return
        except (OSError, RuntimeError):
            return
        candidate = None
        binding = None
        try:
            binding = self._new_history_binding(prepared.mode)
            options = dict(context_identity=self._context_identity, asset_root=self._asset_root, history_binding=binding)
            candidate = (CultivationCalculatorContent(self._service, self.mode_stack, **options) if prepared.mode == "single"
                         else CultivationBatchContent(self._service, parent=self.mode_stack, **options))
            candidate.restore_from_history(prepared)
            if self._context_identity is not None and self._context_identity() != self._initial_identity:
                candidate.close_controller()
                delete_when_page_tasks_finish(candidate)
                return
        except Exception:
            if candidate is not None:
                candidate.close_controller()
                delete_when_page_tasks_finish(candidate)
            elif binding is not None:
                binding.close()
            if self._history_view is not None:
                self._history_view.set_message("历史配置未加载；原草稿保持不变，请确认资料兼容性后重试。")
            return
        # Only a completely built candidate replaces the visible draft.
        self._cancel_batch_result_transition()
        self._single_scroll_generation += 1
        mode = prepared.mode
        old = self.calculator if mode == "single" else self.batch_calculator
        index = self.mode_stack.indexOf(old)
        self.mode_stack.removeWidget(old)
        self.mode_stack.insertWidget(index, candidate)
        if mode == "single":
            self.calculator = candidate
            self._single_available = False
            candidate.calculation_completed.connect(self._queue_single_material_scroll)
        else:
            self.batch_calculator = candidate
            self._batch_available = False
            candidate.result_view_requested.connect(self._show_batch_result)
            candidate.result_replaced.connect(self._begin_batch_result_transition)
        candidate.plan_available.connect(lambda available: self._set_plan_available(mode, available))
        candidate.layout_changed.connect(self._queue_scroll_extent)
        candidate.owned_materials.set_import_available(self._material_importer is not None)
        candidate.owned_materials.import_requested.connect(self._import_owned_materials)
        self.material_scope.setCurrentIndex(self.material_scope.findData(candidate.export_history_configuration()["material_scope"]))
        self._set_material_scope()
        self._set_mode(mode)
        old.close_controller()
        old.hide()
        delete_when_page_tasks_finish(old)
        self._leave_history()

    def _import_owned_materials(self) -> None:
        importer = self._material_importer
        if importer is None:
            return
        if (self._context_identity is not None
                and self._context_identity() != self._initial_identity):
            return
        try:
            imported = importer()
        except ValueError as exc:
            message = f"材料导入未完成：{exc}"
        except Exception:
            message = "材料导入未完成：读取同步数据失败，请检查同步状态后重试。"
        else:
            if (self._context_identity is not None
                    and self._context_identity() != self._initial_identity):
                return
            observed = dict(imported.quantities)
            applied = 0
            for content in (self.calculator, self.batch_calculator):
                applied += content.owned_materials.apply_import(
                    observed, source=imported.source, saved_at_utc=imported.saved_at_utc,
                )
            origin = ("账号已保存的抓包材料观测于" if imported.source == "packet" else "原生归档保存于")
            message = (
                f"{origin} {imported.saved_at_utc}；已识别 {len(observed)} 种材料，"
                f"本次草稿更新 {applied} 处。未观测项和手工修改保持原值。"
            )
            if imported.skipped_item_count:
                message += f" {imported.skipped_item_count} 种记录有冲突或字段异常，已跳过。"
        for content in (self.calculator, self.batch_calculator):
            content.owned_materials.set_import_status(message)

    def _build_header(self) -> QWidget:
        header = QFrame(self)
        header.setObjectName("cultivationCalculatorPageHeader")
        header.setStyleSheet(themed_style(
            "QFrame#cultivationCalculatorPageHeader{background:#161b22;"
            "border:1px solid #30363d;border-radius:10px;}"
        ))
        row = QHBoxLayout(header)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(8)

        back = QPushButton("‹ 返回工具", header)
        back.setObjectName("cultivationCalculatorBack")
        back.clicked.connect(self.back_requested)
        row.addWidget(back)

        title = QLabel("养成计算器", header)
        title.setObjectName("cultivationCalculatorPageTitle")
        title.setStyleSheet(themed_style(
            "color:#f0f6fc;font-size:18px;font-weight:900"
        ))
        row.addWidget(title)

        self.single_mode_button = QPushButton("单角色", header)
        self.single_mode_button.setObjectName("cultivationSingleMode")
        self.single_mode_button.clicked.connect(lambda: self._set_mode("single"))
        row.addWidget(self.single_mode_button)

        self.batch_mode_button = QPushButton("多角色", header)
        self.batch_mode_button.setObjectName("cultivationMultiMode")
        self.batch_mode_button.clicked.connect(lambda: self._set_mode("batch"))
        row.addWidget(self.batch_mode_button)
        row.addStretch(1)

        row.addWidget(QLabel("材料范围", header))
        self.material_scope = QComboBox(header)
        self.material_scope.setObjectName("cultivationMaterialScope")
        self.material_scope.addItem("全部", "all")
        self.material_scope.addItem("仅体力", "stamina")
        self.material_scope.setCurrentIndex(1)
        self.material_scope.setToolTip("仅体力只展示正式副本中消耗体力且有确定产出的材料")
        row.addWidget(self.material_scope)

        reset = QPushButton("重置", header)
        reset.setObjectName("cultivationCalculatorReset")
        reset.clicked.connect(self._reset)
        row.addWidget(reset)

        history = QPushButton("历史记录", header)
        history.setObjectName("cultivationHistoryOpen")
        history.setEnabled(self._history_service is not None)
        history.clicked.connect(self._show_history)
        row.addWidget(history)

        self.copy_button = QPushButton("复制清单", header)
        self.copy_button.setObjectName("cultivationCalculatorCopy")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy)
        row.addWidget(self.copy_button)
        return header

    def _set_material_scope(self) -> None:
        scope = str(self.material_scope.currentData())
        self.calculator.set_material_scope(scope)
        self.batch_calculator.set_material_scope(scope)

    def _reset(self) -> None:
        self._cancel_batch_result_transition()
        self._single_scroll_generation += 1
        self._active_content().reset_draft()
        self.scroll.verticalScrollBar().setValue(0)

    def _copy(self) -> None:
        self._active_content().copy_plan()

    def _set_plan_available(self, mode: str, available: bool) -> None:
        if mode == "single":
            self._single_available = available
        else:
            self._batch_available = available
        self.copy_button.setEnabled(
            self._single_available if self._mode() == "single" else self._batch_available
        )

    def _queue_single_material_scroll(self) -> None:
        self._single_scroll_generation += 1
        generation = self._single_scroll_generation
        QTimer.singleShot(0, self, lambda: self._scroll_to_owned_materials(generation))

    def _scroll_to_owned_materials(self, generation: int) -> None:
        if generation != self._single_scroll_generation or self._mode() != "single":
            return
        self._sync_scroll_extent()
        owned = self.calculator.owned_materials
        position = owned.mapTo(self.mode_stack, QPoint(0, 0)).y()
        bar = self.scroll.verticalScrollBar()
        bar.setValue(max(0, min(bar.maximum(), position - 12)))

    def _set_mode(self, mode: str) -> None:
        self._cancel_batch_result_transition()
        self._single_scroll_generation += 1
        batch = mode == "batch"
        self.mode_stack.setCurrentWidget(
            self.batch_calculator if batch else self.calculator
        )
        self.single_mode_button.setStyleSheet(_mode_style(not batch))
        self.batch_mode_button.setStyleSheet(_mode_style(batch))
        self.copy_button.setEnabled(
            self._batch_available if batch else self._single_available
        )
        self.scroll.verticalScrollBar().setValue(0)
        self._queue_scroll_extent()

    def _mode(self) -> str:
        return "batch" if self.mode_stack.currentWidget() is self.batch_calculator else "single"

    def _active_content(self) -> CultivationCalculatorContent | CultivationBatchContent:
        return self.batch_calculator if self._mode() == "batch" else self.calculator

    def _sync_scroll_extent(self) -> None:
        content = self._active_content()
        content.setMinimumHeight(0)
        layout = content.layout()
        if layout is not None:
            layout.activate()
        content.setMinimumHeight(content.sizeHint().height())
        self.mode_stack.setMinimumHeight(content.sizeHint().height())
        if self._mode() == "batch" and self._batch_available:
            trace_cultivation(self.batch_calculator._active_trace_id, "ui.scroll_extent_ready", height=content.minimumHeight())

    def _queue_scroll_extent(self) -> None:
        if self._batch_transition_active:
            return
        if not self._scroll_extent_timer.isActive():
            self._scroll_extent_timer.start(0)

    def _cancel_batch_result_transition(self) -> None:
        self._batch_transition_generation += 1
        self._batch_transition_operation = 0
        self._batch_transition_active = False
        self._batch_pending_wheel = 0.0
        self._batch_auto_scroll_requested = False
        self._batch_extent_sample = None
        self._batch_stable_samples = 0
        self._batch_probe_queued = False
        self.scroll.verticalScrollBar().setEnabled(True)

    def _begin_batch_result_transition(self, operation: int, retired: object) -> None:
        carry_auto = (self._batch_transition_active
                      and self._batch_transition_operation == operation
                      and self.batch_calculator._last_plan is not None
                      and self._batch_auto_scroll_requested)
        carry_wheel = (self._batch_pending_wheel
                       if self._batch_transition_active
                       and self._batch_transition_operation == operation else 0.0)
        self._cancel_batch_result_transition()
        if self._mode() != "batch":
            return
        generation = self._batch_transition_generation
        widgets = tuple(retired) if isinstance(retired, tuple) else ()
        self._batch_transition_operation = operation
        self._batch_transition_active = True
        self._batch_auto_scroll_requested = carry_auto
        self._batch_pending_wheel = carry_wheel
        self._scroll_extent_timer.stop()
        self.scroll.verticalScrollBar().setEnabled(False)
        trace_cultivation(operation, "ui.result_transition_begin", retired=len(widgets))
        for widget in widgets:
            self._batch_retired_serial += 1
            serial = self._batch_retired_serial
            self._batch_retired_pending.add(serial)
            widget.destroyed.connect(
                lambda _obj=None, marker=serial: self._batch_retired_destroyed(marker)
            )
        self._queue_batch_settle(generation)

    def _batch_retired_destroyed(self, serial: int) -> None:
        if serial not in self._batch_retired_pending:
            return
        self._batch_retired_pending.remove(serial)
        if self._batch_transition_active and not self._batch_retired_pending:
            trace_cultivation(self.batch_calculator._active_trace_id, "ui.old_result_destroyed")
            self._queue_batch_settle(self._batch_transition_generation)

    def _queue_batch_settle(self, generation: int) -> None:
        if (generation != self._batch_transition_generation or not self._batch_transition_active
                or self._batch_retired_pending or self._batch_probe_queued):
            return
        self._batch_probe_queued = True
        QTimer.singleShot(16, self, lambda: self._settle_batch_result(generation))

    def _settle_batch_result(self, generation: int) -> None:
        if generation != self._batch_transition_generation or not self._batch_transition_active:
            return
        self._batch_probe_queued = False
        if self._batch_retired_pending:
            return
        self._sync_scroll_extent()
        extent = (self.batch_calculator.sizeHint().height(),
                  self.scroll.verticalScrollBar().maximum())
        if extent == self._batch_extent_sample:
            self._batch_stable_samples += 1
        else:
            self._batch_extent_sample = extent
            self._batch_stable_samples = 0
        if self._batch_stable_samples < 1:
            self._queue_batch_settle(generation)
            return
        self._batch_transition_active = False
        bar = self.scroll.verticalScrollBar()
        bar.setEnabled(True)
        if self._mode() == "batch" and self._batch_auto_scroll_requested:
            self.scroll.ensureWidgetVisible(self.batch_calculator._result, 0, 16)
            trace_cultivation(self.batch_calculator._active_trace_id, "ui.auto_scroll_end")
        if self._mode() == "batch" and self._batch_pending_wheel:
            bar.setValue(bar.value() - round(self._batch_pending_wheel))
        trace_cultivation(self.batch_calculator._active_trace_id, "ui.result_transition_ready",
                          height=extent[0], buffered_wheel=round(self._batch_pending_wheel))
        self._batch_pending_wheel = 0.0

    def _show_batch_result(self) -> None:
        if self._mode() != "batch":
            return
        self._wheel_trace_count = 0
        trace_cultivation(self.batch_calculator._active_trace_id, "ui.auto_scroll_begin")
        self._batch_auto_scroll_requested = True
        self._queue_batch_settle(self._batch_transition_generation)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        if (self._batch_transition_active and self._mode() == "batch"
                and watched in (self.scroll.viewport(), self.scroll.verticalScrollBar())
                and event.type() == QEvent.Type.Wheel):
            bar = self.scroll.verticalScrollBar()
            delta = event.pixelDelta().y()
            if not delta:
                lines = QApplication.wheelScrollLines()
                distance = bar.pageStep() if lines < 0 else lines * bar.singleStep()
                delta = event.angleDelta().y() / 120 * distance
            self._batch_pending_wheel += delta
            trace_cultivation(self.batch_calculator._active_trace_id, "ui.wheel_buffered")
            event.accept()
            return True
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Wheel and self._mode() == "batch" and self._batch_available:
            if self._wheel_trace_count < 3:
                trace_cultivation(self.batch_calculator._active_trace_id, "ui.wheel_received")
            self._wheel_trace_count += 1
        return super().eventFilter(watched, event)

    def _scroll_value_changed(self, value: int) -> None:
        now = monotonic()
        if self._mode() != "batch" or not self._batch_available or now - self._last_scroll_trace < 0.15:
            return
        self._last_scroll_trace = now
        trace_cultivation(self.batch_calculator._active_trace_id, "ui.scroll_changed", value=value)

    def closeEvent(self, event) -> None:
        self.shutdown()
        super().closeEvent(event)

    def shutdown(self) -> None:
        self._cancel_batch_result_transition()
        self._batch_retired_pending.clear()
        self.calculator.close_controller()
        self.batch_calculator.close_controller()
        if self._history_controller is not None:
            self._history_controller.close()


def _mode_style(active: bool) -> str:
    if active:
        return themed_style(
            "color:#fff;background:#238636;border:1px solid #3fb950;"
            "border-radius:6px;padding:5px 10px;font-weight:800"
        )
    return themed_style(
        "color:#8b949e;background:#0d1117;border:1px solid #30363d;"
        "border-radius:6px;padding:5px 10px;font-weight:700"
    )


class _CurrentPageStack(QStackedWidget):
    """Size the scroll surface from the visible mode rather than the largest draft."""

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        current = self.currentWidget()
        return current.sizeHint() if current is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        current = self.currentWidget()
        return current.minimumSizeHint() if current is not None else super().minimumSizeHint()


__all__ = ["CultivationCalculatorPage"]
