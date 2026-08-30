# 验证游戏资料库所有领域已接入公共搜索与详情入口。
from __future__ import annotations

import unittest
from pathlib import Path

from src.features.static_catalog.dependencies import build_static_catalog_providers
from src.features.static_catalog.providers._adapter_common import (
    encode_typed_record_id,
)
from src.services.static_catalog_service import StaticCatalogService


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_DOMAINS = {
    "coverage",
    "character",
    "fork",
    "monsters",
    "equipment",
    "skills",
    "effects",
    "assets",
    "sources",
    "formulas",
    "counterfactual_models",
}


class StaticCatalogIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        database_path = ROOT / "data" / "game_static.sqlite3"
        self.service = StaticCatalogService(
            static_database_path=database_path,
            providers=build_static_catalog_providers(database_path),
        )

    def tearDown(self) -> None:
        self.service.close()

    def test_every_composed_domain_searches_and_opens_detail(self) -> None:
        request = self.service.start_request()
        self.assertEqual({domain.key for domain in request.domains}, EXPECTED_DOMAINS)
        for domain in request.domains:
            with self.subTest(domain=domain.key):
                page = self.service.search(
                    request,
                    domain_key=domain.key,
                    query="",
                    offset=0,
                    limit=1,
                )
                self.assertGreater(page.total, 0)
                self.assertEqual(len(page.items), 1)
                detail = self.service.detail(
                    request,
                    domain_key=domain.key,
                    record_id=page.items[0].record_id,
                )
                self.assertIsNotNone(detail)

    def test_global_search_is_bounded_and_uses_all_domain_totals(self) -> None:
        request = self.service.start_request()
        page = self.service.search(
            request,
            domain_key="all",
            query="",
            offset=0,
            limit=10,
        )
        self.assertEqual(len(page.items), 10)
        self.assertGreater(page.total, 110)

    def test_cross_domain_fork_reference_opens_real_detail(self) -> None:
        request = self.service.start_request()
        graduation = self.service.detail(
            request,
            domain_key="equipment",
            record_id=encode_typed_record_id("graduation_template", "1036"),
        )

        assert graduation is not None
        reference = next(
            relation
            for section in graduation.sections
            for relation in section.references
            if relation.label == "查看弧盘"
        )
        self.assertEqual("fork", reference.domain_key)
        self.assertIsNotNone(self.service.detail(
            request,
            domain_key=reference.domain_key,
            record_id=reference.record_id,
        ))

    def test_character_cultivation_projects_formal_stage_skills(self) -> None:
        request = self.service.start_request()
        detail = self.service.detail(
            request,
            domain_key="character",
            record_id="1036",
        )

        assert detail is not None
        stage_route = next(
            field.value
            for section in detail.sections
            for field in section.fields
            if field.label == "阶段路线"
        )
        self.assertIn("GA_Zankou_Melee Lv.1", stage_route)
        self.assertIn("GA_Zankou_UltraSkill Lv.10", stage_route)

    def test_roguelike_search_result_opens_through_public_provider(self) -> None:
        request = self.service.start_request()
        page = self.service.search(
            request,
            domain_key="effects",
            query="RG_AtkUp_1",
            offset=0,
            limit=10,
        )
        item = next(row for row in page.items if row.title == "RG_AtkUp_1")

        self.assertIsNotNone(self.service.detail(
            request,
            domain_key=item.domain_key,
            record_id=item.record_id,
        ))


if __name__ == "__main__":
    unittest.main()
