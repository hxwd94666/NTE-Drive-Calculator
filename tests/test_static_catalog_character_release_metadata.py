# 验证角色发行注解只消费 v30 数据行与公共术语投影。
from __future__ import annotations

import unittest

from src.domain.static_catalog_terminology import LocalizedTermRecord
from src.services.static_catalog_character_release_metadata import (
    CharacterReleaseMetadataService,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


NTE_TEST_TIER = "core"


class _ReleaseSource:
    def list_catalog_character_release_annotations(self):
        return (
            {
                "character_id": 7001,
                "quality": "A",
                "quality_source_kind": "official",
                "acquisition_type": "limited",
                "acquisition_source_kind": "official",
                "mainland_release_date": "2030-01-02",
                "release_source_kind": "official",
                "evidence_keys": ("official_row", "official_row"),
            },
            {
                "character_id": 7002,
                "quality": "S",
                "quality_source_kind": "reviewed_fallback",
                "acquisition_type": "permanent",
                "acquisition_source_kind": "reviewed_fallback",
                "mainland_release_date": "2029-12-01",
                "release_source_kind": "reviewed_fallback",
                "evidence_keys": ("reviewed_row",),
            },
            {
                "character_id": 7003,
                "quality": "S",
                "quality_source_kind": "official",
                "acquisition_type": "free",
                "acquisition_source_kind": "official",
                "mainland_release_date": "2030-02-03",
                "release_source_kind": "official",
                "evidence_keys": (),
            },
        )


class _TerminologySource:
    def lookup_localized_term(self, entity_kind, stable_id, *, context):
        if entity_kind != "character_acquisition_type":
            return None
        names = {
            "permanent": {"zh-CN": "常驻", "en-US": "Standard"},
            "limited": {"zh-CN": "限定", "en-US": "Limited"},
            "free": {"zh-CN": "免费获取"},
        }.get(stable_id)
        if names is None:
            return None
        return LocalizedTermRecord(
            entity_kind=entity_kind,
            canonical_id=stable_id,
            names=names,
            source_kind=(
                "reviewed_annotation"
                if stable_id == "free"
                else "formal_localization"
            ),
        )


class _MissingTerminologySource:
    def lookup_localized_term(self, entity_kind, stable_id, *, context):
        return None


class CharacterReleaseMetadataServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CharacterReleaseMetadataService(
            _ReleaseSource(),
            StaticCatalogTerminologyService(_TerminologySource()),
        )

    def test_projects_exact_data_rows_without_seed_fallback(self) -> None:
        limited = self.service.metadata(7001)

        self.assertIsNotNone(limited)
        assert limited is not None
        self.assertEqual(("A", "limited", "2030-01-02"), (
            limited.quality,
            limited.acquisition_type,
            limited.release_date,
        ))
        self.assertEqual(("official_row",), limited.evidence_keys)
        self.assertEqual("限定", limited.acquisition_term.display_name)
        self.assertIsNone(self.service.metadata(1004))

    def test_preserves_injected_filter_order_and_term_provenance(self) -> None:
        terms = self.service.acquisition_terms()

        self.assertEqual(
            ("limited", "permanent", "free"),
            tuple(term.requested_id for term in terms),
        )
        free = next(term for term in terms if term.requested_id == "free")
        self.assertEqual("complete", free.status)
        self.assertEqual("免费获取", free.display_name)
        self.assertEqual("reviewed_annotation", free.source_kind)

    def test_missing_injected_term_stays_explicit(self) -> None:
        service = CharacterReleaseMetadataService(
            _ReleaseSource(),
            StaticCatalogTerminologyService(_MissingTerminologySource()),
        )

        free = next(
            term for term in service.acquisition_terms()
            if term.requested_id == "free"
        )
        self.assertEqual("name_missing", free.status)
        self.assertIsNone(free.display_name)


if __name__ == "__main__":
    unittest.main()
