# 测试截图解析和重复过滤辅助逻辑。
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from src.features.scanning.file_lifecycle import ScanFileLifecycle
from src.features.inventory_import import duplicate_filter
from src.features.identification import parser as identify_parser
from src.optimizer.scoring import ScoringEngine
from src.scanner.parser import DriveDataParser


class _FakeItem:
    item_type = "tape"
    set_name = "森林萤火之心"
    main_stats = "攻击力%"
    sub_stats = {"攻击力": 10.0}

    def model_dump(self):
        return {"uid": "x"}


class _FakeProcessor:
    def __init__(self):
        self.inventory = []
        self.successful_image_paths = []
        self._last_parsed_filename = None
        self._last_parsed_signature = None
        self._last_parsed_image_fingerprint = None
        self.parser = type("Parser", (), {"GOLD_BASE_VALUES": {"攻击力": 1.25}})()

    def _process_single_image(self, image_path):
        return _FakeItem()

    def _item_signature(self, item_data):
        return "same-signature"

    def _load_existing_inventory_signatures(self):
        return {"same-signature"}

    def _is_inventory_probe_filename(self, filename):
        return filename.startswith("raw_drive_probe_")

    def _mark_image_success(self, image_path):
        self.successful_image_paths.append(image_path)


class _InvalidTapeItem:
    item_type = "tape"
    quality = "Gold"
    area = 15
    sub_stats = {}
    role_scores = {}
    max_score = 0.0
    shape_id = "TAPE_15"
    set_name = "未知套装"
    main_stats = "未知主词条"

    def model_dump(self):
        return {
            "uid": "tape_bad",
            "item_type": self.item_type,
            "quality": self.quality,
            "area": self.area,
            "sub_stats": self.sub_stats,
            "shape_id": self.shape_id,
            "set_name": self.set_name,
            "main_stats": self.main_stats,
        }


class _InvalidNoiseTapeItem(_InvalidTapeItem):
    sub_stats = {"内核占用": 54.8}


class _InvalidParseProcessor(_FakeProcessor):
    def _process_single_image(self, image_path):
        return _InvalidTapeItem()


class _InvalidNoiseParseProcessor(_FakeProcessor):
    def __init__(self):
        super().__init__()
        self.parser = type("Parser", (), {"GOLD_BASE_VALUES": {"暴击率%": 1.0}})()

    def _process_single_image(self, image_path):
        return _InvalidNoiseTapeItem()


class DuplicateFilterTests(unittest.TestCase):
    def test_probe_matching_existing_inventory_is_not_added(self):
        original_fingerprint = duplicate_filter.image_fingerprint
        duplicate_filter.image_fingerprint = lambda _path: "fingerprint"
        try:
            processor = _FakeProcessor()
            _item, added = duplicate_filter.process_image_file(
                processor,
                "raw_drive_probe_0001.png",
                "raw_drive_probe_0001.png",
            )
        finally:
            duplicate_filter.image_fingerprint = original_fingerprint

        self.assertFalse(added)
        self.assertEqual([], processor.inventory)
        self.assertEqual(["raw_drive_probe_0001.png"], processor.successful_image_paths)

    def test_placeholder_tape_without_ocr_data_is_parse_failure(self):
        processor = _InvalidParseProcessor()

        original_fingerprint = duplicate_filter.image_fingerprint
        duplicate_filter.image_fingerprint = lambda _path: "fingerprint"
        try:
            with self.assertRaises(ValueError):
                duplicate_filter.process_image_file(processor, "desktop.png", "raw_drive_probe_0001.png")
        finally:
            duplicate_filter.image_fingerprint = original_fingerprint

        self.assertEqual([], processor.inventory)
        self.assertEqual([], processor.successful_image_paths)

    def test_placeholder_tape_with_only_invalid_sub_stat_is_parse_failure(self):
        processor = _InvalidNoiseParseProcessor()

        original_fingerprint = duplicate_filter.image_fingerprint
        duplicate_filter.image_fingerprint = lambda _path: "fingerprint"
        try:
            with self.assertRaises(ValueError):
                duplicate_filter.process_image_file(processor, "desktop.png", "raw_drive_probe_0001.png")
        finally:
            duplicate_filter.image_fingerprint = original_fingerprint

        self.assertEqual([], processor.inventory)
        self.assertEqual([], processor.successful_image_paths)


class _FakeIdentifyItem:
    def __init__(self, *stats):
        self.sub_stats = {stat: 1 for stat in stats}


class IdentifyParserTests(unittest.TestCase):
    def test_valid_identify_item_rejects_current_bad_keyword(self):
        item = _FakeIdentifyItem("攻击力增加", "最多提高")
        self.assertFalse(identify_parser.is_valid_identify_item(item))

    def test_identify_stat_candidate_rejects_current_bad_keyword(self):
        self.assertFalse(identify_parser.is_identify_stat_candidate("装配一个驱动时增加 10%"))

    def test_identify_stat_texts_keeps_flat_stats_when_type_forced(self):
        lines = [
            {"text": "\u653b\u51fb\u529b 48", "box": (0, 0, 10, 10)},
            {"text": "\u751f\u547d\u503c 100", "box": (0, 12, 10, 22)},
            {"text": "\u66b4\u51fb\u7387 2.4%", "box": (0, 24, 10, 34)},
        ]

        texts = identify_parser.identify_stat_texts(lines, forced_type="drive")

        self.assertIn("\u653b\u51fb\u529b 48", texts)
        self.assertIn("\u751f\u547d\u503c 100", texts)
        self.assertIn("\u66b4\u51fb\u7387 2.4%", texts)

    def test_identify_clusters_include_flat_stat_lines(self):
        lines = [
            {"text": "\u653b\u51fb\u529b 48", "box": (10, 10, 90, 28)},
            {"text": "\u751f\u547d\u503c 100", "box": (12, 38, 92, 56)},
            {"text": "\u66b4\u51fb\u7387 2.4%", "box": (11, 66, 91, 84)},
        ]

        clusters = identify_parser.cluster_identify_lines(lines, (200, 200))

        self.assertEqual(1, len(clusters))
        self.assertEqual(
            ["\u653b\u51fb\u529b 48", "\u751f\u547d\u503c 100", "\u66b4\u51fb\u7387 2.4%"],
            [line["text"] for line in clusters[0]],
        )


class StatParserTests(unittest.TestCase):
    def test_clean_stats_discards_unknown_ocr_noise(self):
        parser = DriveDataParser()

        self.assertEqual({}, parser._clean_stats(["内核占用54.8"]))

    def test_clean_stats_fuzzy_matches_common_ocr_typo(self):
        parser = DriveDataParser()

        self.assertEqual({"暴击率%": 10.0}, parser._clean_stats(["爆击率10%"]))


    def test_clean_stats_keeps_multiple_ocr_lines_with_separators(self):
        parser = DriveDataParser(config_dir="config")

        parsed = parser._clean_stats(["暴击率 2.4%", "攻击力 48", "暴击伤害+4.8%"])

        self.assertEqual(2.4, parsed["暴击率%"])
        self.assertEqual(48.0, parsed["攻击力"])
        self.assertEqual(4.8, parsed["暴击伤害%"])

    def test_clean_stats_keeps_damage_percent_alias(self):
        parser = DriveDataParser(config_dir="config")

        parsed = parser._clean_stats(["\u4f24\u5bb3 1.0%", "\u4f24\u5bb3\u589e\u52a0 1.0%"])

        self.assertEqual(1.0, parsed["\u4f24\u5bb3\u589e\u52a0%"])


class ScoringEngineTests(unittest.TestCase):
    def test_flexible_weight_prefers_exact_stat_name_before_alias(self):
        engine = ScoringEngine(config_dir="config")

        self.assertEqual(1.0, engine._get_flexible_weight("\u4f24\u5bb3%", {"\u4f24\u5bb3%": 1.0}))


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


class IncrementalBaselineTests(unittest.TestCase):
    def test_corrupt_raw_drive_0001_marks_incremental_baseline_unusable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            screenshot_dir = root / "scanned_images"
            screenshot_dir.mkdir()
            (screenshot_dir / "raw_drive_probe_0001.png").write_bytes(b"not an image")
            (screenshot_dir / "raw_drive_0001.png").write_bytes(b"not an image")

            lifecycle = ScanFileLifecycle(
                screenshot_dir=screenshot_dir,
                output_file=root / "config" / "real_inventory.json",
                config_dir=root / "config",
            )
            result = lifecycle.prepare_incremental_parse("incremental_auto")

        self.assertTrue(result.baseline_missing)

    def test_failed_incremental_probe_does_not_replace_raw_drive_0001(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            screenshot_dir = root / "scanned_images"
            screenshot_dir.mkdir()
            baseline = screenshot_dir / "raw_drive_0001.png"
            probe = screenshot_dir / "raw_drive_probe_0001.png"
            baseline.write_bytes(b"baseline")
            probe.write_bytes(b"probe")

            lifecycle = ScanFileLifecycle(
                screenshot_dir=screenshot_dir,
                output_file=root / "config" / "real_inventory.json",
                config_dir=root / "config",
            )
            post = lifecycle.postprocess_vision_files(
                {
                    "parse_scope": "incremental_auto",
                    "added_paths": [],
                    "duplicate_paths": [],
                    "failed_paths": [str(probe)],
                }
            )

            self.assertTrue(baseline.exists())
            self.assertEqual(b"baseline", baseline.read_bytes())
            self.assertFalse(probe.exists())
            self.assertTrue((screenshot_dir / "failed" / "raw_drive_probe_0001.png").exists())
            self.assertEqual(1, post["moved_failed"])
            self.assertEqual(0, post["renamed"])


class GamepadScannerTests(unittest.TestCase):
    def test_capture_panel_does_not_save_when_screen_never_changes(self):
        from src.scanner import gamepad_controller

        class FakeScreenshot:
            size = (4, 4)
            rgb = b"\x00" * 4 * 4 * 3

            def __array__(self, dtype=None):
                arr = np.zeros((4, 4, 4), dtype=np.uint8)
                return arr.astype(dtype) if dtype is not None else arr

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        scanner.capture_dir = "unused"
        scanner._last_capture_fingerprint = np.zeros((54, 96), dtype=np.uint8)

        original_capture = gamepad_controller.capture_foreground_window
        original_to_png = gamepad_controller.mss.tools.to_png
        writes = []
        gamepad_controller.capture_foreground_window = lambda _sct: (FakeScreenshot(), None)
        gamepad_controller.mss.tools.to_png = lambda *_args, **_kwargs: writes.append(True)
        try:
            captured = scanner.capture_panel(object(), 1)
        finally:
            gamepad_controller.capture_foreground_window = original_capture
            gamepad_controller.mss.tools.to_png = original_to_png

        self.assertFalse(captured)
        self.assertEqual([], writes)

    def test_start_scan_retries_move_when_capture_is_stale_then_continues(self):
        from src.scanner import gamepad_controller

        class FakeScreenshot:
            size = (4, 4)
            rgb = b"\x00" * 4 * 4 * 3

            def __init__(self, value):
                self.value = value

            def __array__(self, dtype=None):
                arr = np.full((4, 4, 4), self.value, dtype=np.uint8)
                return arr.astype(dtype) if dtype is not None else arr

        class FakeMSS:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        scanner.output_dir = "unused"
        scanner.capture_dir = "unused"
        scanner._stopped = False
        scanner.cols = 7
        scanner._last_capture_fingerprint = None
        moves = []
        commits = []
        scanner.push_left_joystick = lambda x, y: moves.append((x, y))
        scanner._prepare_temp_output = lambda: None
        scanner._commit_temp_output = lambda: commits.append(True)

        frames = [FakeScreenshot(1)] + [FakeScreenshot(1)] * scanner.CAPTURE_CHANGE_ATTEMPTS + [FakeScreenshot(10)]

        original_capture = gamepad_controller.capture_foreground_window
        original_to_png = gamepad_controller.mss.tools.to_png
        original_mss = gamepad_controller.mss.MSS
        original_sleep = gamepad_controller.time.sleep
        writes = []
        gamepad_controller.capture_foreground_window = lambda _sct: (frames.pop(0), None)
        gamepad_controller.mss.tools.to_png = lambda *_args, **_kwargs: writes.append(True)
        gamepad_controller.mss.MSS = FakeMSS
        gamepad_controller.time.sleep = lambda *_args, **_kwargs: None
        try:
            count = scanner.start_scan(2)
        finally:
            gamepad_controller.capture_foreground_window = original_capture
            gamepad_controller.mss.tools.to_png = original_to_png
            gamepad_controller.mss.MSS = original_mss
            gamepad_controller.time.sleep = original_sleep

        right_moves = [move for move in moves if move == (1.0, 0.0)]
        self.assertEqual(2, count)
        self.assertEqual(2, len(writes))
        self.assertEqual(2, len(right_moves))
        self.assertEqual([True], commits)


if __name__ == "__main__":
    unittest.main()
