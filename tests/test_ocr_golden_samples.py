# 验证多分辨率与常见 DPI 的隐私安全 OCR 布局黄金样本。
from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.integrations.vision.identification_parser import (
    cluster_identify_lines,
    identify_stat_texts,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ocr_layout_golden.json"


class OcrGoldenSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_fixture_covers_required_display_profiles(self):
        cases = self.fixture["cases"]
        resolutions = {tuple(case["resolution"]) for case in cases}
        dpi_scales = {float(case["dpi_scale"]) for case in cases}

        self.assertIn((1920, 1080), resolutions)
        self.assertIn((2560, 1440), resolutions)
        self.assertIn((3840, 2160), resolutions)
        self.assertIn((2560, 1600), resolutions)
        self.assertTrue({1.0, 1.25, 1.5}.issubset(dpi_scales))

    def test_normalized_structures_match_golden_expectations(self):
        for case in self.fixture["cases"]:
            with self.subTest(case=case["name"]):
                lines = [{"text": line["text"], "box": tuple(line["box"])} for line in case["lines"]]
                stat_texts = identify_stat_texts(
                    lines,
                    forced_type=case["forced_type"],
                )
                clusters = cluster_identify_lines(
                    lines,
                    tuple(reversed(case["resolution"])),
                )

                self.assertEqual(case["expected_stat_texts"], stat_texts)
                self.assertEqual(case["expected_cluster_count"], len(clusters))


if __name__ == "__main__":
    unittest.main()
