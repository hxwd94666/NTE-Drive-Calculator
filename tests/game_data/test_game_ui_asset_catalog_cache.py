# 验证清单替换使素材缓存失效，缓存仍拒绝根目录外路径。
"""Immutable bundle cache behavior, without image decoding or UI state."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from src.integrations.game_assets.catalog import GameUiAssetCatalog


class GameUiAssetCatalogCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "one.png").write_bytes(b"fixture")
        self.manifest = self.root / "manifest.json"
        self.manifest.write_text(json.dumps({"characters": {"1": "one.png"}}), encoding="utf-8")

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
