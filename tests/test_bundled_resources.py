# 验证发行配置与素材始终按 Python 包位置解析，不受进程当前目录影响。
"""Tests for immutable bundled-resource path resolution."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from src.integrations.bundled_resources import (
    bundled_config_dir,
    bundled_game_ui_asset_root,
    bundled_root,
)


NTE_TEST_TIER = "core"


class BundledResourcesTests(TestCase):
    def tearDown(self) -> None:
        bundled_root.cache_clear()

    def test_paths_derive_from_src_package_location(self) -> None:
        package_dir = Path("D:/example/application/src")
        spec = SimpleNamespace(submodule_search_locations=[str(package_dir)])

        with patch("src.integrations.bundled_resources.find_spec", return_value=spec):
            bundled_root.cache_clear()
            self.assertEqual(bundled_root(), package_dir.resolve().parent)
            self.assertEqual(bundled_config_dir(), package_dir.resolve().parent / "config")
            self.assertEqual(
                bundled_game_ui_asset_root(),
                package_dir.resolve().parent / "assets" / "game_ui",
            )

    def test_resolution_does_not_depend_on_current_directory(self) -> None:
        package_dir = Path("D:/example/application/src")
        spec = SimpleNamespace(submodule_search_locations=[str(package_dir)])
        original_cwd = Path.cwd()

        try:
            os.chdir(original_cwd.parent)
            with patch("src.integrations.bundled_resources.find_spec", return_value=spec):
                bundled_root.cache_clear()
                self.assertEqual(bundled_root(), package_dir.resolve().parent)
        finally:
            os.chdir(original_cwd)

    def test_missing_package_location_is_rejected(self) -> None:
        with patch("src.integrations.bundled_resources.find_spec", return_value=None):
            bundled_root.cache_clear()
            with self.assertRaisesRegex(RuntimeError, "src 包装载位置"):
                bundled_root()
