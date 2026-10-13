# 验证所选 OCR 打包闭包、分发冲突及随包声明，不执行打包器。
from __future__ import annotations

import importlib.metadata
import tomllib
from pathlib import Path

import pytest

from src.integrations.vision.recognition.backend_support import OcrBackendSupport, load_packaged_backend_support
from tools.release import ocr_build_inputs as build

NTE_TEST_TIER = 'core'


def metadata_fixture(monkeypatch, installed):
    inspected = []
    def distribution(name):
        inspected.append(name)
        if name not in installed:
            raise importlib.metadata.PackageNotFoundError(name)
        return object()
    monkeypatch.setattr(build.importlib.metadata, 'distribution', distribution)
    return inspected


@pytest.mark.parametrize('profile', ['default', 'openvino', 'onnx_cpu', 'onnx_directml'])
def test_selected_collection_has_no_excluded_import_data_dll_or_metadata(tmp_path, monkeypatch, profile):
    support = OcrBackendSupport(profile)
    metadata_fixture(monkeypatch, [support.ort_distribution] if support.ort_distribution else [])
    calls = []
    def collect(kind, package, **kwargs):
        calls.append((kind, package, kwargs))
        return [package + '.public'] if kind == 'modules' else [(package + '/member', package)]
    result = build.collect_ocr_build_inputs(support,
        collect_submodules=lambda name, **kw: collect('modules', name, **kw),
        collect_data_files=lambda name, **kw: collect('data', name, **kw),
        collect_dynamic_libs=lambda name: collect('dll', name),
        copy_metadata=lambda name: collect('metadata', name))
    selected = {*support.libraries, *support.rapidocr_packages, support.ort_distribution}
    assert all(package in selected for _kind, package, _kw in calls)
    assert all(name not in result.hidden_imports for name in result.excludes)
    assert all(kw.get('on_error') == 'raise' for kind, _name, kw in calls if kind == 'modules')
    assert tuple(name for kind, name, _kw in calls if kind == 'dll') == support.libraries
    assert all(kw.get('excludes') == ['models/*', 'ch_ppocr_rec/ppocr_keys_v1.txt']
               for kind, name, kw in calls if kind == 'data' and name.startswith('rapidocr_'))
    manifest = build.write_ocr_support_manifest(tmp_path / 'assets/ocr/backend_support.json', support)
    assert manifest.is_file() and load_packaged_backend_support(tmp_path) == support


@pytest.mark.parametrize('installed', [[], ['onnxruntime'], ['onnxruntime', 'onnxruntime-directml']])
def test_directml_missing_or_shared_namespace_conflict_fails_before_hooks(monkeypatch, installed):
    metadata_fixture(monkeypatch, installed)
    with pytest.raises(RuntimeError, match='分发'):
        build.collect_ocr_build_inputs(OcrBackendSupport('onnx_directml'),
            collect_submodules=lambda *args: pytest.fail('collection started before validation'),
            collect_data_files=lambda *args, **kw: [], collect_dynamic_libs=lambda *args: [], copy_metadata=lambda *args: [])


def test_openvino_support_does_not_query_unselected_ort_metadata(monkeypatch):
    inspected = metadata_fixture(monkeypatch, ['onnxruntime', 'onnxruntime-directml'])
    build.validate_ocr_distributions(OcrBackendSupport('openvino'))
    assert inspected == []


@pytest.mark.parametrize('missing', ['modules', 'data', 'dll', 'metadata'])
def test_selected_collection_missing_role_is_a_build_error(monkeypatch, missing):
    metadata_fixture(monkeypatch, [])
    def collect(kind, package, **kw):
        return [] if kind == missing else ([package] if kind == 'modules' else [(str(Path(package) / 'member'), package)])
    with pytest.raises(RuntimeError, match='收集失败'):
        build.collect_ocr_build_inputs(OcrBackendSupport('openvino'),
            collect_submodules=lambda name, **kw: collect('modules', name, **kw),
            collect_data_files=lambda name, **kw: collect('data', name, **kw),
            collect_dynamic_libs=lambda name: collect('dll', name), copy_metadata=lambda name: collect('metadata', name))



def test_project_ocr_dependency_groups_preserve_single_namespace_and_default():
    """公开 profile 的安装输入与运行库声明一致，替代仅限定在核对过的版本。"""
    config = tomllib.loads((Path(__file__).parents[2] / 'pyproject.toml').read_text(encoding='utf-8'))
    groups = config['dependency-groups']
    uv = config['tool']['uv']
    assert uv['default-groups'] == ['dev', 'ocr-openvino', 'ocr-onnx-directml']
    assert uv['conflicts'] == [[{'group': 'ocr-onnx-cpu'}, {'group': 'ocr-onnx-directml'}]]
    assert groups['ocr-openvino'] == ["loguru>=0.7.0 ; sys_platform == 'win32'",
                                          "rapidocr-openvino==1.4.4 ; sys_platform == 'win32'"]
    for profile, group in [('onnx_cpu', 'ocr-onnx-cpu'), ('onnx_directml', 'ocr-onnx-directml')]:
        support = OcrBackendSupport(profile)
        assert groups[group][0] == "loguru>=0.7.0 ; sys_platform == 'win32'"
        assert groups[group][1] == "rapidocr-onnxruntime==1.4.4 ; sys_platform == 'win32'"
        assert groups[group][2].split('>=')[0] == support.ort_distribution
    metadata, = uv['dependency-metadata']
    assert metadata == {
        'name': 'rapidocr-onnxruntime', 'version': '1.4.4', 'requires-python': '>=3.6,<3.13',
        'requires-dist': ['pyclipper>=1.2.0', 'opencv-python>=4.5.1.48', 'numpy<3.0.0,>=1.19.5',
                          'six>=1.15.0', 'Shapely!=2.0.4,>=1.7.1', 'PyYAML', 'Pillow', 'tqdm'],
    }
