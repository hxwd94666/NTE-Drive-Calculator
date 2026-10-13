# 验证两个 OCR 后端只使用一套经校验的共享模型资源。
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.integrations.vision.recognition import model_resources as resources


NTE_TEST_TIER = "core"


class OcrModelResourceTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[resources.OcrModelSpec, ...]:
        specs = []
        for index, keyword in enumerate(
            ("det_model_path", "rec_model_path", "cls_model_path"), start=1
        ):
            payload = f"model-{index}".encode()
            filename = f"model-{index}.onnx"
            (root / filename).write_bytes(payload)
            specs.append(
                resources.OcrModelSpec(
                    filename,
                    len(payload),
                    hashlib.sha256(payload).hexdigest().upper(),
                    keyword,
                )
            )
        return tuple(specs)

    def test_directory_validation_returns_all_backend_keywords(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            specs = self._fixture(root)
            with patch.object(resources, "OCR_MODEL_SPECS", specs):
                result = resources.validate_ocr_model_directory(root)

        self.assertEqual(
            {"det_model_path", "rec_model_path", "cls_model_path"},
            set(result),
        )

    def test_directory_validation_rejects_changed_model(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            specs = self._fixture(root)
            (root / specs[0].filename).write_bytes(b"changed")
            with patch.object(resources, "OCR_MODEL_SPECS", specs):
                with self.assertRaisesRegex(resources.OcrModelResourceError, "不匹配"):
                    resources.validate_ocr_model_directory(root)

    def test_packaged_layout_rejects_backend_model_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            internal = Path(temporary)
            shared = internal / "assets" / "ocr" / "models"
            shared.mkdir(parents=True)
            specs = self._fixture(shared)
            duplicate = internal / "rapidocr_openvino" / "models" / specs[0].filename
            duplicate.parent.mkdir(parents=True)
            duplicate.write_bytes(b"duplicate")
            with patch.object(resources, "OCR_MODEL_SPECS", specs):
                with self.assertRaisesRegex(resources.OcrModelResourceError, "重复"):
                    resources.validate_packaged_ocr_models(internal)



if __name__ == "__main__":
    unittest.main()
