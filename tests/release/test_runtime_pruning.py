# 验证发行包只裁剪已核对的可选运行库。
import tempfile
import unittest
from pathlib import Path
import os
import subprocess

import pytest

from tools.release.runtime_pruning import (
    REQUIRED_OPENVINO_LIBRARIES,
    REQUIRED_ORT_LIBRARIES,
    REQUIRED_QT_BINARIES,
    REQUIRED_SCIPY_DIRECTORIES,
    UNUSED_OPENVINO_LIBRARIES,
    UNUSED_QT_BINARIES,
    UNUSED_SCIPY_DIRECTORIES,
    prune_unused_runtime_binaries,
    validate_pruned_runtime,
)


def _bundle(root: Path) -> Path:
    internal = root / "_internal"
    cv2 = internal / "cv2"
    openvino = internal / "openvino" / "libs"
    cv2.mkdir(parents=True)
    openvino.mkdir(parents=True)
    (cv2 / "cv2.pyd").write_bytes(b"image processing")
    (cv2 / "opencv_videoio_ffmpeg500_64.dll").write_bytes(b"video")
    for name in (*REQUIRED_OPENVINO_LIBRARIES, *UNUSED_OPENVINO_LIBRARIES):
        (openvino / name).write_bytes(name.encode("ascii"))
    (openvino / "cache.json").write_bytes(b"cache")
    for name in (*REQUIRED_QT_BINARIES, *UNUSED_QT_BINARIES):
        path = internal / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode("ascii"))
    for name in (*REQUIRED_SCIPY_DIRECTORIES, *UNUSED_SCIPY_DIRECTORIES):
        path = internal / name
        path.mkdir(parents=True, exist_ok=True)
        (path / "sample.pyd").write_bytes(name.encode("ascii"))
    ort = internal / "onnxruntime/capi"
    ort.mkdir(parents=True)
    for name in (*REQUIRED_ORT_LIBRARIES, "DirectML.dll"):
        (ort / name).write_bytes(name.encode("ascii"))
    return internal


class RuntimePruningTests(unittest.TestCase):
    def test_prunes_only_optional_video_and_openvino_backends(self):
        with tempfile.TemporaryDirectory() as temporary:
            internal = _bundle(Path(temporary))
            removed = prune_unused_runtime_binaries(internal)
            validate_pruned_runtime(internal)
            self.assertEqual(
                1 + len(UNUSED_OPENVINO_LIBRARIES) + len(UNUSED_QT_BINARIES)
                + len(UNUSED_SCIPY_DIRECTORIES),
                len(removed),
            )
            self.assertTrue((internal / "cv2" / "cv2.pyd").is_file())
            self.assertTrue((internal / "openvino" / "libs" / "cache.json").is_file())
            for name in REQUIRED_OPENVINO_LIBRARIES:
                self.assertTrue((internal / "openvino" / "libs" / name).is_file())
            for name in REQUIRED_QT_BINARIES:
                self.assertTrue((internal / name).is_file())
            for name in REQUIRED_SCIPY_DIRECTORIES:
                self.assertTrue((internal / name).is_dir())

    def test_layout_change_fails_before_any_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            internal = _bundle(Path(temporary))
            (internal / "openvino" / "libs" / UNUSED_OPENVINO_LIBRARIES[0]).unlink()
            video = internal / "cv2" / "opencv_videoio_ffmpeg500_64.dll"
            with self.assertRaisesRegex(RuntimeError, "重新审查"):
                prune_unused_runtime_binaries(internal)
            self.assertTrue(video.is_file())

    def test_bundle_rejects_accidentally_collected_avif_binary(self):
        with tempfile.TemporaryDirectory() as temporary:
            internal = _bundle(Path(temporary))
            prune_unused_runtime_binaries(internal)
            optional = internal / "PIL" / "_avif.cp311-win_amd64.pyd"
            optional.parent.mkdir()
            optional.write_bytes(b"unused optional codec")
            with self.assertRaisesRegex(RuntimeError, "_avif"):
                validate_pruned_runtime(internal)



def _junction(link: Path, target: Path) -> None:
    """Windows 使用无需提权的目录联接；所有路径只在本次 pytest 临时根内。"""
    # 调用方已给出本次临时根的确切子路径，既不枚举磁盘也不删除外部目录。
    assert link.is_absolute() and target.is_absolute()
    if os.name == "nt":
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        link.symlink_to(target, target_is_directory=True)


def _snapshot(internal: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in internal.rglob("*") if path.is_file()}


@pytest.mark.parametrize("redirect", ["late_optional", "required", "internal_parent", "recursive_child", "root", "ancestor"])
def test_linked_plan_is_rejected_before_any_original_file_changes(tmp_path, redirect):
    internal = _bundle(tmp_path / "bundle")
    before = _snapshot(internal)
    argument = internal
    if redirect == "late_optional":
        source = internal / "openvino/libs"
        target = tmp_path / "external-openvino"
    elif redirect == "required":
        source = internal / REQUIRED_SCIPY_DIRECTORIES[0]
        target = tmp_path / "external-required"
    elif redirect == "internal_parent":
        source = internal / "PySide6/plugins/imageformats"
        target = internal / "unmanaged-imageformats"
    elif redirect == "recursive_child":
        source = internal / UNUSED_SCIPY_DIRECTORIES[0] / "nested-link"
        target = tmp_path / "external-child"
        target.mkdir()
        (target / "keep.txt").write_bytes(b"outside canary")
    elif redirect == "root":
        source, target = tmp_path / "linked-internal", internal
        argument = source
    else:
        source, target = tmp_path / "linked-parent", internal.parent
        argument = source / "_internal"
    # 移动只涉及本次拥有的合成目录，先核对两端 resolved 路径留在临时根内。
    assert source.resolve().is_relative_to(tmp_path.resolve())
    assert target.resolve().is_relative_to(tmp_path.resolve())
    if redirect in {"late_optional", "required", "internal_parent"}:
        source.rename(target)
    _junction(source, target)
    try:
        with pytest.raises(RuntimeError):
            prune_unused_runtime_binaries(argument)
        assert all(path.is_file() and path.read_bytes() == data for path, data in before.items())
        if redirect == "recursive_child":
            assert (target / "keep.txt").read_bytes() == b"outside canary"
    finally:
        # 只移除确切联接本身；真实目标仍由本次临时夹具持有。
        if source.is_symlink() or source.exists():
            if os.name == "nt":
                source.rmdir()
            else:
                source.unlink()


@pytest.mark.parametrize("kind", ["required_parent", "broken_optional"])
def test_postcheck_rejects_linked_required_or_broken_optional_member(tmp_path, kind):
    internal = _bundle(tmp_path)
    prune_unused_runtime_binaries(internal)
    if kind == "required_parent":
        link = internal / REQUIRED_SCIPY_DIRECTORIES[0]
        target = tmp_path / "external-required"
        assert link.resolve().is_relative_to(tmp_path.resolve()) and target.resolve().is_relative_to(tmp_path.resolve())
        link.rename(target)
    else:
        link = internal / UNUSED_SCIPY_DIRECTORIES[0]
        target = tmp_path / "broken-target"
        target.mkdir()
    _junction(link, target)
    if kind == "broken_optional":
        target.rmdir()
    try:
        with pytest.raises(RuntimeError):
            validate_pruned_runtime(internal)
    finally:
        if os.name == "nt":
            link.rmdir()
        else:
            link.unlink()


def test_io_failure_reports_exact_partial_progress_without_claiming_rollback(tmp_path, monkeypatch):
    internal = _bundle(tmp_path)
    before = _snapshot(internal)
    video = internal / "cv2/opencv_videoio_ffmpeg500_64.dll"
    blocked = internal / "openvino/libs" / UNUSED_OPENVINO_LIBRARIES[0]
    original = Path.unlink

    def fail_exact_member(path, *args, **kwargs):
        if path == blocked:
            raise PermissionError("synthetic occupied member")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as context:
        context.setattr(Path, "unlink", fail_exact_member)
        with pytest.raises(RuntimeError, match="已删除文件 1、目录 0；请重建候选") as caught:
            prune_unused_runtime_binaries(internal)
    assert isinstance(caught.value.__cause__, PermissionError)
    assert not video.exists()
    assert all(path.read_bytes() == data for path, data in before.items() if path != video)


@pytest.mark.parametrize('profile', ['openvino', 'onnx_cpu', 'onnx_directml'])
def test_single_library_pruning_keeps_selected_runtime_and_rejects_selected_missing(tmp_path, profile):
    from src.integrations.vision.recognition.backend_support import OcrBackendSupport
    support = OcrBackendSupport(profile)
    internal = _bundle(tmp_path)
    # 仅在本次合成候选删除已知夹具成员，不枚举或改动真实依赖环境。
    excluded = 'onnxruntime' if profile == 'openvino' else 'openvino'
    excluded_root = internal / excluded
    for path in sorted(excluded_root.rglob('*'), key=lambda path: len(path.parts), reverse=True):
        path.rmdir() if path.is_dir() else path.unlink()
    excluded_root.rmdir()
    removed = prune_unused_runtime_binaries(internal, support=support)
    validate_pruned_runtime(internal, support=support)
    assert all(not name.startswith(excluded + '/') for name in removed)
    selected = internal / ('openvino/libs/openvino.dll' if profile == 'openvino' else 'onnxruntime/capi/onnxruntime.dll')
    assert selected.is_file()
    selected.unlink()
    with pytest.raises(RuntimeError):
        validate_pruned_runtime(internal, support=support)


def test_excluded_runtime_mixed_in_fails_before_any_pruning(tmp_path):
    from src.integrations.vision.recognition.backend_support import OcrBackendSupport
    internal = _bundle(tmp_path)
    with pytest.raises(RuntimeError):
        prune_unused_runtime_binaries(internal, support=OcrBackendSupport('openvino'))
    assert (internal / 'cv2/opencv_videoio_ffmpeg500_64.dll').is_file()
