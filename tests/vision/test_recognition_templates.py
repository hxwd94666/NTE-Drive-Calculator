# 验证中文路径模板、截图区域和装备分类的识别行为。
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from src.integrations.vision.recognition.shape_recognizer import ShapeRecognizer


class GameContentRectTests(unittest.TestCase):
    def test_game_content_rect_supports_standard_and_top_aligned_tall_clients(self):
        from src.integrations.vision.recognition.config import ScannerConfig
        from src.integrations.vision.window_capture import game_content_rect

        cases = [
            ((1920, 1080), (0, 0, 1920, 1080)),
            ((2560, 1440), (0, 0, 2560, 1440)),
            ((3840, 2160), (0, 0, 3840, 2160)),
            ((2560, 1600), (0, 0, 2560, 1440)),
        ]
        for screen_size, expected in cases:
            with self.subTest(screen_size=screen_size):
                self.assertEqual(expected, game_content_rect(*screen_size))

        _name, regions = ScannerConfig.get_region_profiles(2560, 1600)[0]
        self.assertEqual(ScannerConfig.REGIONS_2K["drive_shape_icon"], regions["drive_shape_icon"])


class ShapeTemplateReadinessTests(unittest.TestCase):
    def test_readiness_uses_decoded_canonical_templates(self):
        with tempfile.TemporaryDirectory(prefix="nte_shapes_") as tmp:
            root = Path(tmp)
            ok, encoded = cv2.imencode(".png", np.arange(64, dtype=np.uint8).reshape(8, 8))
            self.assertTrue(ok)
            encoded.tofile(str(root / "H_2.png"))
            recognizer = ShapeRecognizer(tmp, valid_shape_ids=frozenset({"H_2", "V_2"}))
            with self.assertRaisesRegex(RuntimeError, "V_2"):
                recognizer.require_complete_templates()
            encoded.tofile(str(root / "V_2.png"))
            recognizer._load_templates()
            recognizer.require_complete_templates()


class EquipmentClassifierTests(unittest.TestCase):
    def _processor(self, shape_result, ocr_texts=None, fail_on_ocr=False):
        from types import SimpleNamespace

        class ShapeRecognizer:
            def recognize(self, _crop):
                return dict(shape_result)

        class OcrEngine:
            def __init__(self):
                self.calls = 0

            def extract_text(self, _crop):
                self.calls += 1
                if fail_on_ocr:
                    raise AssertionError("identity OCR should be skipped")
                return list(ocr_texts or [])

        ocr_engine = OcrEngine()
        return SimpleNamespace(
            DRIVE_TYPE_CONFIDENCE=0.86,
            shape_recognizer=ShapeRecognizer(),
            ocr_engine=ocr_engine,
            parser=SimpleNamespace(REAL_SETS_WHITE_LIST=["森林萤火之心"]),
        )

    def test_high_confidence_drive_shape_skips_identity_ocr(self):
        from src.integrations.vision.recognition.equipment_classifier import classify_item

        processor = self._processor(
            {"shape_id": "H_2", "confidence": 0.96},
            fail_on_ocr=True,
        )
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        regions = {
            "drive_shape_icon": (0, 0, 20, 20),
            "identity_check": (20, 0, 40, 20),
        }

        item_type, _profile, _regions, shape_res, hub_text = classify_item(
            processor,
            img,
            [("top_16_9", regions)],
        )

        self.assertEqual("drive", item_type)
        self.assertEqual("H_2", shape_res["shape_id"])
        self.assertTrue(shape_res["identity_skipped"])
        self.assertEqual("", hub_text)
        self.assertEqual(0, processor.ocr_engine.calls)

    def test_lower_confidence_shape_still_uses_identity_ocr_for_tape(self):
        from src.integrations.vision.recognition.equipment_classifier import classify_item

        processor = self._processor(
            {"shape_id": "H_2", "confidence": 0.76},
            ocr_texts=["森林萤火之心"],
        )
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        regions = {
            "drive_shape_icon": (0, 0, 20, 20),
            "identity_check": (20, 0, 40, 20),
        }

        item_type, _profile, _regions, shape_res, hub_text = classify_item(
            processor,
            img,
            [("top_16_9", regions)],
        )

        self.assertEqual("tape", item_type)
        self.assertFalse(shape_res.get("identity_skipped", False))
        self.assertEqual("森林萤火之心", hub_text)
        self.assertEqual(1, processor.ocr_engine.calls)

if __name__ == "__main__":
    unittest.main()
