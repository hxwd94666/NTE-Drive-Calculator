# 验证发行包只裁剪已核对的可选运行库。
import tempfile
import unittest
from pathlib import Path

from tools.release.runtime_pruning import (
    REQUIRED_OPENVINO_LIBRARIES,
    UNUSED_OPENVINO_LIBRARIES,
    prune_unused_runtime_binaries,
    validate_pruned_runtime,
)


class RuntimePruningTests(unittest.TestCase):
    def _bundle(self, root: Path) -> Path:
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
        return internal

    def test_prunes_only_optional_video_and_openvino_backends(self):
        with tempfile.TemporaryDirectory() as temporary:
            internal = self._bundle(Path(temporary))
            removed = prune_unused_runtime_binaries(internal)
            validate_pruned_runtime(internal)
            self.assertEqual(1 + len(UNUSED_OPENVINO_LIBRARIES), len(removed))
            self.assertTrue((internal / "cv2" / "cv2.pyd").is_file())
            self.assertTrue((internal / "openvino" / "libs" / "cache.json").is_file())
            for name in REQUIRED_OPENVINO_LIBRARIES:
                self.assertTrue((internal / "openvino" / "libs" / name).is_file())

    def test_layout_change_fails_before_any_deletion(self):
        with tempfile.TemporaryDirectory() as temporary:
            internal = self._bundle(Path(temporary))
            (internal / "openvino" / "libs" / UNUSED_OPENVINO_LIBRARIES[0]).unlink()
            video = internal / "cv2" / "opencv_videoio_ffmpeg500_64.dll"
            with self.assertRaisesRegex(RuntimeError, "重新审查"):
                prune_unused_runtime_binaries(internal)
            self.assertTrue(video.is_file())


if __name__ == "__main__":
    unittest.main()
