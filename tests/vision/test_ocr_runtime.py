# 校验 OCR 实际选择、私有资源与原生调用前后的取消边界。
from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from concurrent.futures import CancelledError
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.integrations.vision.recognition.ocr import backend_runtime as runtime
from src.integrations.vision.recognition.ocr import model_resources as resources
from src.integrations.vision.recognition.ocr import language_resources as language
from src.integrations.vision.recognition.ocr.ocr_engine import OCREngine
from src.utils.exceptions import OCRParseError

NTE_TEST_TIER = 'core'


DICTIONARY_BYTES = b' A \r\nB\n'
DECODER_CHARACTERS = ('blank', ' A ', 'B', ' ')


def create_micro_resources(root):
    root.mkdir(parents=True, exist_ok=True)
    specs = []
    for index, keyword in enumerate(('det_model_path', 'rec_model_path', 'cls_model_path')):
        payload = f'model-{index}'.encode()
        filename = f'{index}.onnx'
        (root / filename).write_bytes(payload)
        specs.append(resources.OcrModelSpec(filename, len(payload), hashlib.sha256(payload).hexdigest().upper(), keyword))
    (root / 'ppocr_keys_v1.txt').write_bytes(DICTIONARY_BYTES)
    dictionary_spec = language.OcrDictionarySpec('ppocr_keys_v1.txt', len(DICTIONARY_BYTES),
        hashlib.sha256(DICTIONARY_BYTES).hexdigest().upper(), 2, specs[1].sha256)
    return tuple(specs), dictionary_spec


def freeze_micro_resources(root):
    specs, dictionary_spec = create_micro_resources(root)
    with patch.object(resources, 'OCR_MODEL_SPECS', specs):
        models = resources._directory_resources(root)
    dictionary = language.load_ocr_dictionary(root / dictionary_spec.filename, spec=dictionary_spec)
    result = language.OcrRuntimeResources(models, dictionary)
    result.verify_unchanged()
    return result


@contextmanager
def model_fixture():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        specs, dictionary_spec = create_micro_resources(root)
        with patch.object(resources, 'OCR_MODEL_SPECS', specs), patch.object(resources, 'bundled_ocr_model_dir', return_value=root), patch.object(language, 'OCR_DICTIONARY_SPEC', dictionary_spec), patch.object(language, 'shared_dictionary_path', return_value=root / dictionary_spec.filename):
            yield root, specs


class NativeOCR:
    def __init__(self):
        self.text_rec = SimpleNamespace(postprocess_op=SimpleNamespace(character=list(DECODER_CHARACTERS)))

    def __call__(self, image):
        return [([[1, 2], [11, 2], [11, 8], [1, 8]], ' 攻击力 ', .99)], 0.0


class OcrRuntimeTests(unittest.TestCase):
    def test_model_kwargs_are_private_and_next_init_rechecks_bytes(self):
        with model_fixture() as (root, specs):
            first = resources.rapidocr_model_kwargs()
            expected = first['det_model_path']
            first['det_model_path'] = 'caller-change'
            self.assertEqual(expected, resources.rapidocr_model_kwargs()['det_model_path'])
            (root / specs[0].filename).write_bytes(b'tamper!')
            with self.assertRaises(resources.OcrModelResourceError):
                resources.rapidocr_model_kwargs()

    def test_frozen_resources_keep_paths_even_if_global_specs_change(self):
        with model_fixture() as (root, specs):
            frozen = resources.load_ocr_model_resources()
            with patch.object(resources, 'OCR_MODEL_SPECS', ()):
                frozen.verify_unchanged()
                self.assertEqual(str(root / specs[0].filename), frozen.model_kwargs()['det_model_path'])
                (root / specs[0].filename).write_bytes(b'tamper!')
                with self.assertRaises(resources.OcrModelResourceError):
                    frozen.verify_unchanged()

    def test_unknown_or_integrated_gpu_skips_provider_import(self):
        for names in ([], ['Intel(R) UHD Graphics'], ['AMD Radeon Vega Graphics']):
            with self.subTest(names=names), model_fixture(), patch.object(runtime, '_get_video_adapter_names', return_value=names), patch.object(runtime, '_available_ort_providers') as providers, patch.dict('sys.modules', {'rapidocr_openvino': SimpleNamespace(RapidOCR=Mock(return_value=NativeOCR()))}):
                OCREngine('directml')
                providers.assert_not_called()

    def test_backend_failure_falls_back_with_identical_paths(self):
        calls = []
        def failing(**kwargs):
            calls.append(kwargs)
            raise RuntimeError('native fixture failure')
        def succeeding(**kwargs):
            calls.append(kwargs)
            return NativeOCR()
        with model_fixture(), patch.dict('sys.modules', {'rapidocr_openvino': SimpleNamespace(RapidOCR=failing), 'rapidocr_onnxruntime': SimpleNamespace(RapidOCR=succeeding)}):
            engine = OCREngine('openvino')
        self.assertEqual(2, len(calls))
        self.assertEqual(calls[0], calls[1])
        # 后端路线由 support 行为保护；灰度文本/框投影只需验证一次实际备用实例。
        image = np.zeros((12, 20), dtype=np.uint8)
        self.assertEqual(['攻击力'], engine.extract_text(image))
        self.assertEqual([{'text': '攻击力', 'box': (1, 2, 11, 8)}], engine.extract_lines(image))

    def test_model_change_during_failed_native_init_is_not_a_fallback(self):
        with model_fixture() as (root, specs):
            def change_then_fail(**kwargs):
                (root / specs[0].filename).write_bytes(b'tamper!')
                raise RuntimeError('native fixture failure')
            cpu = Mock(return_value=NativeOCR())
            with patch.dict('sys.modules', {'rapidocr_openvino': SimpleNamespace(RapidOCR=change_then_fail), 'rapidocr_onnxruntime': SimpleNamespace(RapidOCR=cpu)}):
                with self.assertRaises(resources.OcrModelResourceError):
                    OCREngine('openvino')
            cpu.assert_not_called()

    def test_model_change_during_warmup_rejects_engine(self):
        with model_fixture() as (root, specs):
            class ChangingNative(NativeOCR):
                def __call__(self, image):
                    (root / specs[0].filename).write_bytes(b'tamper!')
                    return [], 0
            with patch.dict('sys.modules', {'rapidocr_openvino': SimpleNamespace(RapidOCR=Mock(return_value=ChangingNative()))}):
                with self.assertRaises(resources.OcrModelResourceError):
                    OCREngine()

    def test_cancel_before_init_does_not_read_models_or_probe(self):
        with patch.object(runtime, 'load_ocr_runtime_resources') as load:
            with self.assertRaises(CancelledError):
                OCREngine(cancel_check=lambda: True)
            load.assert_not_called()

    def test_cancel_during_failed_init_does_not_start_fallback(self):
        state = {'cancel': False}
        def fail(**kwargs):
            state['cancel'] = True
            raise RuntimeError('native fixture failure')
        cpu = Mock(return_value=NativeOCR())
        with model_fixture(), patch.dict('sys.modules', {'rapidocr_openvino': SimpleNamespace(RapidOCR=fail), 'rapidocr_onnxruntime': SimpleNamespace(RapidOCR=cpu)}):
            with self.assertRaises(CancelledError):
                OCREngine(cancel_check=lambda: state['cancel'])
        cpu.assert_not_called()

    def test_native_inference_cancel_is_not_parse_error_or_output(self):
        state = {'cancel': False}
        engine = object.__new__(OCREngine)
        engine.cancel_check = lambda: state['cancel']
        def infer(image):
            state['cancel'] = True
            raise RuntimeError('native fixture failure')
        engine.ocr = infer
        infer.text_rec = NativeOCR().text_rec
        with model_fixture():
            engine.runtime_resources = language.load_ocr_runtime_resources()
        with self.assertRaises(CancelledError):
            engine.extract_text(np.zeros((12, 20), dtype=np.uint8))
        state['cancel'] = False
        with self.assertRaises(CancelledError):
            engine.extract_lines(np.zeros((12, 20), dtype=np.uint8))
        engine.cancel_check = None
        with self.assertRaises(OCRParseError):
            engine.extract_text(np.zeros((12, 20), dtype=np.uint8))

    def test_low_load_limits_are_set_before_backend_factory(self):
        order = []
        with model_fixture(), patch.object(runtime, '_apply_low_load_runtime_limits', side_effect=lambda: order.append('limits')), patch.dict('sys.modules', {'rapidocr_openvino': SimpleNamespace(RapidOCR=lambda **kw: order.append('native') or NativeOCR())}):
            OCREngine('low_load')
        self.assertEqual(['limits', 'native'], order)

    def test_dll_handles_survive_initialization_and_paths_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'openvino/libs').mkdir(parents=True)
            (root / 'onnxruntime/capi').mkdir(parents=True)
            handles = []
            def add(path):
                handle = Mock()
                handles.append(handle)
                return handle
            fake_sys = SimpleNamespace(platform='win32', frozen=True, _MEIPASS=str(root), executable=str(root / 'app.exe'))
            with patch.object(runtime, 'sys', fake_sys), patch.object(runtime, '_DLL_HANDLES', {}), patch.object(os, 'add_dll_directory', side_effect=add, create=True) as register, patch.dict(os.environ, {'PATH': 'fixture-path'}):
                runtime._register_runtime_dll_paths('openvino')
                runtime._register_runtime_dll_paths('openvino')
                self.assertEqual(1, register.call_count)
                self.assertNotIn(str(root / 'onnxruntime/capi'), os.environ['PATH'])
                self.assertEqual(1, os.environ['PATH'].split(os.pathsep).count(str(root / 'openvino/libs')))
                self.assertEqual(handles, list(runtime._DLL_HANDLES.values()))
                handles[0].close.assert_not_called()


    def test_preference_is_frozen_across_backend_failure(self):
        order = []
        def failing(**kwargs):
            order.append('openvino')
            os.environ['NTE_OCR_BACKEND'] = 'directml'
            raise RuntimeError('native fixture failure')
        def succeeding(**kwargs):
            order.append('cpu')
            return NativeOCR()
        with model_fixture(), patch.dict(os.environ, {'NTE_OCR_BACKEND': 'openvino'}), patch.dict('sys.modules', {'rapidocr_openvino': SimpleNamespace(RapidOCR=failing), 'rapidocr_onnxruntime': SimpleNamespace(RapidOCR=succeeding)}), patch.object(runtime, '_get_video_adapter_names') as adapters:
            OCREngine()
            adapters.assert_not_called()
        self.assertEqual(['openvino', 'cpu'], order)

    def test_import_does_not_register_dlls_or_change_root_logger(self):
        import subprocess
        import sys
        code = """
import logging, os, sys, tempfile
from pathlib import Path
import cv2, numpy
from src.utils.logger import logger
logging.getLogger().setLevel(logging.INFO)
with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    (root / 'openvino/libs').mkdir(parents=True)
    (root / 'onnxruntime/capi').mkdir(parents=True)
    sys.frozen = True
    sys._MEIPASS = str(root)
    before = os.environ.get('PATH', '')
    registrations = []
    os.add_dll_directory = lambda path: registrations.append(path)
    import src.integrations.vision.recognition.ocr.ocr_engine
    assert not registrations, registrations
    assert os.environ.get('PATH', '') == before
    assert logging.getLogger().level == logging.INFO
    assert all(name not in sys.modules for name in ('rapidocr_openvino', 'rapidocr_onnxruntime', 'onnxruntime'))
    print('import boundary verified')
"""
        command = [sys.executable, '-c', code]
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('import boundary verified', result.stdout)
