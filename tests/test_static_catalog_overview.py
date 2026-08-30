# 验证游戏资料库固定登记全部 110 张发行静态表。
from __future__ import annotations

import unittest
from pathlib import Path

from src.features.static_catalog.providers.overview import (
    StaticCatalogOverviewProvider,
    registered_static_table_count,
)
from src.integrations.static_catalog_release import StaticCatalogReleaseReader


ROOT = Path(__file__).resolve().parents[1]


class StaticCatalogOverviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = ROOT / "data" / "game_static.sqlite3"
        self.release = StaticCatalogReleaseReader().freeze(self.database_path)
        self.provider = StaticCatalogOverviewProvider(str(self.database_path))

    def tearDown(self) -> None:
        self.provider.close()

    def test_registers_every_release_table(self) -> None:
        self.assertEqual(registered_static_table_count(), 110)
        page = self.provider.search(
            self.release, query="", offset=0, limit=110
        )
        self.assertEqual(page.total, 110)
        self.assertEqual(len(page.items), 110)
        self.assertIn("source_row", {item.record_id for item in page.items})

    def test_source_payload_omission_is_explicit(self) -> None:
        detail = self.provider.detail(self.release, "source_row")
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertTrue(any("省略" in note for note in detail.notes))

    def test_empty_tables_are_registered_without_fake_values(self) -> None:
        detail = self.provider.detail(self.release, "character_shape_bonus")
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertTrue(any("不会用 0" in note for note in detail.notes))


if __name__ == "__main__":
    unittest.main()
