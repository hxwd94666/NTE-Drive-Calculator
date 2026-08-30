# 覆盖游戏资料库公式详情和支持矩阵的 Qt 无关展示投影。
from __future__ import annotations

import unittest

from src.features.static_catalog.counterfactual_model_matrix import (
    build_counterfactual_model_matrix,
)
from src.features.static_catalog.formula_detail import build_formula_detail_sections
from src.services.static_catalog_formula_service import StaticCatalogFormulaService
from src.storage.sqlite.static_catalog_formula_queries import (
    StaticFormulaEvidenceSnapshot,
)


def domain_fixture():
    return StaticCatalogFormulaService.from_snapshot(
        StaticFormulaEvidenceSnapshot(
            dataset_id="fixture_dataset",
            importer_version=34,
            schema_version=29,
            skill_damage_rows=10,
            skill_damage_modifier_rows=2,
            awakening_effect_rows=6,
            awakening_skill_level_bonus_rows=1,
            fork_rows=3,
            fork_modifier_rows=12,
            buff_definition_rows=8,
            gameplay_effect_definition_rows=9,
            buff_modifier_rows=20,
            formal_dot_tag_rows=4,
            formal_dot_tag_assets=2,
            formal_attachment_tag_assets=1,
            final_damage_up_modifier_rows=2,
            dot_scoped_final_damage_up_modifier_rows=1,
        )
    )


class StaticCatalogFormulaPresenterTests(unittest.TestCase):
    def test_formula_detail_retains_source_type_and_boundary(self) -> None:
        sections = build_formula_detail_sections(domain_fixture())
        formulas = [formula for section in sections for formula in section.formulas]
        skill = next(row for row in formulas if row.key == "skill_multiplier")

        self.assertEqual("项目规则", skill.boundary_label)
        self.assertIn("官方静态输入", {source.source_type for source in skill.sources})
        self.assertIn("项目规则", {source.source_type for source in skill.sources})

    def test_matrix_counts_rows_without_exposing_execution_controls(self) -> None:
        matrix = build_counterfactual_model_matrix(domain_fixture())
        counts = dict(matrix.status_counts)

        self.assertEqual(len(matrix.rows), sum(counts.values()))
        self.assertIn("不控制", matrix.readonly_notice)
        self.assertTrue(all(not hasattr(row, "enabled") for row in matrix.rows))
        self.assertTrue(all(not hasattr(row, "executor") for row in matrix.rows))

    def test_matrix_exposes_evidence_consumers_gaps_and_limits(self) -> None:
        matrix = build_counterfactual_model_matrix(domain_fixture())
        dot = next(row for row in matrix.rows if row.key == "dot_state_replay")

        self.assertEqual("partial", dot.status)
        self.assertTrue(dot.evidence)
        self.assertTrue(dot.consumer_entries)
        self.assertIn("dot_state_kind_unmodeled", dot.gap_codes)
        self.assertEqual("fixture_dataset", dot.covered_dataset)
        self.assertIn("State.Damage.Dot", dot.covered_entities)
        self.assertTrue(dot.limitations)


if __name__ == "__main__":
    unittest.main()
