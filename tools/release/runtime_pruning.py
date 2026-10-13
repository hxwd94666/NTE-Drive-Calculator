# 精确裁剪发行包中当前功能未使用的可选原生运行库。
"""Keep packaging trims narrow, auditable, and independent of source installs."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from tools.release.managed_paths import iter_plain_tree, plain_directory_root, plain_member_stat
from pathlib import Path
from src.integrations.vision.recognition.backend_support import OcrBackendSupport


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

# 当前锁定 Windows ORT 的正式 CPU/DirectML 共同成员；设备插件单独核对。
REQUIRED_ORT_LIBRARIES = (
    "onnxruntime.dll", "onnxruntime_providers_shared.dll", "onnxruntime_pybind11_state.pyd",
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
# 工坊模板 HTTPS 请求依赖 QtNetwork 与 Windows Schannel，不作为可选运行库裁剪。
REQUIRED_QT_BINARIES = (
    "PySide6/Qt6Core.dll",
    "PySide6/Qt6Gui.dll",
    "PySide6/Qt6Widgets.dll",
    "PySide6/Qt6Network.dll",
    "PySide6/Qt6OpenGL.dll",
    "PySide6/opengl32sw.dll",
    "PySide6/plugins/platforms/qwindows.dll",
    "PySide6/plugins/tls/qschannelbackend.dll",
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



@dataclass(frozen=True, slots=True)
class _Removal:
    path: Path
    group: str
    directory: bool
    size: int


def _require(root: Path, path: Path, *, directory: bool = False) -> None:
    info = plain_member_stat(root, path)
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise ValueError("发行运行库成员类型变化。")


def _required_layout(root: Path, support: OcrBackendSupport) -> None:
    _require(root, root / "cv2", directory=True)
    if "openvino" in support.libraries:
        for name in REQUIRED_OPENVINO_LIBRARIES:
            _require(root, root / "openvino/libs" / name)
    else:
        # 排除库意外混入也拒绝；裁剪不替选择阶段删除整库或未知目录。
        if _present(root, root / "openvino") or _present(root, root / "rapidocr_openvino"):
            raise ValueError("发行目录混入排除的 OpenVINO 库。")
    if "onnxruntime" in support.libraries:
        for name in REQUIRED_ORT_LIBRARIES:
            _require(root, root / "onnxruntime/capi" / name)
        if support.allows_directml:
            _require(root, root / "onnxruntime/capi/DirectML.dll")
    elif _present(root, root / "onnxruntime") or _present(root, root / "rapidocr_onnxruntime"):
        raise ValueError("发行目录混入排除的 ONNX Runtime 库。")
    for name in REQUIRED_QT_BINARIES:
        _require(root, root / name)
    for name in REQUIRED_SCIPY_DIRECTORIES:
        _require(root, root / name, directory=True)
        # 保留目录也核对普通成员身份，不让有效根掩盖子树中的链接。
        for _path, _info in iter_plain_tree(root / name):
            pass


def _present(root: Path, path: Path) -> bool:
    try:
        plain_member_stat(root, path)
    except FileNotFoundError:
        return False
    return True


def prune_unused_runtime_binaries(internal: Path, *,
                                  support: OcrBackendSupport = OcrBackendSupport("default")) -> dict[str, int]:
    """仅裁剪隔离 onedir 候选；完整身份/类型/字节预检失败时不改任何成员。"""
    plan: list[_Removal] = []
    totals: dict[str, int] = {}
    try:
        root = plain_directory_root(internal)
        _required_layout(root, support)
        videos = sorted((root / "cv2").glob("opencv_videoio_ffmpeg*_64.dll"))
        if len(videos) != 1:
            raise ValueError("OpenCV FFmpeg 运行库数量变化。")
        files = [*videos, *(root / "openvino/libs" / name for name in UNUSED_OPENVINO_LIBRARIES if "openvino" in support.libraries),
                 *(root / name for name in UNUSED_QT_BINARIES)]
        for path in files:
            _require(root, path)
            size = plain_member_stat(root, path).st_size
            name = path.relative_to(root).as_posix()
            totals[name] = size
            plan.append(_Removal(path, name, False, size))
        for name in UNUSED_SCIPY_DIRECTORIES:
            directory = root / name
            _require(root, directory, directory=True)
            # 先枚举完整子树，再固定逐文件和由深至浅目录计划，不递归删除未知新成员。
            members = list(iter_plain_tree(directory))
            totals[name] = sum(info.st_size for _path, info in members if stat.S_ISREG(info.st_mode))
            plan.extend(_Removal(path, name, False, info.st_size) for path, info in members if stat.S_ISREG(info.st_mode))
            plan.extend(_Removal(path, name, True, 0) for path, info in
                        sorted(members, key=lambda pair: len(pair[0].parts), reverse=True) if stat.S_ISDIR(info.st_mode))
        # 所有目标最终复核完成后才进入修改阶段；前面的合法文件不为后面的坏路径垫付。
        for item in plan:
            _require(root, item.path, directory=item.directory)
    except (OSError, ValueError) as error:
        raise RuntimeError("发行运行库布局或路径变化，需要重新审查打包裁剪。") from error

    removed_files = removed_directories = 0
    try:
        for item in plan:
            # 候选须独占；仍在每次确切操作前检查路径，目标变化即停止，不扩大删除范围。
            _require(root, item.path, directory=item.directory)
            if item.directory:
                item.path.rmdir()
                removed_directories += 1
            else:
                item.path.unlink()
                removed_files += 1
    except (OSError, ValueError) as error:
        # 文件系统操作不是事务；如实报告部分完成，拒绝将该候选继续发行。
        raise RuntimeError(f"发行运行库裁剪中断，已删除文件 {removed_files}、目录 {removed_directories}；请重建候选。") from error
    return totals


def validate_pruned_runtime(internal: Path, *,
                            support: OcrBackendSupport = OcrBackendSupport("default")) -> None:
    """核对必要成员及其身份；断链残留和包内/包外重定向同样拒绝。"""
    try:
        root = plain_directory_root(internal)
        _required_layout(root, support)
        remaining = list((root / "cv2").glob("opencv_videoio_ffmpeg*_64.dll"))
        candidates = [*(root / "openvino/libs" / name for name in UNUSED_OPENVINO_LIBRARIES if "openvino" in support.libraries),
                      *(root / name for name in UNUSED_QT_BINARIES), *(root / name for name in UNUSED_SCIPY_DIRECTORIES)]
        remaining.extend(path for path in candidates if _present(root, path))
        pillow = root / "PIL"
        if _present(root, pillow):
            _require(root, pillow, directory=True)
            remaining.extend(pillow.glob("_avif*.pyd"))
        if remaining:
            raise ValueError("发行运行库裁剪存在残留：" + ", ".join(path.relative_to(root).as_posix() for path in remaining))
    except (OSError, ValueError) as error:
        raise RuntimeError("打包运行库裁剪校验失败：" + str(error)) from error
