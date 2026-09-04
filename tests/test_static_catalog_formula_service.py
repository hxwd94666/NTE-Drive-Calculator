# 覆盖游戏资料库公式与反事实状态的只读领域 contract。
from __future__ import annotations

import unittest

from src.services.static_catalog_formula_service import StaticCatalogFormulaService
from src.storage.sqlite.static_catalog_formula_queries import (
    StaticCatalogFormulaQueries,
    StaticFormulaEvidenceSnapshot,
)


def evidence_snapshot() -> StaticFormulaEvidenceSnapshot:
    return StaticFormulaEvidenceSnapshot(
        dataset_id="fixture_dataset",
        importer_version=34,
        schema_version=29,
        skill_damage_rows=907,
        skill_damage_modifier_rows=14,
        awakening_effect_rows=184,
        awakening_skill_level_bonus_rows=66,
        fork_rows=49,
        fork_modifier_rows=2286,
        buff_definition_rows=1535,
        gameplay_effect_definition_rows=1523,
        buff_modifier_rows=565,
        formal_dot_tag_rows=30,
        formal_dot_tag_assets=8,
        formal_attachment_tag_assets=6,
        final_damage_up_modifier_rows=5,
        dot_scoped_final_damage_up_modifier_rows=1,
    )


class StaticCatalogFormulaServiceTests(unittest.TestCase):
    def test_catalog_covers_every_required_formula_zone(self) -> None:
        domain = StaticCatalogFormulaService.from_snapshot(evidence_snapshot())

        self.assertTrue(domain.readonly)
        self.assertEqual(
            {
                "panel_attribute",
                "skill_multiplier",
                "direct_damage",
                "damage_increase",
                "vulnerability",
                "critical",
                "defense",
                "resistance",
                "independent_final_damage",
                "dot_damage",
                "topple_damage",
                "weave_followup",
                "settlement_rounding",
                "max_hp_settlement",
            },
            {entry.key for entry in domain.formulas},
        )

    def test_document_rules_are_not_presented_as_database_fields(self) -> None:
        domain = StaticCatalogFormulaService.from_snapshot(evidence_snapshot())
        direct = next(row for row in domain.formulas if row.key == "direct_damage")
        skill = next(row for row in domain.formulas if row.key == "skill_multiplier")
        critical = next(row for row in domain.formulas if row.key == "critical")
        settlement = next(
            row for row in domain.formulas if row.key == "settlement_rounding"
        )

        self.assertEqual("project_rule", direct.boundary)
        self.assertIn("project_contract", {row.kind for row in direct.evidence})
        self.assertNotIn("official_static", {row.kind for row in direct.evidence})
        self.assertIn("official_static", {row.kind for row in skill.evidence})
        static_sources = [row for row in skill.evidence if row.kind == "official_static"]
        self.assertTrue(all("inputs" in row.note or "rows=" in row.note for row in static_sources))
        self.assertIn("floor(FullPrecision)", critical.expression)
        self.assertEqual(
            "Settlement = floor(max(0, FullPrecisionDamage))",
            settlement.expression,
        )

    def test_matrix_has_four_states_and_gaps_for_incomplete_support(self) -> None:
        domain = StaticCatalogFormulaService.from_snapshot(evidence_snapshot())
        rows = domain.counterfactual_support

        self.assertEqual(
            {"complete", "partial", "unavailable", "not_applicable"},
            {row.status for row in rows},
        )
        for row in rows:
            self.assertTrue(row.evidence)
            self.assertTrue(row.modeling_scheme)
            self.assertTrue(row.covered_dataset)
            self.assertTrue(row.limitations)
            if row.status in {"partial", "unavailable"}:
                self.assertTrue(row.gap_codes, row.key)

    def test_unknown_mechanisms_never_gain_a_zero_or_enabled_flag(self) -> None:
        domain = StaticCatalogFormulaService.from_snapshot(evidence_snapshot())
        summon = next(
            row for row in domain.counterfactual_support
            if row.key == "summon_lifecycle"
        )

        self.assertEqual("unavailable", summon.status)
        self.assertIn("不生成候选命中", summon.modeling_scheme)
        self.assertFalse(hasattr(summon, "enabled"))
        self.assertFalse(hasattr(summon, "ratio"))

    def test_native_sidecar_is_partial_and_never_claims_production_entry(self) -> None:
        domain = StaticCatalogFormulaService.from_snapshot(evidence_snapshot())
        native = next(
            row for row in domain.counterfactual_support
            if row.key == "native_counterfactual_core"
        )

        self.assertEqual("partial", native.status)
        self.assertIn("8 Buff / 56 逐击公开差分", native.covered_entities)
        self.assertEqual((), native.consumer_entries)
        self.assertIn("native_production_consumer_unavailable", native.gap_codes)

    def test_release_query_exposes_formal_counts_without_payloads(self) -> None:
        with StaticCatalogFormulaQueries() as queries:
            snapshot = queries.evidence_snapshot()

        self.assertTrue(snapshot.dataset_id)
        self.assertGreater(snapshot.skill_damage_rows, 0)
        self.assertGreater(snapshot.formal_dot_tag_assets, 0)
        self.assertGreaterEqual(
            snapshot.final_damage_up_modifier_rows,
            snapshot.dot_scoped_final_damage_up_modifier_rows,
        )
        self.assertFalse(hasattr(snapshot, "raw_payload"))


if __name__ == "__main__":
    unittest.main()
