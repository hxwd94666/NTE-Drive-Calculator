# 精确裁剪发行包中当前功能未使用的可选原生运行库。
"""Keep packaging trims narrow, auditable, and independent of source installs."""

from __future__ import annotations

from pathlib import Path


# RapidOCR OpenVINO 的当前适配器固定用 CPU 读取 ONNX 模型。保留 CPU、
# ONNX/IR frontend、TBB、cache.json 和完整 ONNX Runtime/DirectML 后端。
UNUSED_OPENVINO_LIBRARIES = (
    "openvino_intel_gpu_plugin.dll",
    "openvino_intel_npu_plugin.dll",
    "openvino_tensorflow_frontend.dll",
    "openvino_tensorflow_lite_frontend.dll",
    "openvino_pytorch_frontend.dll",
    "openvino_paddle_frontend.dll",
)
REQUIRED_OPENVINO_LIBRARIES = (
    "openvino.dll",
    "openvino_intel_cpu_plugin.dll",
    "openvino_onnx_frontend.dll",
)


def prune_unused_runtime_binaries(internal: Path) -> dict[str, int]:
    """Remove only named optional DLLs from a finished onedir package.

    Fail before removing anything if the expected baseline layout changes. A
    dependency upgrade then requires a fresh review instead of silently
    publishing a package with different runtime capabilities.
    """
    internal = internal.resolve(strict=True)
    cv2_dir = internal / "cv2"
    ov_dir = internal / "openvino" / "libs"
    video_dlls = sorted(cv2_dir.glob("opencv_videoio_ffmpeg*_64.dll"))
    if len(video_dlls) != 1:
        raise RuntimeError("OpenCV FFmpeg 运行库数量变化，需要重新审查打包裁剪")
    missing = [name for name in REQUIRED_OPENVINO_LIBRARIES if not (ov_dir / name).is_file()]
    missing += [name for name in UNUSED_OPENVINO_LIBRARIES if not (ov_dir / name).is_file()]
    if missing:
        raise RuntimeError("OpenVINO 运行库布局变化，需要重新审查打包裁剪：" + ", ".join(missing))

    optional = [*video_dlls, *(ov_dir / name for name in UNUSED_OPENVINO_LIBRARIES)]
    removed: dict[str, int] = {}
    for path in optional:
        if not path.resolve().is_relative_to(internal):
            raise RuntimeError(f"打包裁剪路径越界：{path}")
        removed[path.relative_to(internal).as_posix()] = path.stat().st_size
        path.unlink()
    return removed


def validate_pruned_runtime(internal: Path) -> None:
    """Reject a bundle that lost CPU OCR or retained the optional candidates."""
    cv2_dir = internal / "cv2"
    ov_dir = internal / "openvino" / "libs"
    remaining = list(cv2_dir.glob("opencv_videoio_ffmpeg*_64.dll"))
    remaining.extend(ov_dir / name for name in UNUSED_OPENVINO_LIBRARIES if (ov_dir / name).exists())
    missing = [name for name in REQUIRED_OPENVINO_LIBRARIES if not (ov_dir / name).is_file()]
    if remaining or missing:
        raise RuntimeError(
            "打包运行库裁剪校验失败："
            + f"残留={[str(path) for path in remaining]}，缺少={missing}"
        )
