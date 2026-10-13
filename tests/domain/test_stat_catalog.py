# 验证词条目录配置、历史别名与可选权重池的领域规则。
import json
import tempfile
import unittest
from pathlib import Path


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

