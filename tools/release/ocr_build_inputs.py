# 按冻结库支持集合收集 OCR 打包输入，选中缺项失败且不触碰排除库。
from __future__ import annotations

import importlib.metadata
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.integrations.vision.recognition.backend_support import OcrBackendSupport


@dataclass(frozen=True, slots=True)
class OcrBuildInputs:
    hidden_imports: tuple[str, ...]
    excludes: tuple[str, ...]
    data: tuple[tuple[str, str], ...]
    binaries: tuple[tuple[str, str], ...]


def validate_ocr_distributions(support: OcrBackendSupport) -> None:
    """ORT 两分发共享目录，顶层互斥不抵消 RapidOCR 的 CPU 传递依赖。"""
    if 'onnxruntime' not in support.libraries:
        return
    installed = []
    for name in ('onnxruntime', 'onnxruntime-directml'):
        try:
            importlib.metadata.distribution(name)
            installed.append(name)
        except importlib.metadata.PackageNotFoundError:
            pass
    if installed != [support.ort_distribution]:
        raise RuntimeError('OCR ONNX Runtime 分发缺失或 CPU/DirectML 同装，请使用已核对的独立环境。')


def collect_ocr_build_inputs(support: OcrBackendSupport, *, collect_submodules: Callable,
                             collect_data_files: Callable, collect_dynamic_libs: Callable,
                             copy_metadata: Callable) -> OcrBuildInputs:
    """只输出收集计划，不运行 PyInstaller、部署或删除源环境文件。"""
    validate_ocr_distributions(support)
    hidden = []; data = []; binaries = []
    all_support = OcrBackendSupport('default')
    excludes = tuple(name for library, package in zip(all_support.libraries, all_support.rapidocr_packages)
                     if library not in support.libraries for name in (library, package))
    try:
        for package in support.rapidocr_packages:
            modules = collect_submodules(package, on_error="raise")
            metadata = copy_metadata(package)
            configs = collect_data_files(package, excludes=['models/*', 'ch_ppocr_rec/ppocr_keys_v1.txt'])
            if not modules or not metadata or not configs:
                raise ValueError('所选 RapidOCR 包收集不完整')
            hidden.extend(modules); hidden.append(package); data.extend(metadata); data.extend(configs)
        for library in support.libraries:
            runtime_bins = collect_dynamic_libs(library)
            distribution = support.ort_distribution if library == 'onnxruntime' else library
            metadata = copy_metadata(distribution)
            if not runtime_bins or not metadata:
                raise ValueError('所选原生运行库收集不完整')
            hidden.append(library); binaries.extend(runtime_bins); data.extend(metadata)
            if library == 'openvino':
                hidden.append('openvino.runtime')
                cache = collect_data_files(library, includes=['libs/cache.json'])
                if not cache:
                    raise ValueError('所选 OpenVINO cache.json 缺失')
                data.extend(cache)
    except Exception as error:
        raise RuntimeError(f'OCR {support.profile} 打包输入收集失败，请核对所选依赖及许可证。') from error
    return OcrBuildInputs(tuple(dict.fromkeys(hidden)), excludes, tuple(data), tuple(binaries))


def write_ocr_support_manifest(path: Path, support: OcrBackendSupport) -> Path:
    """仅生成隔离构建输入；源码/用户配置不被本次构建改写。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'schema': 1, 'profile': support.profile}, indent=2), encoding='utf-8')
    return path
