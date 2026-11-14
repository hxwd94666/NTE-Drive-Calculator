# 验证新旧页面结果位置变化的归一化仍完整保留原始字段和人物面板结果。
from dataclasses import dataclass
import unittest

from tools.battle_report.benchmark_native_analysis import _canonical_payload


@dataclass(frozen=True, slots=True)
class _Page:
    analysis: object
    target_catalog: object
    target_catalog_error: object
    marginal_benefits: object
    marginal_panel: object = None
    candidate_display_analysis: object = None


class NativeAnalysisBenchmarkPayloadTests(unittest.TestCase):
    def test_only_relocated_duplicate_panel_is_removed_from_page_envelope(self):
        panel = ("all-quantified-panel-fields",)
        page = _Page("complete-analysis", {"target": "unknown"}, None, "all-benefits", panel)
        canonical = _canonical_payload({"page": page, "panel_results": panel, "units": {"CritBase": 0.032}})
        self.assertEqual(canonical["page"], {
            "analysis": "complete-analysis", "target_catalog": {"target": "unknown"},
            "target_catalog_error": None, "marginal_benefits": "all-benefits",
            "candidate_display_analysis": None,
        })
        self.assertIs(canonical["panel_results"], panel)
        self.assertEqual(canonical["units"], {"CritBase": 0.032})

    def test_old_unpickled_page_without_new_slot_is_readable(self):
        page = _Page("analysis", None, "catalog-error", "benefits")
        object.__delattr__(page, "marginal_panel")
        object.__delattr__(page, "candidate_display_analysis")
        canonical = _canonical_payload({"page": page, "panel_results": (), "units": {}})
        self.assertEqual(canonical["page"]["target_catalog_error"], "catalog-error")
        self.assertEqual(canonical["page"]["marginal_benefits"], "benefits")

    def test_panel_is_not_dropped_without_its_separate_comparison_value(self):
        payload = {"page": _Page(None, None, None, None, "only-panel-value")}
        self.assertIs(_canonical_payload(payload), payload)


if __name__ == "__main__":
    unittest.main()
