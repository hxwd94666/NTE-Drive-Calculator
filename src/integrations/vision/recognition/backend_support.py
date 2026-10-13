# 声明 OCR 库支持集合，分离设备偏好并冻结单次任务的库边界。
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from src.integrations import bundled_resources

# profile 是发行/开发输入，不是用户界面的设备选择；ORT 两分发共享导入目录。
_PROFILES = MappingProxyType({
    'default': (('openvino', 'onnxruntime'), 'onnxruntime-directml'),
    'openvino': (('openvino',), None),
    'onnx_cpu': (('onnxruntime',), 'onnxruntime'),
    'onnx_directml': (('onnxruntime',), 'onnxruntime-directml'),
})
_RAPIDOCR = {'openvino': 'rapidocr_openvino', 'onnxruntime': 'rapidocr_onnxruntime'}


class OcrBackendSupportError(ImportError):
    """库支持声明缺失或无效；不伪装为空识别结果或扩展支持集合。"""


@dataclass(frozen=True, slots=True)
class OcrBackendSupport:
    profile: str

    def __post_init__(self) -> None:
        if not isinstance(self.profile, str) or self.profile not in _PROFILES:
            raise OcrBackendSupportError('OCR 库支持 profile 无效，请核对开发或发行输入。')

    @property
    def libraries(self) -> tuple[str, ...]:
        return _PROFILES[self.profile][0]

    @property
    def rapidocr_packages(self) -> tuple[str, ...]:
        return tuple(_RAPIDOCR[library] for library in self.libraries)

    @property
    def ort_distribution(self) -> str | None:
        return _PROFILES[self.profile][1]

    @property
    def allows_directml(self) -> bool:
        return self.ort_distribution == 'onnxruntime-directml'


def load_packaged_backend_support(internal_root: Path) -> OcrBackendSupport:
    """发行缺声明即故障；环境变量不能将单库包扩展为双库。"""
    path = Path(internal_root) / 'assets' / 'ocr' / 'backend_support.json'
    try:
        if path.stat().st_size > 4096:
            raise ValueError('声明过大')
        value = json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(value, dict) or set(value) != {'schema', 'profile'}
                or type(value['schema']) is not int or value['schema'] != 1):
            raise ValueError('声明格式不匹配')
        return OcrBackendSupport(value['profile'])
    except (OSError, ValueError, OcrBackendSupportError) as error:
        raise OcrBackendSupportError('发行 OCR 库支持声明缺失或无效，请恢复完整组件。') from error


def load_backend_support() -> OcrBackendSupport:
    # 不跨任务缓存；创建任务时读取一次，初始化/fallback 只使用此不可变对象。
    if getattr(sys, 'frozen', False):
        return load_packaged_backend_support(bundled_resources.bundled_root())
    return OcrBackendSupport(os.environ.get('NTE_OCR_PROFILE', 'default').strip().lower())
