# 校验并解析两个 RapidOCR 后端共享的只读模型资源。
"""Shared OCR model identity, source fallback, and packaged-layout checks."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterable

from src.integrations.bundled_resources import bundled_ocr_model_dir


RAPIDOCR_PACKAGE_NAMES = ("rapidocr_openvino", "rapidocr_onnxruntime")


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


def validate_ocr_model_directory(directory: str | Path) -> dict[str, Path]:
    """Validate every shared model and return RapidOCR keyword paths."""

    root = Path(directory).resolve()
    resolved: dict[str, Path] = {}
    issues: list[str] = []
    for spec in OCR_MODEL_SPECS:
        path = root / spec.filename
        if not path.is_file():
            issues.append(f"缺少 {spec.filename}")
            continue
        size = path.stat().st_size
        if size != spec.size_bytes:
            issues.append(f"{spec.filename} 大小不匹配：{size} != {spec.size_bytes}")
            continue
        digest = _sha256(path)
        if digest != spec.sha256:
            issues.append(f"{spec.filename} SHA-256 不匹配")
            continue
        resolved[spec.keyword] = path
    if issues:
        raise OcrModelResourceError(
            f"共享 OCR 模型资源无效（{root}）：" + "；".join(issues)
        )
    return resolved


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


def build_source_ocr_models() -> dict[str, Path]:
    """Select one verified installed model set and reject package drift."""

    directories = _installed_package_model_dirs()
    if not directories:
        raise OcrModelResourceError("构建环境未安装 RapidOCR 模型资源")
    verified: list[dict[str, Path]] = []
    for directory in directories:
        verified.append(validate_ocr_model_directory(directory))
    # Validation uses one immutable size/hash contract, so every verified
    # package is byte-identical. Package the first set only.
    return verified[0]


@lru_cache(maxsize=1)
def rapidocr_model_kwargs() -> dict[str, str]:
    """Return one verified model set for all OCR backends."""

    shared_directory = bundled_ocr_model_dir()
    if shared_directory.is_dir() or getattr(sys, "frozen", False):
        return {
            keyword: str(path)
            for keyword, path in validate_ocr_model_directory(shared_directory).items()
        }
    # Source checkouts use the dependency's verified copy. Release builds
    # always carry the app-owned shared directory above.
    return {
        keyword: str(path)
        for keyword, path in build_source_ocr_models().items()
    }


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
    return shared
