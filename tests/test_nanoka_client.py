# nanoka 静态数据客户端测试。
from __future__ import annotations

import unittest
from unittest import mock

from src.features.settings.nanoka_client import (
    detect_live_version,
    merge_level_sub_stats,
    parse_version_tuple,
    resolve_version,
)


class NanokaClientTests(unittest.TestCase):
    def test_parse_version_tuple(self):
        self.assertEqual(parse_version_tuple("1.2"), (1, 2))
        self.assertEqual(parse_version_tuple("1.2.14"), (1, 2, 14))

    def test_detect_live_version_from_embedded_static_urls(self):
        html = """
        <script data-url="https://static.nanoka.cc/nte/1.3/character.json"></script>
        <script data-url="https://static.nanoka.cc/nte/1.3/weapon.json"></script>
        <script data-url="https://static.nanoka.cc/nte/1.2.14/character.json"></script>
        """
        with mock.patch(
            "src.features.settings.nanoka_client.request_text",
            return_value=html,
        ):
            self.assertEqual(detect_live_version(), "1.3")

    def test_resolve_version_latest(self):
        with mock.patch(
            "src.features.settings.nanoka_client.detect_live_version",
            return_value="2.0",
        ):
            self.assertEqual(resolve_version("latest"), "2.0")
            self.assertEqual(resolve_version(None), "2.0")

    def test_merge_removes_stale_managed_stats(self):
        entity = {
            "level": 80,
            "level_sub_stats": {
                "80": {
                    "攻击力白值": 512.0,
                    "攻击力%": 33.0,
                    "充能效率%": 33.0,
                }
            },
            "sub_stats": {
                "攻击力白值": 512.0,
                "攻击力%": 33.0,
                "充能效率%": 33.0,
                "自定义": 1.0,
            },
        }
        changed, diffs = merge_level_sub_stats(
            entity,
            {"80": {"攻击力白值": 512.0, "充能效率%": 33.0}},
            managed_keys=("攻击力白值", "攻击力%", "充能效率%"),
        )

        self.assertTrue(changed)
        self.assertNotIn("攻击力%", entity["level_sub_stats"]["80"])
        self.assertNotIn("攻击力%", entity["sub_stats"])
        self.assertEqual(entity["sub_stats"]["自定义"], 1.0)
        self.assertIn(
            {"level": "80", "stat": "攻击力%", "local": 33.0, "remote": None},
            diffs,
        )

    def test_missing_zero_value_is_not_treated_as_equal(self):
        entity = {"level": 1, "level_sub_stats": {"1": {}}, "sub_stats": {}}
        changed, _ = merge_level_sub_stats(
            entity,
            {"1": {"暴击率%": 0.0}},
            managed_keys=("暴击率%",),
        )
        self.assertTrue(changed)
        self.assertEqual(entity["sub_stats"]["暴击率%"], 0.0)


if __name__ == "__main__":
    unittest.main()
