# 测试截图解析和重复过滤辅助逻辑。
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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


_FakeItem.sub_stats = {
    "鏀诲嚮鍔?": 10.0,
    "鐢熷懡鍊?": 100.0,
    "鏆村嚮鐜?": 2.0,
    "鏆村嚮浼ゅ%": 4.0,
}


class _FakeProcessor:
    def __init__(self):
        self.inventory = []
        self.successful_image_paths = []
        self._last_parsed_filename = None
        self._last_parsed_signature = None
        self._last_parsed_image_fingerprint = None
        self.parser = type("Parser", (), {"GOLD_BASE_VALUES": {"攻击力": 1.25}})()

        self.parser.GOLD_BASE_VALUES = dict(_FakeItem.sub_stats)

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

    def test_equipment_with_fewer_than_four_sub_stats_is_parse_failure(self):
        item = SimpleNamespace(
            item_type="drive",
            sub_stats={
                "\u653b\u51fb\u529b": 10.0,
                "\u751f\u547d\u503c": 100.0,
                "\u66b4\u51fb\u7387%": 2.4,
            },
        )

        self.assertFalse(duplicate_filter.has_meaningful_parse_data(item, item.sub_stats.keys()))


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

    def test_forced_tape_identify_can_auto_read_set_and_main_stat(self):
        import numpy as np

        class FakeOCR:
            def __init__(self):
                self.calls = 0

            def extract_text(self, _crop):
                self.calls += 1
                if self.calls == 1:
                    return ["\u5947\u70b9\u5957\u88c5"]
                if self.calls == 2:
                    return ["\u653b\u51fb\u529b%"]
                return ["\u66b4\u51fb\u7387 2.4%", "\u653b\u51fb\u529b 48"]

        class FakeParser:
            def _fuzzy_match_set_name(self, text):
                return "\u5947\u70b9\u5957\u88c5" if "\u5947\u70b9" in text else "\u672a\u77e5\u5957\u88c5"

            def synthesize_tape(self, set_name, main_texts, raw_sub_texts):
                return {
                    "set_name": set_name,
                    "main_texts": main_texts,
                    "raw_sub_texts": raw_sub_texts,
                }

        class FakeProcessor:
            ocr_engine = FakeOCR()
            parser = FakeParser()

        original_profiles = identify_parser.ScannerConfig.get_region_profiles
        identify_parser.ScannerConfig.get_region_profiles = classmethod(
            lambda cls, target_width, target_height: [
                (
                    "test",
                    {
                        "identity_check": (0, 0, 10, 10),
                        "tape_main_stat": (10, 0, 20, 10),
                        "tape_sub_stats": (20, 0, 30, 10),
                    },
                )
            ]
        )
        try:
            item = identify_parser.process_identify_standard_forced(
                FakeProcessor(),
                np.zeros((20, 40, 3), dtype=np.uint8),
                forced_type="tape",
            )
        finally:
            identify_parser.ScannerConfig.get_region_profiles = original_profiles

        self.assertEqual("\u5947\u70b9\u5957\u88c5", item["set_name"])
        self.assertEqual(["\u653b\u51fb\u529b%"], item["main_texts"])

    def test_forced_tape_identify_can_read_set_above_main_stat(self):
        import numpy as np

        class FakeOCR:
            def __init__(self):
                self.calls = 0

            def extract_text(self, _crop):
                self.calls += 1
                if self.calls == 1:
                    return ["\u65e0\u5173\u6587\u5b57"]
                if self.calls == 2:
                    return ["\u5947\u70b9\u5957\u88c5"]
                if self.calls == 3:
                    return ["\u751f\u547d\u503c%"]
                return ["\u751f\u547d\u503c 200", "\u9632\u5fa1\u529b 16"]

        class FakeParser:
            def _fuzzy_match_set_name(self, text):
                return "\u5947\u70b9\u5957\u88c5" if "\u5947\u70b9" in text else "\u672a\u77e5\u5957\u88c5"

            def synthesize_tape(self, set_name, main_texts, raw_sub_texts):
                return {
                    "set_name": set_name,
                    "main_texts": main_texts,
                    "raw_sub_texts": raw_sub_texts,
                }

        class FakeProcessor:
            ocr_engine = FakeOCR()
            parser = FakeParser()

        original_profiles = identify_parser.ScannerConfig.get_region_profiles
        identify_parser.ScannerConfig.get_region_profiles = classmethod(
            lambda cls, target_width, target_height: [
                (
                    "test",
                    {
                        "identity_check": (0, 0, 10, 10),
                        "tape_main_stat": (20, 80, 80, 100),
                        "tape_sub_stats": (20, 110, 80, 160),
                    },
                )
            ]
        )
        try:
            item = identify_parser.process_identify_standard_forced(
                FakeProcessor(),
                np.zeros((180, 120, 3), dtype=np.uint8),
                forced_type="tape",
            )
        finally:
            identify_parser.ScannerConfig.get_region_profiles = original_profiles

        self.assertEqual("\u5947\u70b9\u5957\u88c5", item["set_name"])
        self.assertEqual(["\u751f\u547d\u503c%"], item["main_texts"])

    def test_shape_picker_groups_shapes_by_area(self):
        from src.features.identification.dialogs import group_shape_ids_by_area

        grouped = group_shape_ids_by_area(
            {
                "TAPE_15": 15,
                "H_2": 2,
                "V_2": 2,
                "L_3_TL": 3,
                "H_4": 4,
                "Trap_4_V": 4,
            }
        )

        self.assertEqual(["H_2", "V_2"], grouped[2])
        self.assertEqual(["L_3_TL"], grouped[3])
        self.assertEqual(["H_4", "Trap_4_V"], grouped[4])

    def test_tape_identity_defaults_do_not_carry_unforced_main_stat(self):
        from types import SimpleNamespace

        item = SimpleNamespace(item_type="tape", set_name="森林萤火之心", main_stats="攻击力%")

        set_name, main_stat = identify_parser._carry_tape_identity_defaults(item)

        self.assertEqual("森林萤火之心", set_name)
        self.assertIsNone(main_stat)


    def test_tape_identity_defaults_from_reward_full_image_lines(self):
        class FakeParser:
            REAL_SETS_WHITE_LIST = ["\u8fea\u4e9a\u6ce2\u7f57\u65af"]
            TAPE_MAIN_STATS_POOL = [
                "\u5149\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u589e\u5f3a",
                "\u7075\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u589e\u5f3a",
            ]

            def _fuzzy_match_set_name(self, text):
                return self.REAL_SETS_WHITE_LIST[0] if "\u8fea\u4e9a\u6ce2\u7f57\u65af" in text else "\u672a\u77e5\u5957\u88c5"

            def _fuzzy_match_tape_main(self, text):
                return (
                    self.TAPE_MAIN_STATS_POOL[0]
                    if "\u5149\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3" in text
                    else "\u672a\u77e5\u4e3b\u8bcd\u6761"
                )

        processor = SimpleNamespace(parser=FakeParser())
        lines = [
            {"text": "\u300c\u8fea\u4e9a\u6ce2\u7f57\u65af\u300d", "box": (100, 100, 200, 130)},
            {"text": "\u4e3b\u5c5e\u6027", "box": (100, 200, 160, 230)},
            {"text": "\u5149\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u589e\u5f3a", "box": (100, 240, 280, 270)},
            {"text": "7.50%", "box": (300, 240, 360, 270)},
            {"text": "\u53f2\u8bd7\uff01[2]\uff1a\u7075\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u63d0\u534710%", "box": (100, 520, 500, 560)},
        ]

        set_name, main_stat = identify_parser._detect_tape_identity_from_lines(processor, lines)

        self.assertEqual("\u8fea\u4e9a\u6ce2\u7f57\u65af", set_name)
        self.assertEqual("\u5149\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u589e\u5f3a", main_stat)
        self.assertIsNone(
            identify_parser._match_tape_main_line(
                processor,
                "\u66b4\u51fb\u7387\u63d0\u53478.00%",
                allow_fuzzy=True,
            )
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

    def test_fuzzy_match_set_name_ignores_surrounding_ui_text(self):
        parser = DriveDataParser(config_dir="config")

        self.assertEqual(
            "\u68ee\u6797\u8424\u706b\u4e4b\u5fc3",
            parser._fuzzy_match_set_name("\u6536\u8d77\u63a8\u300c\u68ee\u6797\u8424\u706b\u4e4b\u5fc3\u300d+20"),
        )


class RewardSceneParserTests(unittest.TestCase):
    def test_reward_drive_scene_synthesizes_selected_drive(self):
        from src.features.inventory_import import screenshot_parser

        class FakeOCR:
            def extract_lines(self, _img):
                return [
                    {"text": "\u5012\u5e26\u83b7\u5f97", "box": (100, 100, 200, 130)},
                    {"text": "IV\u578b\u9a71\u52a8", "box": (300, 100, 420, 130)},
                    {"text": "\u526f\u5c5e\u6027", "box": (300, 300, 400, 330)},
                    {"text": "\u653b\u51fb\u529b\u63d0\u53473.00%", "box": (300, 350, 500, 380)},
                    {"text": "\u653b\u51fb\u529b\u589e\u52a019", "box": (300, 400, 500, 430)},
                    {"text": "\u73af\u5408\u5f3a\u5ea6\u589e\u52a014", "box": (300, 450, 500, 480)},
                    {"text": "\u66b4\u51fb\u4f24\u5bb3\u63d0\u53474.80%", "box": (300, 500, 500, 530)},
                ]

        processor = SimpleNamespace(
            ocr_engine=FakeOCR(),
            parser=DriveDataParser(config_dir="config"),
            shape_recognizer=object(),
        )
        original = screenshot_parser.locate_selected_reward_shape
        screenshot_parser.locate_selected_reward_shape = lambda *_args, **_kwargs: {
            "shape_id": "Trap_4_H",
            "confidence": 0.75,
        }
        try:
            item = screenshot_parser.process_reward_scene(processor, np.zeros((720, 1280, 3), dtype=np.uint8))
        finally:
            screenshot_parser.locate_selected_reward_shape = original

        self.assertEqual("drive", item.item_type)
        self.assertEqual("Trap_4_H", item.shape_id)
        self.assertEqual(4, len(item.sub_stats))

    def test_reward_tape_scene_synthesizes_set_main_and_four_sub_stats(self):
        from src.features.inventory_import import screenshot_parser

        class FakeOCR:
            def extract_lines(self, _img):
                return [
                    {"text": "\u300c\u8fea\u4e9a\u6ce2\u7f57\u65af\u300d", "box": (300, 100, 450, 130)},
                    {"text": "\u4e3b\u5c5e\u6027", "box": (300, 200, 400, 230)},
                    {"text": "\u5149\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u589e\u5f3a", "box": (300, 250, 600, 280)},
                    {"text": "\u526f\u5c5e\u6027", "box": (300, 320, 400, 350)},
                    {"text": "\u73af\u5408\u5f3a\u5ea6\u589e\u52a060", "box": (300, 370, 500, 400)},
                    {"text": "\u9632\u5fa1\u529b\u589e\u52a080", "box": (300, 420, 500, 450)},
                    {"text": "\u66b4\u51fb\u7387\u63d0\u534710.00%", "box": (300, 470, 500, 500)},
                    {"text": "\u751f\u547d\u503c\u589e\u52a01000", "box": (300, 520, 500, 550)},
                ]

        processor = SimpleNamespace(ocr_engine=FakeOCR(), parser=DriveDataParser(config_dir="config"))

        item = screenshot_parser.process_reward_scene(processor, np.zeros((720, 1280, 3), dtype=np.uint8))

        self.assertEqual("tape", item.item_type)
        self.assertEqual("\u8fea\u4e9a\u6ce2\u7f57\u65af", item.set_name)
        self.assertEqual("\u5149\u5c5e\u6027\u5f02\u80fd\u4f24\u5bb3\u589e\u5f3a", item.main_stats)
        self.assertEqual(4, len(item.sub_stats))


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
        from src.features.inventory_import.equipment_classifier import classify_item

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
        from src.features.inventory_import.equipment_classifier import classify_item

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
    def test_capture_panel_uses_mss_png_writer(self):
        from src.scanner import gamepad_controller

        class FakeScreenshot:
            size = (2, 2)
            rgb = b"\x00" * 2 * 2 * 3

        original_to_png = gamepad_controller.mss.tools.to_png
        calls = []

        def fake_to_png(rgb, size, output):
            calls.append((rgb, size, output))

        gamepad_controller.mss.tools.to_png = fake_to_png
        try:
            gamepad_controller._save_png(FakeScreenshot(), "unused.png")
        finally:
            gamepad_controller.mss.tools.to_png = original_to_png

        self.assertEqual([(FakeScreenshot.rgb, FakeScreenshot.size, "unused.png")], calls)

    def test_push_left_joystick_uses_lenient_timing(self):
        from src.scanner import gamepad_controller

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        updates = []
        sleeps = []
        scanner.gamepad = SimpleNamespace(
            left_joystick_float=lambda **kwargs: updates.append(kwargs),
            update=lambda: updates.append("update"),
        )
        original_sleep = gamepad_controller.time.sleep
        gamepad_controller.time.sleep = lambda seconds, *_args, **_kwargs: sleeps.append(seconds)
        try:
            scanner.push_left_joystick(1.0, 0.0)
        finally:
            gamepad_controller.time.sleep = original_sleep

        self.assertEqual([0.10, 0.25], sleeps)
        self.assertEqual(
            [
                {"x_value_float": 1.0, "y_value_float": 0.0},
                "update",
                {"x_value_float": 0.0, "y_value_float": 0.0},
                "update",
            ],
            updates,
        )

    def test_capture_panel_saves_single_current_frame_without_waiting_for_change(self):
        from src.scanner import gamepad_controller

        class FakeScreenshot:
            size = (4, 4)
            rgb = b"\x00" * 4 * 4 * 3

        scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
        scanner.capture_dir = "unused"

        original_capture = gamepad_controller.capture_foreground_window
        original_save_png = gamepad_controller._save_png
        original_sleep = gamepad_controller.time.sleep
        writes = []
        captures = []
        sleeps = []

        def fake_capture(_sct):
            captures.append(True)
            return FakeScreenshot(), None

        gamepad_controller.capture_foreground_window = fake_capture
        gamepad_controller._save_png = lambda *_args, **_kwargs: writes.append(True)
        gamepad_controller.time.sleep = lambda seconds, *_args, **_kwargs: sleeps.append(seconds)
        try:
            captured = scanner.capture_panel(object(), 1)
        finally:
            gamepad_controller.capture_foreground_window = original_capture
            gamepad_controller._save_png = original_save_png
            gamepad_controller.time.sleep = original_sleep

        self.assertTrue(captured)
        self.assertEqual([True], writes)
        self.assertEqual([True], captures)
        self.assertEqual([], sleeps)

    def test_start_scan_does_not_retry_move_when_capture_is_stale(self):
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
        moves = []
        commits = []
        scanner.push_left_joystick = lambda x, y: moves.append((x, y))
        scanner._prepare_temp_output = lambda: None
        scanner._commit_temp_output = lambda: commits.append(True)

        frames = [FakeScreenshot(1), FakeScreenshot(1)]

        original_capture = gamepad_controller.capture_foreground_window
        original_save_png = gamepad_controller._save_png
        original_mss = gamepad_controller.mss.MSS
        original_sleep = gamepad_controller.time.sleep
        writes = []
        gamepad_controller.capture_foreground_window = lambda _sct: (frames.pop(0), None)
        gamepad_controller._save_png = lambda *_args, **_kwargs: writes.append(True)
        gamepad_controller.mss.MSS = FakeMSS
        gamepad_controller.time.sleep = lambda *_args, **_kwargs: None
        try:
            count = scanner.start_scan(2)
        finally:
            gamepad_controller.capture_foreground_window = original_capture
            gamepad_controller._save_png = original_save_png
            gamepad_controller.mss.MSS = original_mss
            gamepad_controller.time.sleep = original_sleep

        right_moves = [move for move in moves if move == (1.0, 0.0)]
        self.assertEqual(2, count)
        self.assertEqual(2, len(writes))
        self.assertEqual(1, len(right_moves))
        self.assertEqual([True], commits)

    def test_start_scan_can_notify_captures_before_deferred_commit(self):
        from src.scanner import gamepad_controller

        class FakeScreenshot:
            size = (4, 4)
            rgb = b"\x00" * 4 * 4 * 3

        class FakeMSS:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with tempfile.TemporaryDirectory() as tmp:
            scanner = gamepad_controller.GamepadScanner.__new__(gamepad_controller.GamepadScanner)
            scanner.output_dir = tmp
            scanner.capture_dir = tmp
            scanner._stopped = False
            scanner.cols = 7
            scanner.push_left_joystick = lambda *_args, **_kwargs: None

            original_capture = gamepad_controller.capture_foreground_window
            original_save_png = gamepad_controller._save_png
            original_mss = gamepad_controller.mss.MSS
            original_sleep = gamepad_controller.time.sleep
            commits = []
            notifications = []
            gamepad_controller.capture_foreground_window = lambda _sct: (FakeScreenshot(), None)
            gamepad_controller._save_png = lambda _screenshot, filename: Path(filename).write_bytes(b"png")
            gamepad_controller.mss.MSS = FakeMSS
            gamepad_controller.time.sleep = lambda *_args, **_kwargs: None
            scanner._commit_temp_output = lambda: commits.append(True)
            try:
                count = scanner.start_scan(
                    2,
                    on_capture=lambda path, index, total: notifications.append((Path(path).name, index, total)),
                    commit_on_complete=False,
                )
            finally:
                gamepad_controller.capture_foreground_window = original_capture
                gamepad_controller._save_png = original_save_png
                gamepad_controller.mss.MSS = original_mss
                gamepad_controller.time.sleep = original_sleep

        self.assertEqual(2, count)
        self.assertEqual(
            [("raw_drive_0001.png", 1, 2), ("raw_drive_0002.png", 2, 2)],
            notifications,
        )
        self.assertEqual([], commits)


if __name__ == "__main__":
    unittest.main()
