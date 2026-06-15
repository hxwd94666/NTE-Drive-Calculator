# 测试截图解析和重复过滤辅助逻辑。
import unittest

from src.features.inventory_import import duplicate_filter
from src.features.identification import parser as identify_parser


class _FakeItem:
    def model_dump(self):
        return {"uid": "x"}


class _FakeProcessor:
    def __init__(self):
        self.inventory = []
        self.successful_image_paths = []
        self._last_parsed_filename = None
        self._last_parsed_signature = None
        self._last_parsed_image_fingerprint = None

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


class _FakeIdentifyItem:
    def __init__(self, *stats):
        self.sub_stats = {stat: 1 for stat in stats}


class IdentifyParserTests(unittest.TestCase):
    def test_valid_identify_item_rejects_current_bad_keyword(self):
        item = _FakeIdentifyItem("攻击力增加", "最多提高")
        self.assertFalse(identify_parser.is_valid_identify_item(item))

    def test_identify_stat_candidate_rejects_current_bad_keyword(self):
        self.assertFalse(identify_parser.is_identify_stat_candidate("装配一个驱动时增加 10%"))


if __name__ == "__main__":
    unittest.main()
