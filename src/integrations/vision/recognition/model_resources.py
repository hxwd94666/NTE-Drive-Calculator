# 校验并解析两个 RapidOCR 后端共享的只读模型资源。
"""Shared OCR model identity, source fallback, and packaged-layout checks."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.integrations.bundled_resources import bundled_ocr_model_dir
from src.integrations.vision.recognition.backend_support import OcrBackendSupport, load_backend_support, load_packaged_backend_support


RAPIDOCR_PACKAGE_NAMES = OcrBackendSupport("default").rapidocr_packages


@dataclass(frozen=True)
class OcrModelSpec:
    filename: str
    size_bytes: int
    sha256: str
    keyword: str


OCR_MODEL_SPECS = (
    OcrModelSpec(
        "ch_PP-OCRv4_det_infer.onnx",
        4_745_517,
        "D2A7720D45A54257208B1E13E36A8479894CB74155A5EFE29462512D42F49DA9",
        "det_model_path",
    ),
    OcrModelSpec(
        "ch_PP-OCRv4_rec_infer.onnx",
        10_857_958,
        "48FC40F24F6D2A207A2B1091D3437EB3CC3EB6B676DC3EF9C37384005483683B",
        "rec_model_path",
    ),
    OcrModelSpec(
        "ch_ppocr_mobile_v2.0_cls_infer.onnx",
        585_532,
        "E47ACEDF663230F8863FF1AB0E64DD2D82B838FCEB5957146DAB185A89D6215C",
        "cls_model_path",
    ),
)


class OcrModelResourceError(RuntimeError):
    """Raised when the immutable shared OCR model set is missing or altered."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _file_version(path: Path) -> tuple[int, int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


@dataclass(frozen=True)
class OcrModelFile:
    spec: OcrModelSpec
    path: Path
    version: tuple[int, int, int, int, int]


@dataclass(frozen=True)
class OcrModelResources:
    """本次初始化的模型文件身份；不声称覆盖字符字典、布局与模板。"""
    files: tuple[OcrModelFile, ...]

    def model_kwargs(self) -> dict[str, str]:
        # 每个候选独占参数字典，后端或调用方修改不污染其他任务。
        return {entry.spec.keyword: str(entry.path) for entry in self.files}

    def verify_unchanged(self) -> None:
        for entry in self.files:
            _validate_model_file(entry.path, entry.spec, expected_version=entry.version)


def _validate_model_file(path: Path, spec: OcrModelSpec, *,
                         expected_version=None) -> OcrModelFile:
    try:
        before = _file_version(path)
        if not path.is_file():
            raise OcrModelResourceError(f'缺少 {spec.filename}')
        if before[2] != spec.size_bytes:
            raise OcrModelResourceError(f'{spec.filename} 大小不匹配')
        if expected_version is not None and before != expected_version:
            raise OcrModelResourceError(f'{spec.filename} 文件身份不匹配')
        digest = _sha256(path)
        after = _file_version(path)
        if before != after or digest != spec.sha256:
            raise OcrModelResourceError(f'{spec.filename} SHA-256 或读取期间身份不匹配')
        return OcrModelFile(spec, path, after)
    except OSError as exc:
        raise OcrModelResourceError(f'共享 OCR 模型资源无效（{path}）：{exc}') from exc


def _directory_resources(directory: str | Path) -> OcrModelResources:
    root = Path(directory).resolve()
    return OcrModelResources(tuple(_validate_model_file(root / spec.filename, spec)
                                   for spec in OCR_MODEL_SPECS))


def validate_ocr_model_directory(directory: str | Path) -> dict[str, Path]:
    """按既有清单检查每个模型；构建入口不导入任何原生推理库。"""
    return {entry.spec.keyword: entry.path for entry in _directory_resources(directory).files}


def _installed_package_model_dirs(
    package_names: Iterable[str] = RAPIDOCR_PACKAGE_NAMES,
) -> tuple[Path, ...]:
    directories: list[Path] = []
    for package_name in package_names:
        spec = importlib.util.find_spec(package_name)
        if spec is None or spec.origin is None:
            continue
        directory = Path(spec.origin).resolve().parent / "models"
        if directory.is_dir():
            directories.append(directory)
    return tuple(directories)


def _source_ocr_model_resources(support: OcrBackendSupport) -> OcrModelResources:
    directories = _installed_package_model_dirs(support.rapidocr_packages)
    if not directories:
        raise OcrModelResourceError(f'OCR {support.profile} 支持集合中缺少 RapidOCR 模型资源')
    # 只校验本次支持集合的已安装副本；排除库即使存在或漂移也不读取。
    verified = tuple(_directory_resources(directory) for directory in directories)
    return verified[0]


def build_source_ocr_models(*, support: OcrBackendSupport | None = None) -> dict[str, Path]:
    """构建只复制一组字节等价的已验证模型，不加载原生后端。"""
    return {entry.spec.keyword: entry.path for entry in _source_ocr_model_resources(support if support is not None else load_backend_support()).files}


def load_ocr_model_resources(*, support: OcrBackendSupport | None = None) -> OcrModelResources:
    support = support if support is not None else load_backend_support()
    shared_directory = bundled_ocr_model_dir()
    if shared_directory.is_dir() or getattr(sys, 'frozen', False):
        return _directory_resources(shared_directory)
    return _source_ocr_model_resources(support)


def rapidocr_model_kwargs() -> dict[str, str]:
    """兼供资源调用方获取本次校验的私有参数，不缓存可变字典或路径结论。"""
    return load_ocr_model_resources().model_kwargs()


def validate_packaged_ocr_models(internal_root: str | Path) -> dict[str, Path]:
    """Require one shared model set and reject duplicated package copies."""

    root = Path(internal_root).resolve()
    shared = validate_ocr_model_directory(root / "assets" / "ocr" / "models")
    duplicates = [
        path.relative_to(root).as_posix()
        for package_name in RAPIDOCR_PACKAGE_NAMES
        for path in (root / package_name / "models").glob("*.onnx")
        if path.is_file()
    ]
    if duplicates:
        raise OcrModelResourceError(
            "发行目录仍包含重复 RapidOCR 模型：" + "、".join(sorted(duplicates))
        )
    from src.integrations.vision.recognition.language_resources import OcrRuntimeResources, load_ocr_dictionary
    runtime = OcrRuntimeResources(_directory_resources(root / "assets" / "ocr" / "models"),
                                 load_ocr_dictionary(root / "assets" / "ocr" / "ppocr_keys_v1.txt"))
    runtime.require_model_binding()
    duplicate_dictionary = root / "rapidocr_openvino" / "ch_ppocr_rec" / "ppocr_keys_v1.txt"
    if duplicate_dictionary.is_file():
        raise OcrModelResourceError("发行目录仍包含重复 RapidOCR 字符字典。")
    load_packaged_backend_support(root)
    return shared
