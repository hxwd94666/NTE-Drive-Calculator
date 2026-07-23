# 防止词条数值库被误当作旧配置删除。
"""Regression coverage for the editable OCR/scoring stat catalog."""

from __future__ import annotations

import unittest
from pathlib import Path

from src.app.constants import CORE_CONFIG_FILES
from src.domain.stat_catalog import StatCatalog


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATS_PATH = PROJECT_ROOT / "config" / "stats.json"


class StatsCatalogTests(unittest.TestCase):
    def test_stats_json_is_a_required_editable_catalog(self):
        """OCR、鉴定和旧评分仍依赖该可编辑词条数值库。"""

        self.assertTrue(STATS_PATH.is_file(), "config/stats.json 不得删除")
        self.assertIn("stats.json", CORE_CONFIG_FILES)

        catalog = StatCatalog.from_config_dir(STATS_PATH.parent)
        self.assertTrue(catalog.gold_base_values)
        self.assertTrue(catalog.tape_main_values)
        self.assertTrue(catalog.tape_main_stats)
        # Weight editing and graduation templates share this strict equipment
        # sub-stat set; element/healing main stats must not leak into it.
        self.assertEqual(
            set(catalog.gold_base_values), set(catalog.tape_sub_stat_pool())
        )
