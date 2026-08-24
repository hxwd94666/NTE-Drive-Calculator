# 验证主摘要只扣权威超杀，不把 HP 区间重叠诊断重复扣除。
from types import SimpleNamespace
import unittest

from src.features.battle_report.page import BattleReportPage


class BattleReportSummaryCorrectionUiTests(unittest.TestCase):
    def test_top_summary_subtracts_authoritative_overkill_only(self) -> None:
        rendered: dict[str, str] = {}
        labels = {
            key: SimpleNamespace(
                setText=lambda value, name=key: rendered.__setitem__(name, value)
            )
            for key in ("damage", "dps", "duration")
        }
        page = SimpleNamespace(
            _latest_summary=SimpleNamespace(
                total_damage=1_000.0,
                duration_seconds=10.0,
            ),
            metric_labels=labels,
            long_analysis_view=SimpleNamespace(
                set_analysis=lambda _analysis, selected_character_id=None: None
            ),
            marginal_page=SimpleNamespace(set_analysis=lambda _analysis: None),
        )
        analysis = SimpleNamespace(
            axis_complete=True,
            battle_end_us=10_000_000,
            timeline_damage_correction_total=250.0,
            timeline_damage_overlap_correction_total=50.0,
        )

        BattleReportPage.set_analysis(page, analysis)

        self.assertEqual("750", rendered["damage"])
        self.assertEqual("75", rendered["dps"])

    def test_incomplete_axis_does_not_partially_correct_full_summary(self) -> None:
        rendered: dict[str, str] = {}
        page = SimpleNamespace(
            _latest_summary=SimpleNamespace(total_damage=1_000.0, duration_seconds=10.0),
            metric_labels={
                key: SimpleNamespace(
                    setText=lambda value, name=key: rendered.__setitem__(name, value)
                )
                for key in ("damage", "dps", "duration")
            },
            long_analysis_view=SimpleNamespace(set_analysis=lambda *_args, **_kwargs: None),
            marginal_page=SimpleNamespace(set_analysis=lambda _analysis: None),
        )
        analysis = SimpleNamespace(
            axis_complete=False,
            battle_end_us=10_000_000,
            timeline_damage_correction_total=250.0,
        )

        BattleReportPage.set_analysis(page, analysis)

        self.assertEqual("1,000", rendered["damage"])
        self.assertEqual("100", rendered["dps"])


if __name__ == "__main__":
    unittest.main()
