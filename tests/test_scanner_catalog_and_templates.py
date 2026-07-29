# 测试截图解析和重复过滤辅助逻辑。
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from src.optimizer.scoring import ScoringEngine
from src.scanner.shape_recognizer import ShapeRecognizer




class ScoringEngineTests(unittest.TestCase):
    def test_flexible_weight_prefers_exact_stat_name_before_alias(self):
        engine = ScoringEngine(config_dir="config")

        self.assertEqual(1.0, engine.flexible_weight("\u4f24\u5bb3%", {"\u4f24\u5bb3%": 1.0}))


class StatCatalogTests(unittest.TestCase):
    def test_reads_extended_stats_schema(self):
        from src.domain.stat_catalog import StatCatalog

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stats.json").write_text(
                json.dumps(
                    {
                        "gold_base_values": {"\u4f24\u5bb3\u589e\u52a0%": 1.0},
                        "tape_main_stats_pool": ["\u5149\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u589e\u5f3a"],
                        "tape_main_stat_values": {"\u5149\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u589e\u5f3a%": 37.5},
                        "tape_stat_values": {"\u4f24\u5bb3\u589e\u52a0%": 10.0},
                        "benefit_one": {"\u5143\u7d20\u4f24\u5bb3%": 1.25},
                        "benefit_alias_mapping": {
                            "\u5149\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u589e\u5f3a%": "\u5143\u7d20\u4f24\u5bb3%"
                        },
                        "weight_pool": ["\u5149\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u589e\u5f3a%"],
                        "stat_alias_mapping": {"\u4f24\u5bb3%": "\u4f24\u5bb3\u589e\u52a0%"},
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            catalog = StatCatalog.from_config_dir(root)

        self.assertEqual({"\u4f24\u5bb3\u589e\u52a0%": 10.0}, catalog.tape_stat_values)
        self.assertEqual({"\u5143\u7d20\u4f24\u5bb3%": 1.25}, catalog.benefit_one)
        self.assertEqual(
            {"\u5149\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u589e\u5f3a%": "\u5143\u7d20\u4f24\u5bb3%"},
            catalog.benefit_alias_mapping,
        )

    def test_weight_choice_pool_prefers_configured_pool(self):
        from src.domain.stat_catalog import StatCatalog

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stats.json").write_text(
                json.dumps(
                    {
                        "gold_base_values": {"\u653b\u51fb\u529b": 8.0},
                        "tape_main_stat_values": {"\u6cbb\u7597\u52a0\u6210": 34.5},
                        "weight_pool": ["\u6cbb\u7597\u52a0\u6210"],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            pool = StatCatalog.from_config_dir(root).weight_choice_pool()

        self.assertEqual(["\u6cbb\u7597\u52a0\u6210"], pool)

    def test_legacy_damage_percent_normalizes_to_damage_increase(self):
        from src.domain.stat_catalog import StatCatalog

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "stats.json").write_text(
                json.dumps(
                    {
                        "gold_base_values": {"\u4f24\u5bb3\u589e\u52a0%": 1.0},
                        "stat_alias_mapping": {
                            "\u4f24\u5bb3%": "\u4f24\u5bb3\u589e\u52a0%",
                            "\u4f24\u5bb3": "\u4f24\u5bb3\u589e\u52a0%",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            catalog = StatCatalog.from_config_dir(root)

        self.assertEqual("\u4f24\u5bb3\u589e\u52a0%", catalog.normalize_stat_name("\u4f24\u5bb3%", False))
        self.assertEqual("\u4f24\u5bb3\u589e\u52a0%", catalog.normalize_stat_name("\u4f24\u5bb3", True))

    def test_weight_choice_pool_includes_tape_main_damage_stats(self):
        from src.domain.stat_catalog import StatCatalog

        pool = StatCatalog.from_config_dir("config").weight_choice_pool()
        catalog = StatCatalog.from_config_dir("config")

        self.assertIn("\u653b\u51fb\u529b", pool)
        self.assertIn("\u5149\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u589e\u5f3a%", pool)
        self.assertIn("\u4f24\u5bb3\u589e\u52a0%", pool)
        self.assertIn("\u751f\u547d\u503c", pool)
        self.assertIn("\u9632\u5fa1\u529b", pool)
        self.assertIn("\u4f24\u5bb3\u589e\u52a0%", catalog.valid_sub_stats)


class DroneTemplateTests(unittest.TestCase):
    def test_new_tag_template_loader_handles_paths_cv2_imread_cannot_read(self):
        from src.scanner import drone_scanner

        with tempfile.TemporaryDirectory(prefix="nte_template_") as tmp:
            path = Path(tmp) / "new_tag.png"
            ok, encoded = cv2.imencode(".png", np.full((4, 6), 255, dtype=np.uint8))
            self.assertTrue(ok)
            encoded.tofile(str(path))

            original_imread = drone_scanner.cv2.imread
            drone_scanner.cv2.imread = lambda *_args, **_kwargs: None
            try:
                loaded = drone_scanner.load_new_tag_template(path)
            finally:
                drone_scanner.cv2.imread = original_imread

        self.assertIsNotNone(loaded)
        self.assertEqual((4, 6), loaded.shape)


class GameContentRectTests(unittest.TestCase):
    def test_game_content_rect_supports_standard_and_top_aligned_tall_clients(self):
        from src.scanner.config import ScannerConfig
        from src.scanner.window_capture import game_content_rect

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
    def test_missing_canonical_template_is_reported_before_parsing(self):
        recognizer = ShapeRecognizer.__new__(ShapeRecognizer)
        recognizer.valid_shape_ids = {"H_2", "V_2"}
        recognizer.templates = {"H_2": object()}

        with self.assertRaisesRegex(RuntimeError, "V_2"):
            recognizer.require_complete_templates()

    def test_complete_canonical_templates_are_accepted(self):
        recognizer = ShapeRecognizer.__new__(ShapeRecognizer)
        recognizer.valid_shape_ids = {"H_2", "V_2"}
        recognizer.templates = {"H_2": object(), "V_2": object()}

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
        from src.integrations.vision.equipment_classifier import classify_item

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
        from src.integrations.vision.equipment_classifier import classify_item

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
