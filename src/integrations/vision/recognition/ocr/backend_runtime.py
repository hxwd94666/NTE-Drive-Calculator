# 按本次偏好延迟初始化 OCR 后端，持有 DLL 路径并保留取消与资源错误。
from __future__ import annotations

import os
import subprocess
import sys
import threading
from concurrent.futures import CancelledError
from typing import Callable

import cv2
import numpy as np

from src.integrations.vision.recognition.ocr.model_resources import OcrModelResourceError
from src.integrations.vision.recognition.ocr.backend_support import load_backend_support
from src.integrations.vision.recognition.ocr.language_resources import OcrRuntimeResources, load_ocr_runtime_resources
from src.utils.logger import logger

CancelCheck = Callable[[], bool] | None
_DLL_LOCK = threading.Lock()
# Windows DLL 目录句柄析构即撤销搜索路径；原生库可能延迟加载，故进程终生持有。
_DLL_HANDLES: dict[str, object] = {}


def check_cancel(cancel_check: CancelCheck) -> None:
    if cancel_check is not None and cancel_check():
        raise CancelledError('图片识别已取消')


def _register_runtime_dll_paths(backend: str) -> None:
    if sys.platform != 'win32' or not getattr(sys, 'frozen', False):
        return
    relative = 'openvino/libs' if backend == 'openvino' else 'onnxruntime/capi'
    bases = (getattr(sys, '_MEIPASS', ''), os.path.dirname(sys.executable))
    with _DLL_LOCK:
        for base in filter(None, bases):
            directory = os.path.abspath(os.path.join(base, relative))
            if not os.path.isdir(directory):
                continue
            key = os.path.normcase(directory)
            if key not in _DLL_HANDLES:
                try:
                    _DLL_HANDLES[key] = os.add_dll_directory(directory)
                except (OSError, AttributeError) as exc:
                    logger.debug(f'DLL 搜索路径注册失败 {directory}: {exc}')
            existing = os.environ.get('PATH', '').split(os.pathsep)
            if key not in {os.path.normcase(os.path.abspath(path)) for path in existing if path}:
                os.environ['PATH'] = os.pathsep.join([directory, *existing])
            if backend == 'openvino':
                os.environ['OPENVINO_LIB_PATHS'] = directory


def _get_video_adapter_names(cancel_check: CancelCheck = None) -> list[str]:
    if sys.platform != 'win32':
        return []
    commands = (
        ['wmic', 'path', 'win32_VideoController', 'get', 'Name'],
        ['powershell', '-NoProfile', '-Command',
         'Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name'],
    )
    for command in commands:
        check_cancel(cancel_check)
        try:
            result = subprocess.run(command, capture_output=True, text=True,
                encoding='utf-8', errors='ignore', timeout=3,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            check_cancel(cancel_check)
            if result.returncode != 0:
                continue
            names = [line.strip() for line in result.stdout.splitlines()
                     if line.strip() and line.strip().lower() != 'name']
            if names:
                return names
        except CancelledError:
            raise
        except Exception as exc:
            check_cancel(cancel_check)
            logger.debug(f'显卡信息读取失败: {exc}')
    return []


def _has_discrete_gpu(adapter_names: list[str]) -> bool:
    discrete = ('nvidia', 'geforce', 'rtx', 'gtx', 'quadro', 'radeon', 'amd', 'rx ', 'arc')
    integrated = ('intel(r) uhd', 'intel uhd', 'iris', 'xe graphics', 'vega')
    for name in adapter_names:
        lower = name.lower()
        if 'intel' in lower and 'arc' not in lower:
            continue
        if any(keyword in lower for keyword in integrated):
            continue
        if any(keyword in lower for keyword in discrete):
            return True
    return False


def _available_ort_providers() -> list[str]:
    try:
        _register_runtime_dll_paths('onnxruntime')
        import onnxruntime as ort
        return list(ort.get_available_providers())
    except (CancelledError, OcrModelResourceError):
        raise
    except Exception as exc:
        logger.debug(f'ONNX Runtime provider 查询失败: {exc}')
        return []


def _ocr_backend_preference(value: str | None = None) -> str:
    source = '参数' if value is not None else 'NTE_OCR_BACKEND'
    value = (value if value is not None else os.environ.get('NTE_OCR_BACKEND', 'openvino')).strip().lower()
    aliases = {'ov': 'openvino', 'intel': 'openvino', 'gpu': 'directml',
               'dml': 'directml', 'auto-safe': 'auto', 'amd': 'amd_compat',
               'amd-compat': 'amd_compat', 'amd_compatibility': 'amd_compat',
               'low-load': 'low_load'}
    value = aliases.get(value, value)
    if value not in {'openvino', 'directml', 'auto', 'cpu', 'amd_compat', 'low_load'}:
        logger.warning(f'未知 OCR 后端配置 {source}={value!r}，已使用 openvino。')
        return 'openvino'
    return value


def _apply_low_load_runtime_limits() -> None:
    # 这些限制属进程级尽力设置；已加载的原生运行库未必重新读取环境变量。
    for key in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS',
                'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS',
                'OV_CPU_THREADS_NUM', 'OPENVINO_CPU_THREADS_NUM'):
        os.environ[key] = '1'
    try:
        cv2.setNumThreads(1)
    except Exception as exc:
        logger.debug(f'OpenCV 线程限制失败: {exc}')


def _warmup(ocr, cancel_check: CancelCheck = None) -> None:
    check_cancel(cancel_check)
    try:
        ocr(np.zeros((32, 100, 3), dtype=np.uint8))
    finally:
        # 原生调用返回前不释放物理 owner；取消优先于其错误和备用后端。
        check_cancel(cancel_check)


def _verify_resources(resources: OcrRuntimeResources, cancel_check: CancelCheck, native=None) -> None:
    check_cancel(cancel_check)
    resources.verify_unchanged()
    check_cancel(cancel_check)
    if native is not None:
        resources.verify_decoder(native)


def _initialize_backend(backend: str, resources: OcrRuntimeResources,
                        cancel_check: CancelCheck = None, **options):
    check_cancel(cancel_check)
    _verify_resources(resources, cancel_check)
    _register_runtime_dll_paths(backend)
    try:
        if backend == 'openvino':
            from rapidocr_openvino import RapidOCR
            label = 'OpenVINO'
        else:
            from rapidocr_onnxruntime import RapidOCR
            label = 'DirectML GPU' if options else 'ONNX Runtime CPU (通用模式)'
        check_cancel(cancel_check)
        ocr = RapidOCR(use_cls=False, **resources.model_kwargs(), **options)
        _verify_resources(resources, cancel_check, ocr)
        try:
            _warmup(ocr, cancel_check)
        finally:
            _verify_resources(resources, cancel_check, ocr)
        return ocr, label
    except (CancelledError, OcrModelResourceError):
        raise
    except SystemExit as exc:
        _verify_resources(resources, cancel_check)
        if backend != 'openvino':
            raise
        logger.warning(f'OpenVINO 触发 sys.exit (DLL路径问题): {exc}')
    except Exception as exc:
        _verify_resources(resources, cancel_check)
        logger.warning(f'{backend} OCR 初始化失败: {exc}')
    return None, ''


def _create_openvino_ocr(resources: OcrRuntimeResources, cancel_check: CancelCheck = None):
    return _initialize_backend('openvino', resources, cancel_check)


def _create_onnx_cpu_ocr(resources: OcrRuntimeResources, cancel_check: CancelCheck = None):
    return _initialize_backend('onnxruntime', resources, cancel_check)


def _create_directml_ocr(providers: list[str], resources: OcrRuntimeResources,
                       cancel_check: CancelCheck = None):
    check_cancel(cancel_check)
    if 'DmlExecutionProvider' not in providers:
        logger.warning(f'当前 ONNX Runtime 不包含 DirectML Provider，可用 Provider: {providers}')
        return None, ''
    return _initialize_backend('onnxruntime', resources, cancel_check,
                               det_use_dml=True, cls_use_dml=True, rec_use_dml=True)


def create_ocr_engine(backend_preference: str | None = None, *,
                      cancel_check: CancelCheck = None):
    check_cancel(cancel_check)
    # 本次偏好与模型仅解析一次；后端失败不重新取环境、路径或另一组模型。
    support = load_backend_support()
    preference = _ocr_backend_preference(backend_preference)
    resources = load_ocr_runtime_resources(support=support)
    check_cancel(cancel_check)
    mode_label = ''
    if preference in {'amd_compat', 'low_load'}:
        mode_label = '异常兼容模式' if preference == 'amd_compat' else '正式低负载模式'
        _apply_low_load_runtime_limits()
        logger.warning(f'已启用 {mode_label}：禁用 DirectML，尽力限制进程级 OCR/图像线程。')
    candidates = []
    if support.allows_directml and preference in {'directml', 'auto'}:
        names = _get_video_adapter_names(cancel_check)
        check_cancel(cancel_check)
        if _has_discrete_gpu(names):
            providers = _available_ort_providers()
            check_cancel(cancel_check)
            candidates.append(lambda kwargs: _create_directml_ocr(providers, kwargs, cancel_check))
        else:
            logger.info('未确认可用独显，优先尝试 OpenVINO；显卡信息缺失不视为已确认无独显。')
    if 'openvino' in support.libraries and preference != 'cpu':
        candidates.append(lambda kwargs: _create_openvino_ocr(kwargs, cancel_check))
    if 'onnxruntime' in support.libraries:
        candidates.append(lambda kwargs: _create_onnx_cpu_ocr(kwargs, cancel_check))
    elif not candidates:
        # 现有固定 cpu/openvino/low_load 参数只表达偏好，不使另一单库包失效。
        candidates.append(lambda kwargs: _create_openvino_ocr(kwargs, cancel_check))
    for factory in candidates:
        check_cancel(cancel_check)
        ocr, label = factory(resources)
        check_cancel(cancel_check)
        if ocr is not None:
            return ocr, f'{label} / {mode_label}' if mode_label else label, resources
    raise ImportError(f'OCR {support.profile} 支持集合内没有可用引擎，请修复所选依赖后重试。')
