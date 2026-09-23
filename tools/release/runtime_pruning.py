# 精确裁剪发行包中当前功能未使用的可选原生运行库。
"""Keep packaging trims narrow, auditable, and independent of source installs."""

from __future__ import annotations

import shutil
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

# 当前桌面界面只使用 Widgets 和原生 Windows 输入上下文。PDF 图片插件、
# Qt Virtual Keyboard 以及它独占的 QML/Quick 依赖不属于现有功能。
# 保留 Qt6OpenGL 与 opengl32sw 软件渲染后备，避免改变图形回退路径。
UNUSED_QT_BINARIES = (
    "PySide6/Qt6Pdf.dll",
    "PySide6/plugins/imageformats/qpdf.dll",
    "PySide6/Qt6Qml.dll",
    "PySide6/Qt6QmlMeta.dll",
    "PySide6/Qt6QmlModels.dll",
    "PySide6/Qt6QmlWorkerScript.dll",
    "PySide6/Qt6Quick.dll",
    "PySide6/Qt6VirtualKeyboard.dll",
    "PySide6/plugins/platforminputcontexts/qtvirtualkeyboardplugin.dll",
)
REQUIRED_QT_BINARIES = (
    "PySide6/Qt6Core.dll",
    "PySide6/Qt6Gui.dll",
    "PySide6/Qt6Widgets.dll",
    "PySide6/Qt6OpenGL.dll",
    "PySide6/opengl32sw.dll",
    "PySide6/plugins/platforms/qwindows.dll",
)

# 计算仅调用 scipy.optimize 的 MILP 与线性分配；这四个子包没有调用方，
# 也不属于上述算法在当前锁定版本中的导入闭包。
UNUSED_SCIPY_DIRECTORIES = (
    "scipy/stats",
    "scipy/interpolate",
    "scipy/integrate",
    "scipy/ndimage",
)
REQUIRED_SCIPY_DIRECTORIES = (
    "scipy/optimize",
    "scipy/sparse",
    "scipy/linalg",
    "scipy/spatial",
    "scipy/special",
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
    missing += [name for name in (*UNUSED_QT_BINARIES, *REQUIRED_QT_BINARIES) if not (internal / name).is_file()]
    missing += [name for name in (*UNUSED_SCIPY_DIRECTORIES, *REQUIRED_SCIPY_DIRECTORIES) if not (internal / name).is_dir()]
    if missing:
        raise RuntimeError("发行运行库布局变化，需要重新审查打包裁剪：" + ", ".join(missing))
    for name in UNUSED_SCIPY_DIRECTORIES:
        directory = internal / name
        if directory.is_symlink() or not directory.resolve().is_relative_to(internal):
            raise RuntimeError(f"打包裁剪路径越界：{directory}")

    optional = [
        *video_dlls,
        *(ov_dir / name for name in UNUSED_OPENVINO_LIBRARIES),
        *(internal / name for name in UNUSED_QT_BINARIES),
    ]
    removed: dict[str, int] = {}
    for path in optional:
        if not path.resolve().is_relative_to(internal):
            raise RuntimeError(f"打包裁剪路径越界：{path}")
        removed[path.relative_to(internal).as_posix()] = path.stat().st_size
        path.unlink()
    for name in UNUSED_SCIPY_DIRECTORIES:
        directory = internal / name
        removed[name] = sum(path.stat().st_size for path in directory.rglob("*") if path.is_file())
        shutil.rmtree(directory)
    return removed


def validate_pruned_runtime(internal: Path) -> None:
    """Reject a bundle that lost CPU OCR or retained the optional candidates."""
    cv2_dir = internal / "cv2"
    ov_dir = internal / "openvino" / "libs"
    remaining = list(cv2_dir.glob("opencv_videoio_ffmpeg*_64.dll"))
    remaining.extend(ov_dir / name for name in UNUSED_OPENVINO_LIBRARIES if (ov_dir / name).exists())
    remaining.extend(internal / name for name in UNUSED_QT_BINARIES if (internal / name).exists())
    missing = [name for name in REQUIRED_OPENVINO_LIBRARIES if not (ov_dir / name).is_file()]
    missing += [name for name in REQUIRED_QT_BINARIES if not (internal / name).is_file()]
    remaining.extend(internal / name for name in UNUSED_SCIPY_DIRECTORIES if (internal / name).exists())
    missing += [name for name in REQUIRED_SCIPY_DIRECTORIES if not (internal / name).is_dir()]
    if remaining or missing:
        raise RuntimeError(
            "打包运行库裁剪校验失败："
            + f"残留={[str(path) for path in remaining]}，缺少={missing}"
        )
