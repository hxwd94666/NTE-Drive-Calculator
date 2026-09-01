# 控制单件识别页面的输入、解析和结果保存。
"""MainWindow methods for identification."""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QCompleter,
    QFileDialog,
    QMessageBox,
    QScrollArea,
    QWidget,
)

from src.i18n import tr
from src.app.theme import current_style_sheet
from src.app.context import AppContext
from src.app.workers import WorkerThread
from src.features.identification.lifecycle import (
    cleanup_pending_identify_clipboard_files as _cleanup_pending_identify_clipboard_files,
    current_identification_dependencies as _current_identification_dependencies,
    identification_is_running as _identification_is_running,
    task_identification_dependencies as _task_identification_dependencies,
)
from src.features.identification.dependencies import (
    IdentificationDependencies,
)
from src.features.identification.manual_parsing import (
    IdentificationManualParsingMixin,
)
from src.features.identification.dialogs import (
    choose_identify_image_options,
    confirm_identify_tape_main_stats,
)
from src.features.identification.page import (
    build_identify_page,
    build_identify_result_row,
    parse_identify_paths,
    refresh_identify_previews,
    render_identify_result_page,
    show_identify_preview_image,
)
from src.features.identification.operation_logging import (
    begin_identification_operation as _begin_identification_operation,
    identification_event as _identification_event,
)
from src.features.identification.temp_files import is_identify_clipboard_file
from src.models.equipment import Drive, Tape
from src.integrations.global_hotkeys import GlobalHotkeyManager
from src.observability.context import OperationContext
from src.observability.operation import log_event
from src.optimizer.scoring import ScoringEngine
from src.scanner.batch_processor import BatchProcessor
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.services.equipment_identification_service import (
    EquipmentIdentificationService,
)
from src.ui.plain_text_edit import PlainTextOnlyTextEdit
from src.ui.equipment_presentation import EquipmentPresentation
from src.ui.widgets import SearchableComboBox
from src.utils.logger import logger
from src.utils.name_resolver import resolve_name

__all__ = ["IdentificationController"]


class IdentificationController(IdentificationManualParsingMixin, QObject):
    identify_capture_signal = Signal(str)
    identify_capture_done_signal = Signal()

    def __init__(
        self,
        *,
        app_context: AppContext,
        dialog_parent: QWidget,
        card_factory: Callable[..., Any],
        equipment_presentation: EquipmentPresentation,
        hotkey_manager: GlobalHotkeyManager,
        minimize_window: Callable[[], None],
        restore_window: Callable[[], None],
        activate_window: Callable[[], None],
    ) -> None:
        super().__init__(dialog_parent)
        self.app_context = app_context
        self._dialog_parent = dialog_parent
        self._card_factory = card_factory
        self._equipment_presentation = equipment_presentation
        self._hotkey_manager = hotkey_manager
        self._minimize_window = minimize_window
        self._restore_window = restore_window
        self._activate_window = activate_window
        self._shape_areas: dict[str, int] = {}
        self.all_set_names: list[str] = []
        self.scoring_engine: ScoringEngine | None = None
        self._identify_blueprint_cache: tuple[NTEPipelineOrchestrator, dict[str, Any]] | None = None
        self._identify_parse_worker: WorkerThread | None = None
        self._identify_worker: WorkerThread | None = None
        self._identify_dependencies: IdentificationDependencies | None = None
        self._pending_identify_clipboard_cleanup: list[Path] = []
        self.identify_capture_signal.connect(self._add_identify_capture_path)
        self.identify_capture_done_signal.connect(self._finish_identify_capture_mode)

    def build_page(self) -> QScrollArea:
        return self._page_identify()

    def refresh_options(self) -> None:
        self._refresh_identify_options()

    def update_catalog(
        self,
        *,
        shape_areas: dict[str, int],
        set_names: list[str],
        scoring_engine: ScoringEngine,
    ) -> None:
        self._shape_areas = dict(shape_areas)
        self.all_set_names = list(set_names)
        self.scoring_engine = scoring_engine
        self._identify_blueprint_cache = None
        self.refresh_options()

    def reset_account_state(self) -> None:
        self._identify_blueprint_cache = None
        if hasattr(self, "_identify_capture_dir"):
            self._identify_capture_dir = self.app_context.account.account_data_root / "identify_captures"

    def is_running(self) -> bool:
        return (
            _identification_is_running(self)
            or self._hotkey_manager.active_owner == "identification"
        )

    def capture_foreground(self) -> None:
        self._capture_identify_foreground()

    def finish_capture(self) -> None:
        self.identify_capture_done_signal.emit()

    def _card(self, *args: Any, **kwargs: Any) -> Any:
        return self._card_factory(*args, **kwargs)

    def _equip_card(self, *args: Any, **kwargs: Any) -> Any:
        return self._equipment_presentation.equipment_card(*args, **kwargs)

    def _start_capture_hotkeys(self) -> None:
        self._hotkey_manager.start(
            owner="identification",
            on_stop=self._on_capture_stop_hotkey,
            on_capture=self.capture_foreground,
            on_finish=self.finish_capture,
        )

    def _stop_capture_hotkeys(self) -> None:
        self._hotkey_manager.stop(owner="identification")

    def _on_capture_stop_hotkey(self) -> None:
        configuration = self._hotkey_manager.configuration
        logger.warning(
            "收到停止热键 {}；连续截图鉴定请使用完成热键 {} 返回。",
            configuration.stop,
            configuration.finish,
        )

    def showMinimized(self) -> None:
        self._minimize_window()

    def showNormal(self) -> None:
        self._restore_window()

    def activateWindow(self) -> None:
        self._activate_window()

    def _choose_identify_image_options(
        self,
        path: Path,
    ) -> dict[str, Any] | None:
        return choose_identify_image_options(
            self,
            path,
            parent=self._dialog_parent,
        )

    def _confirm_identify_tape_main_stats(self, items):
        return confirm_identify_tape_main_stats(
            self,
            items,
            parent=self._dialog_parent,
        )

    def _page_identify(self):
        return build_identify_page(self, PlainTextOnlyTextEdit)

    def _refresh_identify_options(self):
        if not hasattr(self, "ident_shape_combo"):
            return

        current_shape = self.ident_shape_combo.currentData()
        self.ident_shape_combo.blockSignals(True)
        self.ident_shape_combo.clear()
        for sid in sorted(
            [s for s in self._shape_areas.keys() if s != "TAPE_15"], key=lambda x: (self._shape_areas.get(x, 0), x)
        ):
            self.ident_shape_combo.addItem(
                tr("{shape} ({cells}格)", shape=sid, cells=self._shape_areas.get(sid, 0)),
                sid,
            )
        idx = self.ident_shape_combo.findData(current_shape)
        if idx >= 0:
            self.ident_shape_combo.setCurrentIndex(idx)
        self.ident_shape_combo.blockSignals(False)
        self._make_combo_searchable(self.ident_shape_combo)

        current_set = self.ident_set_combo.currentData()
        self.ident_set_combo.blockSignals(True)
        self.ident_set_combo.clear()
        for set_name in self.all_set_names:
            self.ident_set_combo.addItem(set_name, set_name)
        idx = self.ident_set_combo.findData(current_set)
        if idx >= 0:
            self.ident_set_combo.setCurrentIndex(idx)
        self.ident_set_combo.blockSignals(False)
        self._make_combo_searchable(self.ident_set_combo)

        current_main = self.ident_main_combo.currentData()
        self.ident_main_combo.blockSignals(True)
        self.ident_main_combo.clear()
        for stat_name in self._get_tape_main_stats_pool():
            self.ident_main_combo.addItem(stat_name, stat_name)
        idx = self.ident_main_combo.findData(current_main)
        if idx >= 0:
            self.ident_main_combo.setCurrentIndex(idx)
        self.ident_main_combo.blockSignals(False)
        self._make_combo_searchable(self.ident_main_combo)

    def _on_identify_type_changed(self):
        is_tape = hasattr(self, "ident_tape_rb") and self.ident_tape_rb.isChecked()
        if hasattr(self, "ident_shape_row"):
            self.ident_shape_row.setVisible(not is_tape)
        if hasattr(self, "ident_tape_row"):
            self.ident_tape_row.setVisible(is_tape)

    def _get_tape_main_stats_pool(self):
        dependencies = _current_identification_dependencies(self)
        try:
            with open(dependencies.config_dir / "stats.json", "r", encoding="utf-8") as f:
                return json.load(f).get("tape_main_stats_pool", [])
        except Exception:
            return []

    def _set_combo_data(self, combo, value):
        if value is None:
            return
        if isinstance(combo, SearchableComboBox):
            combo.refresh_search_items()
        idx = combo.findData(value)
        if idx < 0:
            idx = combo.findText(str(value))
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _make_combo_searchable(self, combo):
        if isinstance(combo, SearchableComboBox):
            combo.refresh_search_items()
            return
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.NoInsert)
        items = [combo.itemText(i) for i in range(combo.count())]
        completer = QCompleter(items, combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        combo.setCompleter(completer)

    def _combo_data_or_resolved_text(self, combo, choices=None):
        data = combo.currentData()
        if data:
            return data
        text = combo.currentText().strip()
        for i in range(combo.count()):
            if text == combo.itemText(i):
                return combo.itemData(i) or combo.itemText(i)
        if choices:
            return resolve_name(text, choices, cutoff=0.55) or text
        return text

    def _identify_quality(self):
        return self.ident_quality_combo.currentData() or "Gold"

    def _clear_identify_input(self):
        self.ident_path_edit.clear()
        self.ident_manual_text.clear()
        self._clear_identify_results()
        self.ident_summary.setText(tr("等待输入装备数据"))
        self._refresh_identify_previews()

    def _clear_identify_results(self):
        while self.ident_result_layout.count():
            it = self.ident_result_layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._delete_layout(it.layout())

    def _delete_layout(self, layout):
        while layout.count():
            it = layout.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                self._delete_layout(it.layout())

    def _set_identify_busy(self, busy, msg=None):
        if msg:
            self.ident_summary.setText(msg)
        for btn in (getattr(self, "ident_parse_btn", None), getattr(self, "ident_manual_btn", None)):
            if btn:
                btn.setEnabled(not busy)

    def _identify_paths_from_text(self):
        return parse_identify_paths(self.ident_path_edit.text())

    def _refresh_identify_previews(self, *_):
        return refresh_identify_previews(self, self._identify_paths_from_text())

    def _show_identify_preview_image(self, path: Path) -> None:
        show_identify_preview_image(
            self._dialog_parent,
            path,
            current_style_sheet(),
        )

    def _remove_identify_preview_path(self, path: Path) -> None:
        paths = [p for p in self._identify_paths_from_text() if p != path]
        self.ident_path_edit.setText(";".join(str(p) for p in paths))

    def _identify_start(self):
        self._identify_dependencies = _current_identification_dependencies(self)
        has_images = bool(self._identify_paths_from_text())
        _begin_identification_operation(
            self,
            self._identify_dependencies,
            input_source="image" if has_images else "manual",
        )
        if has_images:
            self._identify_from_image_path()
        else:
            self._identify_from_manual()

    def _start_identify_capture_mode(self):
        dependencies = _current_identification_dependencies(self)
        hotkeys = self._hotkey_manager.configuration
        self._identify_dependencies = dependencies
        self._identify_capture_operation_context = OperationContext.create(
            "identification_capture",
            account_id=dependencies.account_id,
            context_generation=dependencies.generation,
        )
        log_event(
            "INFO",
            "identification.capture_started",
            "开始连续截图鉴定",
            self._identify_capture_operation_context,
        )
        QMessageBox.information(
            self._dialog_parent,
            tr("截图鉴定"),
            tr("点击 OK 后请切回游戏。\n\n按 {capture} 连续截图，按 {finish} 完成并返回鉴定页。",
               capture=hotkeys.capture, finish=hotkeys.finish),
        )
        self._identify_capture_dir = dependencies.account_data_root / "identify_captures"
        self._identify_capture_dir.mkdir(parents=True, exist_ok=True)
        self._identify_capture_count = 0
        self.showMinimized()
        self._start_capture_hotkeys()
        self.ident_summary.setText(
            tr("截图鉴定已启动：{capture} 截图，{finish} 完成",
               capture=hotkeys.capture, finish=hotkeys.finish)
        )

    def _capture_identify_foreground(self):
        dependencies = _task_identification_dependencies(self)
        try:
            import mss
            import mss.tools
            from src.scanner.window_capture import capture_foreground_window

            with mss.MSS() as sct:
                screenshot, _ = capture_foreground_window(sct)
                self._identify_capture_count = getattr(self, "_identify_capture_count", 0) + 1
                filename = f"identify_capture_{int(time.time() * 1000)}_{self._identify_capture_count:04d}.png"
                path = (
                    getattr(
                        self,
                        "_identify_capture_dir",
                        dependencies.account_data_root / "identify_captures",
                    )
                    / filename
                )
                path.parent.mkdir(parents=True, exist_ok=True)
                mss.tools.to_png(screenshot.rgb, screenshot.size, output=str(path))
            logger.success(f"鉴定截图成功: {path.name}")
            self.identify_capture_signal.emit(str(path))
        except Exception as e:
            logger.error(f"鉴定截图失败: {e}")
            log_event(
                "ERROR",
                "identification.capture_failed",
                "鉴定截图失败",
                getattr(
                    self,
                    "_identify_capture_operation_context",
                    OperationContext.create("identification_capture"),
                ),
                error=e,
            )

    def _add_identify_capture_path(self, path_text):
        paths = [str(p) for p in self._identify_paths_from_text()]
        paths.append(path_text)
        self.ident_path_edit.setText(";".join(paths))

    def _finish_identify_capture_mode(self):
        self._stop_capture_hotkeys()
        self.showNormal()
        self.activateWindow()
        count = getattr(self, "_identify_capture_count", 0)
        log_event(
            "INFO",
            "identification.capture_succeeded",
            "连续截图鉴定采集完成",
            getattr(
                self,
                "_identify_capture_operation_context",
                OperationContext.create("identification_capture"),
            ),
            captured_count=count,
        )
        self.ident_summary.setText(tr("已完成鉴定截图 {count} 张，点击开始鉴定继续。", count=count))

    def _identify_choose_file(self):
        dependencies = _current_identification_dependencies(self)
        paths, _ = QFileDialog.getOpenFileNames(
            self._dialog_parent,
            "选择装备截图",
            str(dependencies.screenshot_dir),
            "Images (*.png *.jpg *.jpeg *.bmp)",
        )
        if paths:
            self.ident_path_edit.setText(";".join(paths))

    def _identify_from_clipboard(self):
        dependencies = _current_identification_dependencies(self)
        self._identify_dependencies = dependencies
        cb = QApplication.clipboard()
        mime = cb.mimeData()
        if mime and mime.hasImage():
            img = cb.image()
            if not img.isNull():
                clip_path = dependencies.account_data_root / f"identify_clipboard_{int(time.time() * 1000)}.png"
                img.save(str(clip_path))
                self.ident_path_edit.setText(str(clip_path))
                return

        text = (cb.text() or "").strip()
        if not text:
            QMessageBox.information(
                self._dialog_parent,
                tr("粘贴"),
                tr("剪贴板中没有图片、路径或文本数据。"),
            )
            return
        maybe_paths = [
            Path(os.path.expandvars(part.strip().strip('"'))) for part in re.split(r"[;\n]+", text) if part.strip()
        ]
        if maybe_paths and all(path.exists() for path in maybe_paths):
            self.ident_path_edit.setText(";".join(str(path) for path in maybe_paths))
        else:
            self.ident_manual_text.setPlainText(text)
            self._apply_identify_manual_fields(text)

    def _identify_from_image_path(self):
        dependencies = _task_identification_dependencies(self)
        paths = self._identify_paths_from_text()
        if not paths:
            QMessageBox.warning(
                self._dialog_parent,
                tr("鉴定"),
                tr("请先选择或粘贴图片路径。"),
            )
            return
        missing = [str(path) for path in paths if not path.exists()]
        if missing:
            QMessageBox.warning(
                self._dialog_parent,
                tr("鉴定"),
                tr("图片不存在：{path}", path=missing[0]),
            )
            return
        image_jobs = []
        for path in paths:
            options = self._choose_identify_image_options(path)
            if options is None:
                return
            image_jobs.append((path, options))
        self._pending_identify_clipboard_cleanup = [
            path for path, _options in image_jobs if is_identify_clipboard_file(path, dependencies.account_data_root)
        ]
        self._set_identify_busy(True, "正在解析图片...")
        worker = WorkerThread(
            target=lambda: self._parse_identify_images(image_jobs),
            parent=self,
        )
        self._identify_parse_worker = worker
        worker.result_ready.connect(self._on_identify_items_loaded)
        worker.error.connect(self._on_identify_error)
        worker.start()

    def _parse_identify_images(
        self,
        image_jobs: list[tuple[Path, dict[str, Any]]],
    ) -> list[Drive | Tape]:
        dependencies = _task_identification_dependencies(self)
        p = BatchProcessor(
            input_dir=str(dependencies.screenshot_dir),
            output_file=str(dependencies.user_config_dir / "identify_preview.json"),
            config_dir=str(dependencies.config_dir),
        )
        items = []
        for path, options in image_jobs:
            items.extend(
                p.parse_identify_items(
                    str(path),
                    forced_type=options.get("type"),
                    forced_shape_id=options.get("shape_id"),
                    forced_set_name=options.get("set_name"),
                    forced_main_stat=options.get("main_stat"),
                )
            )
        return items

    def _on_identify_items_loaded(self, items):
        self._set_identify_busy(False)
        _cleanup_pending_identify_clipboard_files(self)
        if not items:
            _identification_event(
                self,
                "INFO",
                "identification.succeeded",
                "图片解析完成但未识别到装备",
                item_count=0,
                result="no_items",
            )
            QMessageBox.warning(
                self._dialog_parent,
                tr("鉴定"),
                tr("未从图片中识别到可鉴定的驱动或卡带。"),
            )
            return
        if not self._confirm_identify_tape_main_stats(items):
            _identification_event(
                self,
                "WARNING",
                "identification.cancelled",
                "用户取消卡带主词条确认",
                item_count=len(items),
            )
            self.ident_summary.setText(tr("已取消鉴定"))
            return
        self._load_identify_item_to_form(items[0])
        self._start_identify_items(items)

    def _load_identify_item_to_form(self, item):
        if isinstance(item, Tape):
            self.ident_tape_rb.setChecked(True)
            set_name = resolve_name(item.set_name, self.all_set_names, cutoff=0.78) or item.set_name
            self._set_combo_data(self.ident_set_combo, set_name)
            self._set_combo_data(self.ident_main_combo, item.main_stats)
        else:
            self.ident_drive_rb.setChecked(True)
            self._set_combo_data(self.ident_shape_combo, item.shape_id)
        self._set_combo_data(self.ident_quality_combo, item.quality)
        self.ident_manual_text.setPlainText("\n".join(f"{k}: {v}" for k, v in item.sub_stats.items()))
        self._on_identify_type_changed()

    def _identify_from_manual(self):
        self._identify_dependencies = _current_identification_dependencies(self)
        text = self.ident_manual_text.toPlainText()
        self._apply_identify_manual_fields(text)
        quality = self._identify_quality()
        uid = f"identify_{int(time.time() * 1000)}"
        try:
            if self.ident_tape_rb.isChecked():
                # 卡带副词条沿用扫描管线的 10 格当量，展示值与游戏同品质卡带一致。
                sub_stats = self._parse_manual_stats(text, quality=quality, grid_equivalent=10)
                set_name = self._combo_data_or_resolved_text(self.ident_set_combo, self.all_set_names)
                set_name = resolve_name(set_name, self.all_set_names, cutoff=0.78) or set_name
                main_stat = self._combo_data_or_resolved_text(self.ident_main_combo, self._get_tape_main_stats_pool())
                item = Tape(
                    uid=uid,
                    item_type="tape",
                    shape_id="TAPE_15",
                    area=15,
                    quality=quality,
                    set_name=set_name,
                    main_stats=main_stat,
                    sub_stats=sub_stats,
                )
            else:
                shape_id = self._combo_data_or_resolved_text(self.ident_shape_combo, self._shape_areas.keys()).split()[
                    0
                ]
                area = self._shape_areas.get(shape_id, 3)
                sub_stats = self._parse_manual_stats(text, quality=quality, grid_equivalent=area)
                item = Drive(
                    uid=uid,
                    item_type="drive",
                    shape_id=shape_id,
                    area=area,
                    quality=quality,
                    main_stats=self._manual_drive_main_stats(area, quality),
                    sub_stats=sub_stats,
                )
        except Exception as e:
            _identification_event(
                self,
                "ERROR",
                "identification.failed",
                "手工鉴定输入无效",
                stage="manual_parse",
                error=e,
            )
            QMessageBox.critical(
                self._dialog_parent,
                tr("鉴定"),
                tr("装备数据无效：\n{error}", error=e),
            )
            return
        self._start_identify_item(item)

    def _start_identify_item(self, item):
        self._start_identify_items([item])

    def _start_identify_items(self, items):
        self._set_identify_busy(True, "正在匹配角色图纸并评分...")
        worker = WorkerThread(
            target=lambda: self._run_identify_items(items),
            parent=self,
        )
        self._identify_worker = worker
        worker.result_ready.connect(self._render_identify_result)
        worker.error.connect(self._on_identify_error)
        worker.start()

    def _get_identify_blueprints(
        self,
    ) -> tuple[NTEPipelineOrchestrator, dict[str, Any]]:
        dependencies = _task_identification_dependencies(self)
        if self._identify_blueprint_cache:
            return self._identify_blueprint_cache
        orchestrator = NTEPipelineOrchestrator(
            config_dir=str(dependencies.config_dir),
            user_database_path=dependencies.user_database_path,
        )
        roles = list(orchestrator.roles_db.keys())
        blueprints = orchestrator.solve_blueprints(roles)
        self._identify_blueprint_cache = (orchestrator, blueprints)
        return self._identify_blueprint_cache

    def _run_identify_item(self, item):
        dependencies = _task_identification_dependencies(self)
        orchestrator, blueprints = self._get_identify_blueprints()
        scoring = ScoringEngine(
            str(dependencies.config_dir),
            user_database_path=dependencies.user_database_path,
        )
        return EquipmentIdentificationService(
            orchestrator,
            blueprints,
            scoring,
        ).identify_item(item)

    def _run_identify_items(self, items):
        return [self._run_identify_item(item) for item in items]

    def _render_identify_result(self, data):
        pages = data if isinstance(data, list) else [data]
        _identification_event(
            self,
            "INFO",
            "identification.succeeded",
            "单件鉴定完成",
            item_count=len(pages),
            matched_role_count=sum(len(page.get("rows") or []) for page in pages if isinstance(page, dict)),
        )
        if isinstance(data, list):
            self._identify_result_pages = data
            self._identify_result_page_index = 0
            self._render_identify_result_page()
            return
        self._identify_result_pages = [data]
        self._identify_result_page_index = 0
        self._render_identify_result_page()

    def _render_identify_result_page(self):
        return render_identify_result_page(self, getattr(self, "_identify_result_pages", []))

    def _set_identify_result_page(self, index):
        self._identify_result_page_index = index
        self._render_identify_result_page()

    def _identify_result_row(self, rank, row):
        return build_identify_result_row(
            rank,
            row,
            game_ui_asset_root=self.app_context.paths.asset_dir / "game_ui",
        )

    def _on_identify_error(self, err):
        _identification_event(
            self,
            "ERROR",
            "identification.failed",
            "单件鉴定失败",
            stage="background_worker",
            error=err,
        )
        self._set_identify_busy(False)
        _cleanup_pending_identify_clipboard_files(self)
        QMessageBox.critical(
            self._dialog_parent,
            tr("鉴定失败"),
            str(err),
        )
