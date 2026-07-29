# 测试截图解析和重复过滤辅助逻辑。
import unittest
from types import SimpleNamespace

import numpy as np

from src.integrations.vision import duplicate_filter
from src.integrations.vision import identification_parser as identify_parser
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


class _PartialDriveItem:
    item_type = "drive"
    quality = "Gold"
    area = 2
    shape_id = "H_2"
    set_name = "未知套装"
    main_stats = {"攻击力": 42.0, "生命值": 560.0}
    sub_stats = {"攻击力%": 2.5, "暴击率%": 2.0, "攻击力": 16.0}

    def model_dump(self):
        return {
            "uid": "partial_drive",
            "item_type": self.item_type,
            "quality": self.quality,
            "area": self.area,
            "shape_id": self.shape_id,
            "set_name": self.set_name,
            "main_stats": self.main_stats,
            "sub_stats": self.sub_stats,
        }


class _PartialDriveParseProcessor(_FakeProcessor):
    def __init__(self):
        super().__init__()
        self.parser = type("Parser", (), {"GOLD_BASE_VALUES": dict(_PartialDriveItem.sub_stats)})()

    def _process_single_image(self, image_path):
        return _PartialDriveItem()


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

    def test_adjacent_duplicate_filter_can_be_disabled_for_full_parse(self):
        original_fingerprint = duplicate_filter.image_fingerprint
        duplicate_filter.image_fingerprint = lambda _path: np.zeros((4, 4), dtype=np.uint8)
        try:
            processor = _FakeProcessor()
            _item, first_added = duplicate_filter.process_image_file(
                processor,
                "raw_drive_0001.png",
                "raw_drive_0001.png",
                filter_adjacent_duplicates=False,
            )
            _item, second_added = duplicate_filter.process_image_file(
                processor,
                "raw_drive_0002.png",
                "raw_drive_0002.png",
                filter_adjacent_duplicates=False,
            )
        finally:
            duplicate_filter.image_fingerprint = original_fingerprint

        self.assertTrue(first_added)
        self.assertTrue(second_added)
        self.assertEqual(2, len(processor.inventory))

    def test_adjacent_duplicate_filter_still_blocks_incremental_duplicates(self):
        original_fingerprint = duplicate_filter.image_fingerprint
        duplicate_filter.image_fingerprint = lambda _path: np.zeros((4, 4), dtype=np.uint8)
        try:
            processor = _FakeProcessor()
            _item, first_added = duplicate_filter.process_image_file(
                processor,
                "raw_drive_new_0001.png",
                "raw_drive_new_0001.png",
            )
            _item, second_added = duplicate_filter.process_image_file(
                processor,
                "raw_drive_new_0002.png",
                "raw_drive_new_0002.png",
            )
        finally:
            duplicate_filter.image_fingerprint = original_fingerprint

        self.assertTrue(first_added)
        self.assertFalse(second_added)
        self.assertEqual(1, len(processor.inventory))

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

    def test_three_valid_sub_stats_become_recoverable_parse_error(self):
        processor = _PartialDriveParseProcessor()

        with self.assertRaises(duplicate_filter.RecoverableParseError) as ctx:
            duplicate_filter.process_image_file(processor, "desktop.png", "raw_drive_0001.png")

        record = ctx.exception.to_record("desktop.png", "raw_drive_0001.png")
        self.assertEqual(3, record["recognized_count"])
        self.assertEqual(1, record["missing_count"])
        self.assertEqual("drive", record["item_type"])
        self.assertEqual([], processor.inventory)


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

        set_name, main_stat = identify_parser.detect_tape_identity_from_lines(
            processor,
            lines,
        )

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

    def test_clean_stats_accepts_fullwidth_percent_and_decimal_ocr_noise(self):
        parser = DriveDataParser(config_dir="config")

        parsed = parser._clean_stats(["攻击力提升2，50％", "暴击伤害提升4·80%"])

        self.assertEqual(2.5, parsed["攻击力%"])
        self.assertEqual(4.8, parsed["暴击伤害%"])

    def test_attribute_tape_main_requires_clear_attribute(self):
        parser = DriveDataParser(config_dir="config")

        self.assertEqual("咒属性异能伤害增强", parser._fuzzy_match_tape_main("咒属性异能伤害增强"))
        self.assertEqual("未知主词条", parser._fuzzy_match_tape_main("属性异能伤害增强"))

    def test_attribute_sub_stat_does_not_fuzzy_guess_missing_attribute(self):
        parser = DriveDataParser(config_dir="config")

        self.assertEqual({}, parser._clean_stats(["属性异能伤害增强 37.5%"]))

    def test_fuzzy_match_set_name_ignores_surrounding_ui_text(self):
        parser = DriveDataParser(config_dir="config")

        self.assertEqual(
            "\u68ee\u6797\u8424\u706b\u4e4b\u5fc3",
            parser._fuzzy_match_set_name("\u6536\u8d77\u63a8\u300c\u68ee\u6797\u8424\u706b\u4e4b\u5fc3\u300d+20"),
        )


class RewardSceneParserTests(unittest.TestCase):
    def test_reward_drive_scene_synthesizes_selected_drive(self):
        from src.integrations.vision import screenshot_parser

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
        from src.integrations.vision import screenshot_parser

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

if __name__ == "__main__":
    unittest.main()
