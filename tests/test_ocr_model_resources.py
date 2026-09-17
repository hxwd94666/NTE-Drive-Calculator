# 验证两个 OCR 后端只使用一套经校验的共享模型资源。
from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.integrations import ocr_model_resources as resources
from src.scanner import ocr_engine


NTE_TEST_TIER = "core"


class OcrModelResourceTests(unittest.TestCase):
    def tearDown(self) -> None:
        resources.rapidocr_model_kwargs.cache_clear()

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

    def test_backend_factories_receive_the_same_explicit_model_paths(self) -> None:
        model_kwargs = {
            "det_model_path": "shared/det.onnx",
            "rec_model_path": "shared/rec.onnx",
            "cls_model_path": "shared/cls.onnx",
        }
        openvino_factory = Mock(return_value=Mock())
        onnx_factory = Mock(return_value=Mock())
        with (
            patch.dict(
                "sys.modules",
                {
                    "rapidocr_openvino": SimpleNamespace(RapidOCR=openvino_factory),
                    "rapidocr_onnxruntime": SimpleNamespace(RapidOCR=onnx_factory),
                },
            ),
            patch.object(ocr_engine, "_warmup"),
        ):
            self.assertIsNotNone(ocr_engine._create_openvino_ocr(model_kwargs)[0])
            self.assertIsNotNone(ocr_engine._create_onnx_cpu_ocr(model_kwargs)[0])
            self.assertIsNotNone(
                ocr_engine._create_directml_ocr(["DmlExecutionProvider"], model_kwargs)[0]
            )

        openvino_factory.assert_called_once_with(use_cls=False, **model_kwargs)
        self.assertEqual(2, onnx_factory.call_count)
        cpu_kwargs = onnx_factory.call_args_list[0].kwargs
        directml_kwargs = onnx_factory.call_args_list[1].kwargs
        self.assertEqual(model_kwargs, {key: cpu_kwargs[key] for key in model_kwargs})
        self.assertEqual(model_kwargs, {key: directml_kwargs[key] for key in model_kwargs})
        self.assertTrue(directml_kwargs["det_use_dml"])
        self.assertTrue(directml_kwargs["rec_use_dml"])


if __name__ == "__main__":
    unittest.main()
