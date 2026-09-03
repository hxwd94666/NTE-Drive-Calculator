# 验证静态术语查询服务。
from __future__ import annotations

import unittest

from src.services.static_catalog_terminology_service import (
    LocalizedTermRecord,
    StaticCatalogTerminologyService,
)


class _Source:
    def __init__(self) -> None:
        self.rows = {
            ("item", "Fons", "progression_cost"): LocalizedTermRecord(
                entity_kind="item",
                canonical_id="Fons",
                names={"zh-CN": "方斯", "en-US": "Fons"},
                text_table="/Game/Text/ST_Item.ST_Item",
                text_key="item_Fons_name",
            ),
            ("item", "Gold", None): LocalizedTermRecord(
                entity_kind="item",
                canonical_id="Gold",
                names={"zh-CN": "甲硬币"},
                text_table="/Game/Text/ST_Ui.ST_Ui",
                text_key="gold_name",
            ),
            ("gameplay_ability", "GA_Test", None): LocalizedTermRecord(
                entity_kind="gameplay_ability",
                canonical_id="GA_Test",
                names={"zh-CN": "测试技能"},
                source_kind="reviewed_annotation",
            ),
            ("item", "Blank", None): LocalizedTermRecord(
                entity_kind="item",
                canonical_id="Blank",
                names={"zh-CN": "  "},
            ),
        }

    def lookup_localized_term(self, entity_kind, stable_id, *, context):
        if (entity_kind, stable_id, context) == (
            "item",
            "gold",
            "progression_cost",
        ):
            return self.rows[("item", "Fons", "progression_cost")]
        return self.rows.get((entity_kind, stable_id, context)) or self.rows.get(
            (entity_kind, stable_id, None)
        )


class StaticCatalogTerminologyServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = StaticCatalogTerminologyService(_Source())

    def test_context_alias_resolves_to_canonical_item_without_casefolding(self) -> None:
        cost = self.service.resolve("item", "gold", context="progression_cost")
        capital = self.service.resolve(
            "item", "Gold", context="progression_cost"
        )

        self.assertEqual((cost.canonical_id, cost.display_name), ("Fons", "方斯"))
        self.assertEqual(
            (capital.canonical_id, capital.display_name), ("Gold", "甲硬币")
        )

    def test_source_record_must_keep_the_requested_entity_kind(self) -> None:
        class _InvalidSource:
            def lookup_localized_term(self, entity_kind, stable_id, *, context):
                return LocalizedTermRecord("monster", stable_id, {"zh-CN": "错误"})

        service = StaticCatalogTerminologyService(_InvalidSource())

        with self.assertRaisesRegex(ValueError, "entity_kind"):
            service.resolve("item", "Fons")

    def test_requested_locale_precedes_configured_fallback(self) -> None:
        term = self.service.resolve(
            "item", "gold", context="progression_cost", locale="en_US"
        )

        self.assertEqual(term.display_name, "Fons")
        self.assertEqual(term.resolved_locale, "en-US")
        self.assertEqual(term.source_label, "Official in-game text")

    def test_missing_requested_locale_uses_explicit_fallback(self) -> None:
        term = self.service.resolve("item", "Gold", locale="ja-JP")

        self.assertEqual(term.display_name, "甲硬币")
        self.assertEqual(term.resolved_locale, "zh-CN")

    def test_missing_name_never_exposes_raw_identifier_as_display_name(self) -> None:
        absent = self.service.resolve("item", "Unknown_Internal_ID")
        blank = self.service.resolve("item", "Blank")

        self.assertEqual(absent.status, "name_missing")
        self.assertIsNone(absent.display_name)
        self.assertEqual(blank.status, "name_missing")
        self.assertIsNone(blank.display_name)

    def test_professional_identifier_is_preserved_separately(self) -> None:
        term = self.service.resolve("gameplay_ability", "GA_Test")

        self.assertEqual(term.display_name, "测试技能")
        self.assertEqual(term.canonical_id, "GA_Test")
        self.assertEqual(term.source_kind, "reviewed_annotation")
        self.assertEqual(term.source_label, "审阅注解")
        self.assertNotIn("GA_Test", term.display_name or "")


if __name__ == "__main__":
    unittest.main()
