# 核对实际解码的模板字节与任务级图片布局，供确认及提交复核。
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import numpy as np

from src.integrations.vision.recognition.config import RecognitionLayout
from src.integrations.vision.recognition.language_resources import OcrRuntimeResources
from src.integrations.vision.recognition.resources import RecognitionResourceError


def _version(path: Path) -> tuple[int, ...]:
    state = path.stat()
    return (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns)


def immutable_template_pixels(image: np.ndarray) -> np.ndarray:
    # bytes 底层不可写；基础和缩放矩阵都不能被外部重新开启写权限。
    return np.frombuffer(image.tobytes(), dtype=image.dtype).reshape(image.shape)


def _read_template_payload(path: Path) -> tuple[bytes, str, tuple[int, ...]]:
    before = _version(path)
    payload = path.read_bytes()
    if _version(path) != before or len(payload) != before[2]:
        raise RecognitionResourceError("图片模板读取期间发生变化，请重新解析。")
    return payload, hashlib.sha256(payload).hexdigest(), before


@dataclass(frozen=True)
class CaptureTemplateFile:
    path: Path
    sha256: str
    version: tuple[int, ...]

    @classmethod
    def read(cls, path: Path) -> tuple[CaptureTemplateFile, bytes]:
        payload, digest, version = _read_template_payload(Path(path))
        return cls(Path(path).resolve(), digest, version), payload

    def verify(self) -> None:
        try:
            actual, _payload = self.read(self.path)
            if actual != self:
                raise RecognitionResourceError("扫描标记模板已更新，请重新采集；原截图保留。")
        except OSError as error:
            raise RecognitionResourceError("扫描标记模板检查失败，请恢复后重新采集。") from error


@dataclass(frozen=True)
class ShapeTemplateFile:
    shape_id: str
    path: Path
    sha256: str
    version: tuple[int, ...]

    @classmethod
    def read(cls, shape_id: str, path: Path) -> tuple[ShapeTemplateFile, bytes]:
        # 同一份字节既用于哈希也用于 imdecode；不在哈希后再次从路径解码。
        payload, digest, version = _read_template_payload(path)
        return cls(shape_id, path.resolve(), digest, version), payload

    def verify(self) -> None:
        try:
            actual, _payload = self.read(self.shape_id, self.path)
            if actual != self:
                raise RecognitionResourceError("形状模板已更新，请重新解析；当前背包保留。")
        except OSError as error:
            raise RecognitionResourceError("形状模板检查失败，请恢复模板后重新解析。") from error


@dataclass(frozen=True)
class RecognitionImageProfile:
    layout: RecognitionLayout
    template_files: tuple[ShapeTemplateFile, ...]
    ocr_resources: OcrRuntimeResources | None = None
    capture_template: CaptureTemplateFile | None = None

    def verify(self) -> None:
        if self.capture_template is not None:
            self.capture_template.verify()
        if self.ocr_resources is not None:
            self.ocr_resources.verify_unchanged()
        for evidence in self.template_files:
            evidence.verify()

    def public_identity(self) -> dict:
        # 只公开规则与内容身份，不向库存 metadata 写本机路径或模板原文。
        layout = json.dumps((self.layout.base_size, self.layout.regions), ensure_ascii=False,
                            separators=(",", ":")).encode("utf-8")
        return {"layout_sha256": hashlib.sha256(layout).hexdigest(),
                "shape_templates": {file.shape_id: file.sha256 for file in self.template_files},
                "ocr_resources": self.ocr_resources.public_identity() if self.ocr_resources is not None else None,
                "capture_new_tag_sha256": self.capture_template.sha256 if self.capture_template is not None else None}
