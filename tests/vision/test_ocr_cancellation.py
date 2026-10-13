# 验证鉴定与扫描真实入口传递取消 Event，不交付迟到的原生解析结果。
from __future__ import annotations

import tempfile
import threading
import unittest
from concurrent.futures import CancelledError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
from PySide6.QtWidgets import QApplication

from src.app.workers import FullVisualScanParseWorkerThread
from src.features.scanning.hotkey_actions import on_hotkey_stop
from src.integrations.vision.recognition import backend_runtime
from tests.vision.test_ocr_runtime import model_fixture
from src.features.scanning.vision_worker import VisionWorkerThread
from src.integrations.vision.recognition import identification_parser, screenshot_parser
from src.integrations.vision.recognition import batch_processor
from src.services.identification.tasks import IdentificationImageRequest, IdentificationImageJob, parse_identification_images

NTE_TEST_TIER = 'core'


class OcrCancellationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_identification_passes_plain_cancel_callback_to_processor(self):
        cancel = threading.Event()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request = IdentificationImageRequest(root, root / 'output', root, (IdentificationImageJob(root / 'image.png'),))
            def construct(**kwargs):
                self.assertIs(kwargs['cancel_check'].__self__, cancel)
                cancel.set()
                return Mock()
            with patch('src.services.identification.tasks.load_recognition_resources', return_value=Mock()), patch.object(batch_processor, 'BatchProcessor', side_effect=construct):
                with self.assertRaises(CancelledError):
                    parse_identification_images(request, cancel_check=cancel.is_set)

    def test_offline_cancel_before_run_skips_processor_and_emits_cancel(self):
        worker = VisionWorkerThread('synthetic')
        done, errors, canceled = [], [], []
        worker.processing_done.connect(done.append)
        worker.error.connect(errors.append)
        worker.canceled.connect(canceled.append)
        worker.request_cancel()
        with patch('src.features.scanning.vision_worker.BatchProcessor') as processor:
            worker.run()
        processor.assert_not_called()
        self.assertEqual([0], canceled)
        self.assertEqual([], done)
        self.assertEqual([], errors)

    def test_cancel_keeps_physical_worker_until_native_constructor_returns(self):
        entered, release = threading.Event(), threading.Event()
        worker = VisionWorkerThread('synthetic')
        callbacks = []
        def construct(**kwargs):
            callbacks.append(kwargs['cancel_check'])
            entered.set()
            release.wait(5)
            if kwargs['cancel_check']():
                raise CancelledError()
            raise AssertionError('expected cancellation')
        with patch('src.features.scanning.vision_worker.load_recognition_resources', return_value=Mock()), patch('src.features.scanning.vision_worker.BatchProcessor', side_effect=construct):
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                worker.request_cancel()
                self.assertTrue(worker.isRunning())
                self.assertTrue(callbacks[0]())
                self.assertIsInstance(callbacks[0].__self__, type(threading.Event()))
            finally:
                release.set()
                self.assertTrue(worker.wait(5000))

    def test_full_scan_cancel_before_scanner_creation_has_no_input(self):
        worker = FullVisualScanParseWorkerThread(1, screenshot_dir='synthetic', config_dir='synthetic', user_database_path='synthetic')
        worker.request_cancel()
        with patch('src.app.workers.BatchProcessor') as processor, patch('src.integrations.vision.mouse_inventory_scan.MouseInventoryScanner') as scanner:
            worker.run()
        scanner.assert_not_called()
        processor.assert_not_called()

    def test_full_scan_cancel_stops_owned_scanner_and_sets_event(self):
        worker = FullVisualScanParseWorkerThread(1, screenshot_dir='synthetic', config_dir='synthetic', user_database_path='synthetic')
        worker.scanner = Mock()
        worker.request_cancel()
        worker.scanner.emergency_stop.assert_called_once_with()
        self.assertTrue(worker._cancel_event.is_set())

    def test_identification_cancel_does_not_enter_full_image_retry(self):
        processor = SimpleNamespace(ocr_engine=Mock(), _process_single_image=Mock(side_effect=CancelledError()))
        with patch.object(identification_parser, 'imread_unicode', return_value=np.zeros((20, 20, 3), dtype=np.uint8)), patch.object(identification_parser, 'crop_window_border_from_image', side_effect=lambda image: image):
            with self.assertRaises(CancelledError):
                identification_parser.parse_identify_items(processor, 'synthetic.png')
        processor.ocr_engine.extract_lines.assert_not_called()

    def test_reward_retry_preserves_cancel_exception(self):
        with patch.object(screenshot_parser, 'process_reward_scene', side_effect=CancelledError()):
            with self.assertRaises(CancelledError):
                screenshot_parser._prefer_complete_or_reward_item(SimpleNamespace(), None, SimpleNamespace(sub_stats={}), 'synthetic.png')


    def test_actual_hotkey_cancels_native_init_not_an_ended_old_worker(self):
        entered, release = threading.Event(), threading.Event()
        worker = FullVisualScanParseWorkerThread(1, screenshot_dir='synthetic', config_dir='synthetic', user_database_path='synthetic')
        old_scanner = Mock()
        owner = SimpleNamespace(_scan_worker=SimpleNamespace(isRunning=lambda: False, scanner=old_scanner), _gamepad_worker=worker,
            _hotkey_manager=SimpleNamespace(configuration=SimpleNamespace(stop='F8')))
        cpu = Mock()
        def native(**kwargs):
            entered.set()
            release.wait(5)
            raise RuntimeError('native fixture failure')
        scanner = SimpleNamespace(emergency_stop=Mock(), close=Mock())
        def construct_processor(**kwargs):
            backend_runtime.create_ocr_engine(cancel_check=kwargs['cancel_check'])
            raise AssertionError('cancel must block processor return')
        done, errors = [], []
        worker.processing_done.connect(done.append)
        worker.error.connect(errors.append)
        with model_fixture(), patch.dict('sys.modules', {'rapidocr_openvino': SimpleNamespace(RapidOCR=native), 'rapidocr_onnxruntime': SimpleNamespace(RapidOCR=cpu)}), patch('src.integrations.vision.mouse_scan_runtime.require_mouse_scan_runtime'), patch('src.integrations.vision.mouse_inventory_scan.MouseInventoryScanner', return_value=scanner), patch('src.app.workers.load_recognition_resources', return_value=Mock()), patch('src.app.workers.BatchProcessor', side_effect=construct_processor), patch('src.services.scanning.pipeline.run_streaming_scan_parse') as stream:
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                on_hotkey_stop(owner)
                self.assertTrue(worker._cancel_event.is_set())
                self.assertTrue(worker.isRunning())
            finally:
                release.set()
                self.assertTrue(worker.wait(5000))
                self.app.processEvents()
        old_scanner.emergency_stop.assert_not_called()
        scanner.emergency_stop.assert_called_once_with()
        scanner.close.assert_called_once_with()
        cpu.assert_not_called()
        stream.assert_not_called()
        self.assertEqual([], done)
        self.assertEqual(['扫描已取消'], errors)

    def test_hotkey_keeps_legacy_scanner_stop_without_new_owner_api(self):
        scanner = SimpleNamespace(emergency_stop=Mock())
        owner = SimpleNamespace(_scan_worker=SimpleNamespace(isRunning=lambda: True, scanner=scanner),
            _hotkey_manager=SimpleNamespace(configuration=SimpleNamespace(stop='F8')))
        on_hotkey_stop(owner)
        scanner.emergency_stop.assert_called_once_with()
        self.assertTrue(scanner._finish_flag)
