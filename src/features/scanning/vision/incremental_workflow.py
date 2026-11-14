# 绑定增量采集 worker 与冻结账号，将标记身份和停止 token 交接到图片解析。
from src.ui.common.text_catalog import plain_tooltip, resolve_text_catalog
from PySide6.QtWidgets import QMessageBox

from src.app.workers import ScanWorkerThread
from src.features.input_operation_entry import request_input_entry, show_input_unavailable
from src.features.scanning.dependencies import current_scanning_dependencies, scanning_dependencies_are_current
from src.features.scanning.operation_logging import begin_scan_operation, scan_event
from src.integrations.vision.recognition.image_resources import CaptureTemplateFile


def start_incremental_capture(owner, mode, *, text_catalog=None):
    if not request_input_entry(owner, "interface_input", "增量截图扫描"):
        return
    dependencies = current_scanning_dependencies(owner)
    owner._scan_dependencies = dependencies
    begin_scan_operation(owner, dependencies, route=str(mode))
    owner._pending_scan_mode = mode
    owner.showMinimized()
    worker = ScanWorkerThread(output_dir=dependencies.screenshot_dir,
        template_path=dependencies.template_dir / "new_tag.png", mode=mode,
        operation_guard=dependencies.operation_guard, parent=owner)
    owner._scan_worker = worker
    # 回调绑定真实发出结果的 worker/代次，不从后来换掉的当前指针取资源。
    worker.scan_done.connect(lambda count: owner._on_scan_done(count, worker=worker, dependencies=dependencies))
    worker.canceled.connect(lambda: cancel_incremental_capture(owner, worker, dependencies, text_catalog=text_catalog))
    worker.error.connect(lambda error: fail_incremental_capture(owner, error, text_catalog=text_catalog)
        if owner._scan_worker is worker and scanning_dependencies_are_current(owner, dependencies) else None)
    owner._start_scan_hotkeys(mode)
    owner.btn_run.setText(resolve_text_catalog(text_catalog).render('ui.scanning.start_incremental_capture.btn_run', stop=f'{owner._hotkey_manager.configuration.stop}'))
    worker.start()


def cancel_incremental_capture(owner, worker, dependencies, *, text_catalog=None):
    if owner._scan_worker is not worker or not scanning_dependencies_are_current(owner, dependencies):
        return
    owner._stop_scan_hotkeys()
    owner.showNormal()
    owner.activateWindow()
    owner.btn_run.setEnabled(True)
    owner.btn_run.setText(resolve_text_catalog(text_catalog).render('ui.scanning.on_vision_canceled.caption'))
    owner._pending_parse_only = False
    owner._replace_inventory_on_next_parse = False
    scan_event(owner, "WARNING", "scanning.cancelled", "增量采集已取消", snapshot_written=False)


def finish_incremental_capture(owner, count, worker, dependencies, *, text_catalog=None):
    if owner._scan_worker is not worker or not scanning_dependencies_are_current(owner, dependencies):
        return
    if worker.is_cancelled():
        cancel_incremental_capture(owner, worker, dependencies, text_catalog=text_catalog)
        return
    capture_template = worker.capture_template
    if worker.mode == 'auto' and not isinstance(capture_template, CaptureTemplateFile):
        fail_incremental_capture(owner, "自动采集结果缺少本批扫描标记，请重新采集。", text_catalog=text_catalog)
        return
    try:
        if capture_template is not None:
            capture_template.verify()
    except Exception as error:
        fail_incremental_capture(owner, str(error), text_catalog=text_catalog)
        return
    owner._stop_scan_hotkeys()
    owner.showNormal()
    owner.activateWindow()
    owner._replace_inventory_on_next_parse = False
    if count > 0:
        scope = 'incremental_auto' if worker.mode == 'auto' else 'incremental_semi'
        owner._start_vision_processing(replace_output=False, parse_scope=scope,
            capture_template=capture_template, capture_dependencies=dependencies,
            capture_cancel_token=worker.cancel_token)
    else:
        owner.btn_run.setEnabled(True)
        owner.btn_run.setText(resolve_text_catalog(text_catalog).render('ui.scanning.on_vision_canceled.caption'))
        owner._pending_parse_only = False
        scan_event(owner, "INFO", "scanning.succeeded", "扫描完成但没有捕获新装备", captured_count=0, snapshot_written=False)
        QMessageBox.information(owner.dialog_parent, resolve_text_catalog(text_catalog).render('ui.scanning.finish_incremental_capture.qmessagebox'), plain_tooltip(resolve_text_catalog(text_catalog).render('ui.scanning.finish_incremental_capture.qmessagebox_2')))


def fail_incremental_capture(owner, error, *, text_catalog=None):
    scan_event(owner, "ERROR", "scanning.failed", "扫描捕获失败", stage="capture", error=error)
    owner._stop_scan_hotkeys()
    owner.showNormal()
    owner.activateWindow()
    owner.btn_run.setEnabled(True)
    owner.btn_run.setText(resolve_text_catalog(text_catalog).render('ui.scanning.on_vision_canceled.caption'))
    owner._pending_parse_only = False
    show_input_unavailable(owner, "截图扫描", str(error))
