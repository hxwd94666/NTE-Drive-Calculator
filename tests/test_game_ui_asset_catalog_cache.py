# 验证公共素材清单缓存替换、受限路径与缺项行为。
# 验证图片目录缓存跨实例复用、清单更换失效及路径边界。
"""Immutable bundle cache behavior, without image decoding or UI state."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.integrations.game_assets.catalog import GameUiAssetCatalog


class GameUiAssetCatalogCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "one.png").write_bytes(b"fixture")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({"characters": {"1": "one.png"}}), encoding="utf-8")

    def test_manifest_is_read_once_for_unchanged_instances(self):
        original = Path.read_text
        reads = []

        def read(path, *args, **kwargs):
            reads.append(path)
            return original(path, *args, **kwargs)

        with patch.object(Path, "read_text", read):
            first = GameUiAssetCatalog(self.root)
            second = GameUiAssetCatalog(self.root)
            self.assertEqual(first.character_icon(1), second.character_icon(1))
        self.assertEqual([self.manifest], reads)

    def test_atomic_manifest_replacement_invalidates_cached_paths(self):
        first = GameUiAssetCatalog(self.root)
        self.assertEqual(self.root / "one.png", first.character_icon(1))
        (self.root / "two.png").write_bytes(b"fixture")
        replacement = self.root / "replacement.json"
        replacement.write_text(json.dumps({"characters": {"1": "two.png"}}), encoding="utf-8")
        replacement.replace(self.manifest)
        self.assertEqual(self.root / "two.png", GameUiAssetCatalog(self.root).character_icon(1))

    def test_cached_resolution_still_rejects_paths_outside_bundle(self):
        self.manifest.write_text(json.dumps({"characters": {"1": "../outside.png"}}), encoding="utf-8")
        self.assertIsNone(GameUiAssetCatalog(self.root).character_icon(1))


if __name__ == "__main__":
    unittest.main()
