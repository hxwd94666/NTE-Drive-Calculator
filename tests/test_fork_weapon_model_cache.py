# 固化弧盘模板投影按静态数据集身份缓存。
"""Guard the fork weapon-model memoization.

弧盘投影是纯 CPU 计算（51 个弧盘逐级的面板统计，实测一次约 0.95 s），且只随静态
数据集发布变化。缓存后第二次及以后的重建不再重复这笔开销；数据集变化或显式清理
后必须重新计算。
"""

from __future__ import annotations

import unittest
from typing import Any

from src.services.role_fork_template_service import (
    clear_weapon_model_cache,
    fork_templates_as_weapon_models,
)


def _payload(dataset_id: str) -> dict[str, Any]:
    return {
        "static_dataset": {
            "dataset_id": dataset_id,
            "importer_version": "1.0",
            "built_at_utc": "2026-09-28T00:00:00.000Z",
        },
        "forks": [
            {
                "name_zh": "测试弧盘",
                "fork_id": "fork-1",
                "fork_type_name_zh": "测试类型",
                "max_breakthrough": 0,
                "max_star": 0,
                "upgrade_levels": [],
                "star_levels": [],
            }
        ],
    }


class ForkWeaponModelCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_weapon_model_cache()

    def tearDown(self) -> None:
        clear_weapon_model_cache()

    def test_same_dataset_reuses_the_projection(self) -> None:
        """同一数据集的第二次调用必须命中缓存。"""

        first = fork_templates_as_weapon_models(_payload("dataset-a"))
        second = fork_templates_as_weapon_models(_payload("dataset-a"))
        self.assertIs(first, second)

    def test_new_dataset_rebuilds_with_the_same_content(self) -> None:
        """数据集变化后必须重算，且内容一致。"""

        first = fork_templates_as_weapon_models(_payload("dataset-a"))
        second = fork_templates_as_weapon_models(_payload("dataset-b"))
        self.assertIsNot(first, second)
        self.assertEqual(first, second)

    def test_clear_forces_a_rebuild(self) -> None:
        first = fork_templates_as_weapon_models(_payload("dataset-a"))
        clear_weapon_model_cache()
        second = fork_templates_as_weapon_models(_payload("dataset-a"))
        self.assertIsNot(first, second)


if __name__ == "__main__":
    unittest.main()
