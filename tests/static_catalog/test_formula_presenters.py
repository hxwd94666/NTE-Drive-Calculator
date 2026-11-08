# 覆盖游戏资料库公式详情和支持矩阵的 Qt 无关展示投影。
from __future__ import annotations

import unittest

from src.features.static_catalog.counterfactual_model_matrix import (
    build_counterfactual_model_matrix,
)
from src.services.static_catalog.formulas.service import StaticCatalogFormulaService
from src.storage.sqlite.catalog.static_catalog_formula_queries import (
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
    def test_matrix_counts_rows_without_exposing_execution_controls(self) -> None:
        matrix = build_counterfactual_model_matrix(domain_fixture())
        counts = dict(matrix.status_counts)

        self.assertEqual(len(matrix.rows), sum(counts.values()))
        self.assertIn("不控制", matrix.readonly_notice)
        self.assertTrue(all(not hasattr(row, "enabled") for row in matrix.rows))
        self.assertTrue(all(not hasattr(row, "executor") for row in matrix.rows))

if __name__ == "__main__":
    unittest.main()
