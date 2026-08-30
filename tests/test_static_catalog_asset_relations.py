# 验证游戏资料库资源关系通过公共入口按页加载并保留不可用目标。
from __future__ import annotations

import os
import unittest
from pathlib import Path
from typing import Any, cast

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QVBoxLayout, QWidget

from src.features.static_catalog.contracts import (
    CatalogDetail,
    CatalogField,
    CatalogItem,
    CatalogReference,
    CatalogRelationGroup,
    CatalogRelationPage,
    CatalogSection,
    CatalogValueSource,
)
from src.features.static_catalog.dependencies import build_static_catalog_providers
from src.features.static_catalog.page import StaticCatalogPage
from src.features.static_catalog.providers._adapter_common import encode_typed_record_id
from src.services.static_catalog_service import StaticCatalogService


ROOT = Path(__file__).resolve().parents[1]
STATIC_DATABASE = ROOT / "data" / "game_static.sqlite3"
BLUEPRINT_PATH = (
    "/Game/Blueprints/Abilities/Player/Ability_071_Chaos/GA_Chaos071_Melee"
)
MONTAGE_PATH = (
    "/Game/Characters/Player/073_rabbit/animation/Skill/"
    "Chiichan073_Skill_2_Short"
)
TAG_SOURCE_PATH = (
    "/Game/Blueprints/Abilities/Buff/Common/Buff_ExtemeEvde_AtkDisplay"
)
CURVE_SOURCE_PATH = (
    "/Game/Blueprints/Abilities/Buff/element/Reaction/ReactionDisplay/"
    "new/buff_reaction_display2_new"
)


class StaticCatalogAssetRelationIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = StaticCatalogService(
            static_database_path=STATIC_DATABASE,
            providers=build_static_catalog_providers(STATIC_DATABASE),
        )
        self.request = self.service.start_request()

    def tearDown(self) -> None:
        self.service.close()

    def test_blueprint_relation_rows_are_bounded_and_gate_target_links(self) -> None:
        record_id = encode_typed_record_id("blueprint", BLUEPRINT_PATH)
        detail = self.service.detail(
            self.request,
            domain_key="assets",
            record_id=record_id,
        )

        assert detail is not None
        groups = {group.kind: group for group in detail.relation_groups}
        self.assertEqual(groups["references"].total, 298)
        available = self.service.relations(
            self.request,
            domain_key="assets",
            record_id=record_id,
            relation_kind="references",
            offset=23,
            limit=1,
        )
        unavailable = self.service.relations(
            self.request,
            domain_key="assets",
            record_id=record_id,
            relation_kind="references",
            offset=24,
            limit=1,
        )

        self.assertEqual(len(available.rows), 1)
        self.assertTrue(available.rows[0].references)
        self.assertIsNotNone(self.service.detail(
            self.request,
            domain_key=available.rows[0].references[0].domain_key,
            record_id=available.rows[0].references[0].record_id,
        ))
        unavailable_values = {
            field.label: field.value for field in unavailable.rows[0].fields
        }
        self.assertEqual(unavailable_values["target_available"], "否")
        self.assertEqual(unavailable.rows[0].references, ())

    def test_montage_notify_count_and_rows_use_the_same_lazy_entry(self) -> None:
        record_id = encode_typed_record_id("montage", MONTAGE_PATH)
        detail = self.service.detail(
            self.request,
            domain_key="assets",
            record_id=record_id,
        )

        assert detail is not None
        notifies = next(group for group in detail.relation_groups if group.kind == "notifies")
        page = self.service.relations(
            self.request,
            domain_key="assets",
            record_id=record_id,
            relation_kind="notifies",
            offset=0,
            limit=5,
        )
        self.assertGreater(notifies.total, 5)
        self.assertEqual(page.total, notifies.total)
        self.assertEqual(len(page.rows), 5)

    def test_tag_relation_uses_the_formal_composite_detail_key(self) -> None:
        page = self.service.relations(
            self.request,
            domain_key="assets",
            record_id=encode_typed_record_id("blueprint", TAG_SOURCE_PATH),
            relation_kind="tags",
            offset=0,
            limit=1,
        )

        self.assertEqual(len(page.rows[0].references), 1)
        reference = page.rows[0].references[0]
        self.assertIsNotNone(self.service.detail(
            self.request,
            domain_key=reference.domain_key,
            record_id=reference.record_id,
        ))

    def test_available_curve_reference_has_evidence_but_no_bad_button(self) -> None:
        page = self.service.relations(
            self.request,
            domain_key="assets",
            record_id=encode_typed_record_id("blueprint", CURVE_SOURCE_PATH),
            relation_kind="references",
            offset=3,
            limit=1,
        )

        values = {field.label: field.value for field in page.rows[0].fields}
        self.assertEqual(values["target_available"], "是")
        self.assertEqual(values["catalog_detail_available"], "否")
        self.assertEqual(page.rows[0].references, ())

    def test_missing_ga_montage_target_is_explicit_and_not_clickable(self) -> None:
        source = (
            "/Game/Blueprints/Abilities/Player/Ability_010_Nanally/GA_Nanally_Melee"
        )
        page = self.service.relations(
            self.request,
            domain_key="assets",
            record_id=encode_typed_record_id("blueprint", source),
            relation_kind="ability_montages",
            offset=15,
            limit=1,
        )

        values = {field.label: field.value for field in page.rows[0].fields}
        self.assertEqual(values["target_available"], "否")
        self.assertEqual(page.rows[0].references, ())


class _RelationController:
    PAGE_SIZE = 50

    def __init__(self) -> None:
        self.calls: list[int] = []

    def relations(self, **kwargs) -> CatalogRelationPage:
        self.calls.append(int(kwargs["offset"]))
        source = CatalogValueSource.OFFICIAL_STATIC
        return CatalogRelationPage(
            relation_kind="references",
            rows=(
                CatalogSection(
                    "可用目标",
                    (CatalogField("target_available", "是", source),),
                    (CatalogReference("查看目标资源", "assets", "target"),),
                ),
                CatalogSection(
                    "不可用目标",
                    (CatalogField("target_available", "否", source),),
                ),
            ),
            total=2,
            offset=0,
            limit=50,
        )


class StaticCatalogAssetRelationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_relation_rows_are_not_requested_until_load_button_is_clicked(self) -> None:
        controller = _RelationController()
        owner = QWidget()
        host = QWidget()
        page = StaticCatalogPage(
            controller=cast(Any, controller),
            dialog_parent=owner,
        )
        page._detail_layout = QVBoxLayout(host)
        page._render_detail(CatalogDetail(
            item=CatalogItem("assets", "source", "GA 资源"),
            sections=(),
            relation_groups=(CatalogRelationGroup("references", "资源引用", 2),),
        ))

        self.assertEqual(controller.calls, [])
        load = host.findChild(QPushButton, "staticCatalogLoadRelations")
        assert load is not None
        load.click()
        self.app.processEvents()

        self.assertEqual(controller.calls, [0])
        self.assertEqual(
            len(host.findChildren(QPushButton, "staticCatalogRelationTarget")),
            1,
        )
        self.assertFalse(load.isEnabled())
        owner.deleteLater()
        host.deleteLater()


if __name__ == "__main__":
    unittest.main()
