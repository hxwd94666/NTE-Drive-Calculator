# 验证游戏资料库装备、效果、资源与来源域的只读契约。
from __future__ import annotations

import unittest
from pathlib import Path

from src.services.static_catalog_misc_service import (
    CatalogDetail,
    ORIGIN_DERIVED,
    SourceTrace,
    StaticCatalogMiscService,
)
from src.storage.sqlite.static_catalog_misc_queries import StaticCatalogMiscDao


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"
STATIC_MANIFEST = PROJECT_ROOT / "data" / "manifest.json"


class StaticCatalogMiscQueryTests(unittest.TestCase):
    def test_all_domain_search_finds_formal_ga_without_loading_every_row(self) -> None:
        with StaticCatalogMiscDao(STATIC_DATABASE) as dao:
            page = dao.search_catalog_entries(
                "all", "GA_Adler_Melee", limit=5, offset=0
            )

        self.assertLessEqual(len(page["items"]), 5)
        self.assertGreaterEqual(page["total"], 1)
        self.assertTrue(
            any(
                row["entity_kind"] == "gameplay_ability"
                and row["entity_key"] == "GA_Adler_Melee"
                for row in page["items"]
            )
        )

    def test_gameplay_tag_search_collapses_duplicate_property_occurrences(self) -> None:
        with StaticCatalogMiscDao(STATIC_DATABASE) as dao:
            page = dao.search_catalog_entries(
                "effects", "State.Damage.Dot", limit=100, offset=0
            )

        tag_keys = [
            row["entity_key"]
            for row in page["items"]
            if row["entity_kind"] == "gameplay_tag"
        ]
        self.assertTrue(tag_keys)
        self.assertEqual(len(tag_keys), len(set(tag_keys)))

    def test_search_values_are_parameterized_and_wildcards_are_literal(self) -> None:
        with StaticCatalogMiscDao(STATIC_DATABASE) as dao:
            injection = dao.search_catalog_entries(
                "equipment", "Suit1' OR 1=1 --", limit=10, offset=0
            )
            literal_wildcard = dao.search_catalog_entries(
                "equipment", "%", limit=10, offset=0
            )

        self.assertEqual(injection["total"], 0)
        self.assertEqual(literal_wildcard["total"], 0)

    def test_query_and_relation_pages_enforce_the_shared_limit(self) -> None:
        with StaticCatalogMiscDao(STATIC_DATABASE) as dao:
            with self.assertRaises(ValueError):
                dao.search_catalog_entries("effects", "", limit=101)
            with self.assertRaises(ValueError):
                dao.list_asset_relations(
                    "montage", "/Game/Any", "notifies", limit=0
                )
            with self.assertRaises(ValueError):
                dao.search_catalog_entries("sqlite_schema", "", limit=10)

    def test_skill_damage_relation_coverage_matches_release_catalogs(self) -> None:
        with StaticCatalogMiscDao(STATIC_DATABASE) as dao:
            coverage = dao.get_skill_damage_relation_coverage()

        self.assertEqual(coverage["missing_ability_targets"], 108)
        self.assertEqual(coverage["absent_ability_ids"], 73)
        self.assertEqual(coverage["missing_gameplay_effect_targets"], 12)

    def test_montage_notifies_are_paged_independently(self) -> None:
        montage_path = (
            "/Game/Characters/Player/073_rabbit/animation/Skill/"
            "Chiichan073_Skill_2_Short"
        )
        with StaticCatalogMiscDao(STATIC_DATABASE) as dao:
            page = dao.list_asset_relations(
                "montage", montage_path, "notifies", limit=5, offset=0
            )

        self.assertGreater(page["total"], 5)
        self.assertEqual(len(page["items"]), 5)


class StaticCatalogMiscServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = StaticCatalogMiscService(
            STATIC_DATABASE,
            manifest_path=STATIC_MANIFEST,
        )

    def test_release_metadata_preserves_payload_omission_boundary(self) -> None:
        metadata = self.service.release_metadata()

        self.assertEqual(metadata.dataset_id, "cn_1_3_13_20260828")
        self.assertEqual(metadata.schema_version, 31)
        self.assertEqual(metadata.importer_version, 37)
        self.assertTrue(metadata.source_payloads_omitted)

    def test_source_trace_does_not_promise_an_omitted_payload(self) -> None:
        trace = self.service.source_trace(source_row_id=1)

        self.assertIsInstance(trace, SourceTrace)
        self.assertTrue(trace.payloads_omitted)
        self.assertFalse(trace.payload_present)
        self.assertIn("省略原始 payload", trace.explanation)
        self.assertFalse(Path(trace.relative_path).is_absolute())

    def test_graduation_template_is_marked_as_derived_display(self) -> None:
        detail = self.service.detail("graduation_template", "1003")

        self.assertIsInstance(detail, CatalogDetail)
        self.assertEqual(detail.origin_kind, ORIGIN_DERIVED)
        self.assertTrue(detail.sections)
        self.assertTrue(
            all(
                field.origin_kind == ORIGIN_DERIVED
                for section in detail.sections
                for field in section.fields
            )
        )

    def test_formal_ids_tags_and_resource_paths_are_copyable(self) -> None:
        ability = self.service.detail("gameplay_ability", "GA_Adler_Melee")
        tag_page = self.service.search("effects", "State.Damage.Dot", limit=10)
        tag = next(
            item for item in tag_page.items if item.entity_kind == "gameplay_tag"
        )
        tag_detail = self.service.detail(tag.entity_kind, tag.entity_key)

        ability_copy_kinds = {
            field.copy_kind
            for section in ability.sections
            for field in section.fields
        }
        tag_copy_kinds = {
            field.copy_kind
            for section in tag_detail.sections
            for field in section.fields
        }
        self.assertIn("ga_id", ability_copy_kinds)
        self.assertIn("resource_path", ability_copy_kinds)
        self.assertIn("gameplay_tag", tag_copy_kinds)

    def test_source_rows_are_lazy_and_bounded(self) -> None:
        page = self.service.source_rows(1, limit=2, offset=0)

        self.assertEqual(len(page.rows), 2)
        self.assertGreater(page.total, len(page.rows))
        self.assertTrue(page.has_more)

    def test_composite_combat_curve_key_resolves_points(self) -> None:
        key = (
            "/Game/DataTable/Skill/GlobalCharacterData/"
            f"DT_AICharacterEffectFigure{chr(31)}AIDamage"
        )
        detail = self.service.detail("combat_curve", key)

        self.assertIsInstance(detail, CatalogDetail)
        self.assertEqual(detail.title, "AIDamage")
        self.assertTrue(any(section.title.startswith("曲线点") for section in detail.sections))

    def test_source_row_search_jumps_to_retained_trace(self) -> None:
        page = self.service.search("sources", "abyss_10_buff_01", limit=5)
        item = next(row for row in page.items if row.entity_kind == "source_row")
        trace = self.service.detail(item.entity_kind, item.entity_key)

        self.assertIsInstance(trace, SourceTrace)
        self.assertEqual(trace.row_key, "abyss_10_buff_01")

    def test_combat_effect_relations_only_link_available_targets(self) -> None:
        relations = StaticCatalogMiscService._relations(
            "combat_effect",
            {
                "buff_links": (
                    {
                        "target_asset_path": "/Game/Missing/Buff_Unavailable",
                        "target_available": False,
                    },
                    {
                        "target_asset_path": "/Game/Available/Buff_Available",
                        "target_available": True,
                    },
                )
            },
        )

        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].target_key, "/Game/Available/Buff_Available")

    def test_skill_damage_shows_complete_breakable_fields_and_validated_links(self) -> None:
        complete = self.service.detail(
            "skill_damage", "GE_Player_Adler_Aim_Damage"
        )
        missing_ga = self.service.detail(
            "skill_damage", "GE_Player_Adler_PerfectEvadeAttack_Damage"
        )
        missing_ge = self.service.detail(
            "skill_damage", "GE_Player_Cang_AirAttack1_Damage"
        )

        fields = {
            field.label: field.value
            for section in complete.sections
            for field in section.fields
        }
        self.assertTrue({
            "覆盖可破坏物伤害",
            "可破坏物伤害",
            "覆盖可破坏物冲量",
            "可破坏物冲量",
            "覆盖载具可破坏冲量",
            "载具可破坏冲量",
        }.issubset(fields))
        self.assertEqual(fields["来源 GA 关系状态"], "available")
        self.assertEqual(fields["同名 GE 关系状态"], "available")
        self.assertEqual(
            {relation.target_kind for relation in complete.relations},
            {"gameplay_ability", "gameplay_effect"},
        )

        missing_ga_fields = {
            field.label: field.value
            for section in missing_ga.sections
            for field in section.fields
        }
        self.assertEqual(missing_ga_fields["来源 GA 关系状态"], "unavailable")
        self.assertNotIn(
            "gameplay_ability",
            {relation.target_kind for relation in missing_ga.relations},
        )
        missing_ge_fields = {
            field.label: field.value
            for section in missing_ge.sections
            for field in section.fields
        }
        self.assertEqual(missing_ge_fields["同名 GE 关系状态"], "unavailable")
        self.assertNotIn(
            "gameplay_effect",
            {relation.target_kind for relation in missing_ge.relations},
        )

    def test_roguelike_modifier_is_searchable_and_structured_without_owner_guess(self) -> None:
        page = self.service.search("effects", "RG_AtkUp_1", limit=10)
        item = next(
            row
            for row in page.items
            if row.entity_kind == "roguelike_modifier"
            and row.entity_key == "RG_AtkUp_1"
        )
        detail = self.service.detail(item.entity_kind, item.entity_key)

        fields = {
            field.label: field.value
            for section in detail.sections
            for field in section.fields
        }
        property_section = next(
            section for section in detail.sections
            if section.title == "属性 Modifier #1"
        )
        property_labels = {field.label for field in property_section.fields}
        self.assertEqual(fields["生效条件"], "[]")
        self.assertEqual(fields["归属解析状态"], "unavailable")
        self.assertEqual(
            property_labels,
            {"序号", "属性正式 ID", "修改运算", "属性值", "排序键"},
        )
        self.assertIsNotNone(detail.source_row_id)
        trace = self.service.source_trace(source_row_id=detail.source_row_id)
        self.assertEqual(trace.source_row_id, detail.source_row_id)


if __name__ == "__main__":
    unittest.main()
