# 覆盖实际增量采集身份、后台首图比对、取消交接与原图保留。
import threading
import time
from concurrent.futures import CancelledError
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QWidget

from src.app.workers import ScanWorkerThread
from src.features.scanning import incremental_workflow, file_workflow
from src.features.scanning.file_lifecycle import ScanFileLifecycle
from src.features.scanning.vision_worker import VisionWorkerThread
from src.integrations.vision import incremental_scan as scan
from src.integrations.vision.recognition import batch_processor
from src.integrations.vision.recognition.image_resources import CaptureTemplateFile
from src.integrations.vision.recognition.resources import RecognitionResourceError
from src.integrations.vision.recognition.ocr.model_resources import OcrModelResourceError
from src.integrations.vision.window_capture import WindowRect
from src.models.equipment import Tape
from src.services.inventory.visual_scan_commit import commit_completed_vision_inventory
from src.services.inventory.vision_commit import VisionCommitRequest
from tests.vision.test_ocr_runtime import NativeOCR, model_fixture
from tests.vision.test_recognition_resources import selected as selected, freeze_image

NTE_TEST_TIER = 'core'


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def capture(tmp_path, monkeypatch):
    root = tmp_path / '中文标记'
    root.mkdir()
    tag = root / 'new_tag.png'
    pixels = np.random.default_rng(10).integers(0, 256, (8, 12), dtype=np.uint8)
    ok, encoded = cv2.imencode('.png', pixels)
    assert ok
    tag.write_bytes(encoded.tobytes())
    monkeypatch.setattr(scan.ctypes, 'windll', SimpleNamespace(user32=SimpleNamespace(
        GetSystemMetrics=lambda index: 2560 if index == 0 else 1440)), raising=False)
    monkeypatch.setattr(scan, 'get_foreground_client_rect', lambda: WindowRect(0, 0, 2560, 1440))
    return tag, pixels


def test_actual_new_tag_decode_is_unicode_immutable_and_preserves_match_scale(capture, tmp_path):
    tag, pixels = capture
    scanner = scan.DroneScanner(output_dir=tmp_path/'shots', template_path=tag)
    assert np.array_equal(scanner.template, pixels)
    screen = np.zeros((80, 100), dtype=np.uint8)
    screen[20:28, 30:42] = pixels
    assert scanner._find_new_tags(screen) == [(30, 20)]
    assert scanner.confidence_threshold == .85
    for rect in (WindowRect(0, 0, 2560, 1440), WindowRect(0, 0, 1280, 800)):
        scanner._apply_window_rect(rect)
        with pytest.raises(ValueError):
            scanner.template.setflags(write=True)
    assert scanner.template.shape == (4, 6)
    assert scanner._base_to_window(30, 20) == (15, 10)
    tag.write_bytes(tag.read_bytes() + b'drift')
    with pytest.raises(RecognitionResourceError):
        scanner._find_new_tags(screen)
    with patch.object(scan, '_send_input') as send, patch.object(scanner, '_move_to') as move:
        with pytest.raises(RecognitionResourceError):
            scanner._click_at(30, 20)
    send.assert_not_called()
    move.assert_not_called()


def test_template_change_during_matching_never_delivers_coordinates(capture, tmp_path, monkeypatch):
    tag, pixels = capture
    scanner = scan.DroneScanner(output_dir=tmp_path/'shots', template_path=tag)
    real = cv2.matchTemplate
    def changed(*args):
        result = real(*args)
        tag.write_bytes(tag.read_bytes() + b'drift')
        return result
    monkeypatch.setattr(scan.cv2, 'matchTemplate', changed)
    with pytest.raises(RecognitionResourceError):
        scanner._find_new_tags(pixels)


@pytest.mark.parametrize('point', ['before', 'constructor', 'capture'])
def test_actual_scan_worker_stop_never_reports_success(app, capture, tmp_path, monkeypatch, point):
    tag, _pixels = capture
    worker = ScanWorkerThread(output_dir=tmp_path/'shots', template_path=tag, mode='auto')
    done, canceled, errors = [], [], []
    worker.scan_done.connect(done.append)
    worker.canceled.connect(lambda: canceled.append(True))
    worker.error.connect(errors.append)
    real = scan.DroneScanner.__init__
    def construct(scanner, **kwargs):
        real(scanner, **kwargs)
        if point == 'constructor':
            worker.request_cancel()
    def capture_scan(scanner):
        if point == 'capture':
            scanner._stopped = True
        return 2
    monkeypatch.setattr(scan.DroneScanner, '__init__', construct)
    monkeypatch.setattr(scan.DroneScanner, 'start_scan', capture_scan)
    if point == 'before':
        worker.request_cancel()
    worker.run()
    assert canceled == [True] and done == [] and errors == []
    assert worker.cancel_token.is_set()


def test_semi_finish_is_success_without_new_tag(app, capture, tmp_path, monkeypatch):
    worker = ScanWorkerThread(output_dir=tmp_path/'shots', template_path=tmp_path/'missing.png', mode='semi')
    def finish(scanner):
        scanner._finish_flag = True
        return 1
    monkeypatch.setattr(scan.DroneScanner, 'start_semi_auto_scan', finish)
    done, errors = [], []
    worker.scan_done.connect(done.append)
    worker.error.connect(errors.append)
    worker.run()
    assert done == [1] and errors == [] and not worker.is_cancelled()
    assert worker.capture_template is None and worker.scanner.template is None


@pytest.mark.parametrize('boundary', ['old_worker', 'generation', 'cancel', 'missing_auto', 'changed_tag', 'valid'])
def test_handoff_binds_emitter_and_original_dependencies(capture, monkeypatch, boundary):
    tag, _pixels = capture
    evidence, _payload = CaptureTemplateFile.read(tag)
    dependencies = object()
    worker = SimpleNamespace(mode='auto', capture_template=evidence, cancel_token=threading.Event(),
                             is_cancelled=lambda: boundary == 'cancel')
    owner = SimpleNamespace(_scan_worker=object() if boundary == 'old_worker' else worker,
        _stop_scan_hotkeys=Mock(), showNormal=Mock(), activateWindow=Mock(), btn_run=Mock(),
        _start_vision_processing=Mock(), dialog_parent=None)
    monkeypatch.setattr(incremental_workflow, 'scanning_dependencies_are_current', lambda *args: boundary != 'generation')
    monkeypatch.setattr(incremental_workflow, 'scan_event', Mock())
    fault = Mock()
    monkeypatch.setattr(incremental_workflow, 'fail_incremental_capture', fault)
    if boundary == 'missing_auto': worker.capture_template = None
    if boundary == 'changed_tag': tag.write_bytes(tag.read_bytes() + b'drift')
    incremental_workflow.finish_incremental_capture(owner, 2, worker, dependencies)
    if boundary == 'valid':
        kwargs = owner._start_vision_processing.call_args.kwargs
        assert kwargs['capture_template'] is evidence and kwargs['capture_dependencies'] is dependencies
        assert kwargs['capture_cancel_token'] is worker.cancel_token
        assert kwargs['parse_scope'] == 'incremental_auto' and not kwargs['replace_output']
    else:
        owner._start_vision_processing.assert_not_called()
    if boundary in {'old_worker', 'generation'}: owner._stop_scan_hotkeys.assert_not_called()
    assert fault.call_count == int(boundary in {'missing_auto', 'changed_tag'})


@pytest.mark.parametrize('reason', ['cancel', 'generation', 'marker', 'valid'])
def test_final_file_gate_keeps_input_and_never_writes_formal_inventory(selected, capture, tmp_path, reason):
    tag, _pixels = capture
    evidence, _payload = CaptureTemplateFile.read(tag)
    resources = freeze_image(selected)
    resources = replace(resources, image_profile=replace(resources.image_profile, capture_template=evidence))
    shots = tmp_path / 'shots'
    shots.mkdir()
    duplicate = shots/'raw_drive_probe_0000.png'
    duplicate.write_bytes(b'original')
    token = threading.Event()
    stats = {'parse_scope': 'incremental_auto', 'recognition_resources': resources,
             'capture_cancel_token': token, 'delete_after_parse': [str(duplicate)], 'probe_duplicate_count': 1}
    request = VisionCommitRequest.freeze(tmp_path/'user.sqlite3', stats, [])
    assert request.stats['capture_cancel_token'] is token
    assert request.stats['recognition_resources'] is resources
    assert commit_completed_vision_inventory(tmp_path/'user.sqlite3', stats, []) is None
    assert not (tmp_path/'user.sqlite3').exists()
    if reason == 'cancel': token.set()
    if reason == 'marker': tag.write_bytes(tag.read_bytes() + b'drift')
    owner = SimpleNamespace(_scan_lifecycle=lambda: ScanFileLifecycle(shots))
    if reason == 'valid':
        result = file_workflow.postprocess_vision_files(owner, request.stats, before_files=lambda: True)
        assert result['deleted_duplicates'] == 1 and result['probe_duplicates'] == 1 and not duplicate.exists()
    else:
        with pytest.raises((CancelledError, RecognitionResourceError)):
            file_workflow.postprocess_vision_files(owner, request.stats, before_files=lambda: reason != 'generation')
        assert duplicate.read_bytes() == b'original'


def test_each_actual_file_mutation_rechecks_cancel(tmp_path):
    shots = tmp_path/'shots'
    shots.mkdir()
    paths = [shots/'raw_drive_probe_0000.png', shots/'raw_drive_probe_0001.png']
    for path in paths: path.write_bytes(b'original')
    calls = []
    def before_change():
        calls.append(True)
        if len(calls) == 2: raise CancelledError('test second dispatch revoked')
    with pytest.raises(CancelledError):
        ScanFileLifecycle(shots).postprocess_vision_files({'parse_scope': 'incremental_auto',
            'duplicate_paths': [str(path) for path in paths]}, before_change=before_change)
    assert not paths[0].exists() and paths[1].read_bytes() == b'original'


def test_probe_compare_resource_fault_propagates_but_ordinary_parse_failure_keeps_probe(tmp_path):
    left, right = tmp_path/'left.png', tmp_path/'right.png'
    lifecycle = ScanFileLifecycle(tmp_path)
    for error in (CancelledError('test'), RecognitionResourceError('test'), OcrModelResourceError('test')):
        with pytest.raises(type(error)):
            lifecycle.same_equipment_by_ocr(left, right, SimpleNamespace(_process_single_image=Mock(side_effect=error)))
    assert not lifecycle.same_equipment_by_ocr(left, right,
        SimpleNamespace(_process_single_image=Mock(side_effect=ValueError('ordinary parse failure'))))


@pytest.mark.parametrize('change', ['none', 'tag', 'cancel', 'decoder'])
def test_actual_parser_reuses_one_ocr_for_probe_and_returns_owned_preparation(app, selected, capture, tmp_path, monkeypatch, change):
    tag, pixels = capture
    evidence, _payload = CaptureTemplateFile.read(tag)
    shots = tmp_path/'shots'
    shots.mkdir()
    for name in ('raw_drive_0001.png', 'raw_drive_probe_0000.png'):
        ok, encoded = cv2.imencode('.png', pixels)
        assert ok
        (shots/name).write_bytes(encoded.tobytes())
    token = threading.Event()
    worker = VisionWorkerThread(str(shots), parse_scope='incremental_auto', config_dir=selected[0],
        static_database_path=selected[1], capture_template=evidence, capture_cancel_token=token)
    done, errors, canceled, instances, owners = [], [], [], [], []
    worker.processing_done.connect(done.append)
    worker.error.connect(errors.append)
    worker.canceled.connect(canceled.append)
    item = Tape(uid='synthetic-probe', set_name='夹具套装', main_stats='攻击力%', sub_stats={}, quality='Gold', area=15)
    def parse(processor, path):
        owners.append(id(processor))
        if change == 'tag': tag.write_bytes(tag.read_bytes() + b'drift')
        if change == 'cancel': token.set()
        if change == 'decoder':
            processor.ocr_engine.ocr.text_rec.postprocess_op.character.reverse()
            processor.ocr_engine.extract_text(np.zeros((10, 12), dtype=np.uint8))
        return item
    monkeypatch.setattr(batch_processor.BatchProcessor, '_process_single_image', parse)
    process = Mock(side_effect=AssertionError('duplicate probe must be skipped'))
    monkeypatch.setattr(batch_processor.BatchProcessor, 'process_image_file', process)
    def native(**kwargs):
        instances.append(kwargs)
        return NativeOCR()
    with model_fixture(), patch.dict('sys.modules', {'rapidocr_openvino': SimpleNamespace(RapidOCR=native)}):
        worker.run()
    assert len(instances) == 1 and len(owners) == (1 if change == 'decoder' else 2) and len(set(owners)) == 1
    process.assert_not_called()
    assert (shots/'raw_drive_probe_0000.png').exists()
    if change == 'none':
        assert len(done) == 1 and not errors and not canceled
        stats = done[0]
        assert stats['delete_after_parse'] == [str(shots/'raw_drive_probe_0000.png')]
        assert stats['probe_duplicate_count'] == 1 and stats['total_count'] == 0
        assert stats['capture_cancel_token'] is token
        assert stats['recognition_resources'].image_profile.capture_template is evidence
    elif change in {'tag', 'decoder'}:
        assert not done and len(errors) == 1 and not canceled
    else:
        assert not done and not errors and canceled == [0]


def test_real_parse_thread_does_not_block_gui_during_incremental_preparation(app, selected, tmp_path, monkeypatch):
    shots = tmp_path/'shots'
    shots.mkdir()
    (shots/'raw_drive_semi_0001.png').write_bytes(b'synthetic')
    root = QWidget()
    worker = VisionWorkerThread(str(shots), parent=root, parse_scope='incremental_semi',
        config_dir=selected[0], static_database_path=selected[1])
    entered, release = threading.Event(), threading.Event()
    ticks, prepare_threads = [], []
    original = ScanFileLifecycle.prepare_incremental_parse
    def prepare(lifecycle, scope, processor):
        prepare_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return original(lifecycle, scope, processor)
    monkeypatch.setattr(ScanFileLifecycle, 'prepare_incremental_parse', prepare)
    monkeypatch.setattr(batch_processor.BatchProcessor, 'process_image_file', lambda *args, **kwargs: (None, False))
    timer = QTimer(root)
    timer.timeout.connect(lambda: ticks.append(True))
    timer.start(2)
    with model_fixture(), patch.dict('sys.modules', {'rapidocr_openvino': SimpleNamespace(RapidOCR=lambda **kwargs: NativeOCR())}):
        try:
            worker.start()
            deadline = time.monotonic()+5
            while not entered.is_set() or len(ticks) < 3:
                app.processEvents()
                assert time.monotonic() < deadline
                time.sleep(.002)
            assert worker.isRunning() and prepare_threads != [threading.get_ident()]
        finally:
            release.set()
            assert worker.wait(5000)
            timer.stop()
            root.deleteLater()
            app.processEvents()


@pytest.mark.parametrize('mode', ['auto', 'semi'])
def test_recapture_rejects_retained_batch_before_capture_or_input(capture, tmp_path, monkeypatch, mode):
    tag, _pixels = capture
    shots = tmp_path/'shots'
    shots.mkdir()
    retained = shots/'raw_drive_semi_0001.png'
    retained.write_bytes(b'previous canceled original')
    scanner = scan.DroneScanner(output_dir=shots, template_path=tag, use_new_tag=mode == 'auto')
    native = Mock(side_effect=AssertionError('no capture/input after conflict'))
    monkeypatch.setattr(scan.mss, 'MSS', native)
    monkeypatch.setattr(scan, '_send_input', native)
    with pytest.raises(RecognitionResourceError):
        (scanner.start_scan if mode == 'auto' else scanner.start_semi_auto_scan)()
    native.assert_not_called()
    assert retained.read_bytes() == b'previous canceled original'


def test_exclusive_capture_preserves_racing_target_and_writes_real_png(capture, tmp_path, monkeypatch):
    tag, _pixels = capture
    shots = tmp_path/'shots'
    scanner = scan.DroneScanner(output_dir=shots, template_path=tag, use_new_tag=False)
    target = shots/'raw_drive_semi_0001.png'
    frame = SimpleNamespace(rgb=bytes([1, 2, 3])*12, size=(4, 3))
    def captured(_sct):
        return frame, WindowRect(0, 0, 2560, 1440)
    monkeypatch.setattr(scan, 'capture_foreground_window', captured)
    scanner._capture_window_png(None, target)
    decoded = cv2.imdecode(np.frombuffer(target.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (3, 4, 3)
    original = target.read_bytes()
    with pytest.raises(RecognitionResourceError):
        scanner._capture_window_png(None, target)
    assert target.read_bytes() == original
    scanner._stopped = True
    with pytest.raises(CancelledError):
        scanner._capture_window_png(None, shots/'raw_drive_semi_0002.png')
    assert not (shots/'raw_drive_semi_0002.png').exists()


def test_actual_incremental_handoff_callback_runs_on_gui_thread(app, capture, tmp_path, monkeypatch):
    tag, _pixels = capture
    root = QWidget()
    root.app_context = None
    root.dialog_parent = root
    root.btn_run = Mock()
    root._hotkey_manager = SimpleNamespace(configuration=SimpleNamespace(stop='test'))
    root._start_scan_hotkeys = Mock()
    dependencies = SimpleNamespace(screenshot_dir=tmp_path/'shots', template_dir=tag.parent, operation_guard=None)
    monkeypatch.setattr(incremental_workflow, 'current_scanning_dependencies', lambda owner: dependencies)
    monkeypatch.setattr(incremental_workflow, 'request_input_entry', lambda *args: True)
    monkeypatch.setattr(incremental_workflow, 'begin_scan_operation', Mock())
    monkeypatch.setattr(scan.DroneScanner, 'start_scan', lambda scanner: 1)
    thread_ids = []
    root._on_scan_done = lambda count, **kwargs: thread_ids.append(threading.get_ident())
    try:
        incremental_workflow.start_incremental_capture(root, 'auto')
        deadline = time.monotonic()+5
        while not thread_ids:
            app.processEvents()
            assert time.monotonic() < deadline
            time.sleep(.002)
        assert thread_ids == [threading.get_ident()]
    finally:
        root._scan_worker.request_cancel()
        assert root._scan_worker.wait(5000)
        root.deleteLater()
        app.processEvents()

