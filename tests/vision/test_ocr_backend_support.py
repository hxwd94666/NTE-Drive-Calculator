# 验证库支持与设备偏好分离、排除库隔离及冻结发行声明。
from __future__ import annotations

import importlib.machinery
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.integrations import bundled_resources
from src.integrations.vision.recognition import backend_runtime as runtime
from src.integrations.vision.recognition import model_resources as models
from src.integrations.vision.recognition.backend_support import (
    OcrBackendSupport, OcrBackendSupportError, load_backend_support, load_packaged_backend_support,
)
from src.integrations.vision.recognition.ocr_engine import OCREngine
from tests.vision.test_ocr_runtime import NativeOCR, create_micro_resources, model_fixture

NTE_TEST_TIER = 'core'


@pytest.mark.parametrize(('profile', 'mode', 'chosen', 'gpu'), [
    ('default', 'openvino', 'openvino', False),
    ('openvino', 'cpu', 'openvino', False),
    ('openvino', 'directml', 'openvino', False),
    ('onnx_cpu', 'openvino', 'onnxruntime', False),
    ('onnx_cpu', 'directml', 'onnxruntime', False),
    ('onnx_cpu', 'low_load', 'onnxruntime', False),
    ('onnx_directml', 'directml', 'onnxruntime', True),
    ('onnx_directml', 'low_load', 'onnxruntime', False),
])
def test_library_boundary_constrains_real_engine_and_device_probe(monkeypatch, profile, mode, chosen, gpu):
    monkeypatch.setenv('NTE_OCR_PROFILE', profile)
    adapters = Mock(return_value=['NVIDIA GeForce fixture'])
    providers = Mock(return_value=['DmlExecutionProvider'])
    monkeypatch.setattr(runtime, '_get_video_adapter_names', adapters)
    monkeypatch.setattr(runtime, '_available_ort_providers', providers)
    monkeypatch.setattr(runtime, '_apply_low_load_runtime_limits', lambda: None)
    ov = Mock(return_value=NativeOCR()); ort = Mock(return_value=NativeOCR())
    monkeypatch.setitem(sys.modules, 'rapidocr_openvino', SimpleNamespace(RapidOCR=ov))
    monkeypatch.setitem(sys.modules, 'rapidocr_onnxruntime', SimpleNamespace(RapidOCR=ort))
    with model_fixture():
        engine = OCREngine(mode)
    selected, excluded = (ov, ort) if chosen == 'openvino' else (ort, ov)
    assert selected.call_count == 1 and excluded.call_count == 0
    assert adapters.call_count == providers.call_count == int(gpu)
    assert bool(selected.call_args.kwargs.get('rec_use_dml')) == gpu
    assert engine.ocr.text_rec.postprocess_op.character == list(engine.runtime_resources.dictionary.decoder_characters)


@pytest.mark.parametrize('profile', ['openvino', 'onnx_cpu'])
def test_single_library_failure_does_not_expand_mid_init(monkeypatch, profile):
    monkeypatch.setenv('NTE_OCR_PROFILE', profile)
    def fail(**kwargs):
        os.environ['NTE_OCR_PROFILE'] = 'default'
        raise RuntimeError('selected engine failed')
    excluded = Mock(return_value=NativeOCR())
    for name in ('openvino', 'onnxruntime'):
        factory = fail if name in OcrBackendSupport(profile).libraries else excluded
        monkeypatch.setitem(sys.modules, 'rapidocr_' + name, SimpleNamespace(RapidOCR=factory))
    with model_fixture(), pytest.raises(ImportError, match=profile):
        OCREngine('openvino')
    excluded.assert_not_called()


def test_directml_failure_falls_back_inside_same_ort_library(monkeypatch):
    monkeypatch.setenv('NTE_OCR_PROFILE', 'onnx_directml')
    monkeypatch.setattr(runtime, '_get_video_adapter_names', lambda *args: ['NVIDIA GeForce fixture'])
    monkeypatch.setattr(runtime, '_available_ort_providers', lambda: ['DmlExecutionProvider'])
    calls = []
    def factory(**kwargs):
        calls.append(kwargs)
        if kwargs.get('rec_use_dml'):
            raise RuntimeError('device unavailable')
        return NativeOCR()
    excluded = Mock(side_effect=AssertionError('excluded OpenVINO imported'))
    monkeypatch.setitem(sys.modules, 'rapidocr_openvino', SimpleNamespace(RapidOCR=excluded))
    monkeypatch.setitem(sys.modules, 'rapidocr_onnxruntime', SimpleNamespace(RapidOCR=factory))
    with model_fixture():
        OCREngine('directml')
    excluded.assert_not_called()
    assert len(calls) == 2
    assert {key: value for key, value in calls[0].items() if not key.endswith('_use_dml')} == calls[1]


def test_source_models_ignore_excluded_corrupt_copy_and_reject_selected_drift(tmp_path, monkeypatch):
    roots = {}
    for name in ('rapidocr_openvino', 'rapidocr_onnxruntime'):
        root = tmp_path / name / 'models'; specs, _dictionary = create_micro_resources(root)
        roots[name] = root
    monkeypatch.setattr(models, 'OCR_MODEL_SPECS', specs)
    inspected = []
    def spec(name):
        inspected.append(name)
        return importlib.machinery.ModuleSpec(name, None, origin=str(roots[name].parent / '__init__.py'))
    monkeypatch.setattr(models.importlib.util, 'find_spec', spec)
    (roots['rapidocr_openvino'] / specs[0].filename).write_bytes(b'corrupt excluded')
    support = OcrBackendSupport('onnx_cpu')
    selected = models.build_source_ocr_models(support=support)
    assert inspected == ['rapidocr_onnxruntime'] and all(path.parent == roots['rapidocr_onnxruntime'] for path in selected.values())
    with pytest.raises(models.OcrModelResourceError):
        models.build_source_ocr_models(support=OcrBackendSupport('default'))
    (roots['rapidocr_onnxruntime'] / specs[0].filename).unlink()
    with pytest.raises(models.OcrModelResourceError):
        models.build_source_ocr_models(support=support)


def test_frozen_support_ignores_environment_and_rejects_missing_or_invalid_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(bundled_resources, 'bundled_root', lambda: tmp_path)
    monkeypatch.setenv('NTE_OCR_PROFILE', 'default')
    path = tmp_path / 'assets/ocr/backend_support.json'; path.parent.mkdir(parents=True)
    with pytest.raises(OcrBackendSupportError):
        load_backend_support()
    for value in ({'schema': True, 'profile': 'openvino'}, {'schema': 1, 'profile': 'other'},
                  {'schema': 1, 'profile': 'openvino', 'libraries': ['onnxruntime']}, []):
        path.write_text(json.dumps(value), encoding='utf-8')
        with pytest.raises(OcrBackendSupportError):
            load_backend_support()
    path.write_text(json.dumps({'schema': 1, 'profile': 'openvino'}), encoding='utf-8')
    assert load_backend_support() == load_packaged_backend_support(tmp_path) == OcrBackendSupport('openvino')


def test_source_unknown_profile_fails_before_resources_or_backend(monkeypatch):
    monkeypatch.setenv('NTE_OCR_PROFILE', 'unknown')
    load = Mock(); monkeypatch.setattr(runtime, 'load_ocr_runtime_resources', load)
    with pytest.raises(OcrBackendSupportError):
        OCREngine()
    load.assert_not_called()
