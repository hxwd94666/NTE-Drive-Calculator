# 验证核心测试并行分片保持模块完整、负载均衡且不依赖文件白名单。
"""Tests for the maintenance test runner."""

from __future__ import annotations

from unittest import TestCase

from tools.quality.run_tests import balanced_module_shards


class _AlphaCase:
    pass


class _BetaCase:
    pass


_BetaCase.__module__ = "test_beta_module"


class QualityTestRunnerTests(TestCase):
    def test_balancing_keeps_each_module_in_one_shard(self) -> None:
        cases = [_AlphaCase(), _AlphaCase(), _BetaCase()]

        shards = balanced_module_shards(cases, 2)

        flattened = [module for shard in shards for module in shard]
        current_module = __name__ if "." in __name__ else f"tests.{__name__}"
        self.assertEqual(len(flattened), len(set(flattened)))
        self.assertIn(current_module, flattened)
        self.assertIn("tests.test_beta_module", flattened)

    def test_jobs_are_capped_by_discovered_module_count(self) -> None:
        shards = balanced_module_shards([_AlphaCase()], 8)

        current_module = __name__ if "." in __name__ else f"tests.{__name__}"
        self.assertEqual(shards, [[current_module]])
