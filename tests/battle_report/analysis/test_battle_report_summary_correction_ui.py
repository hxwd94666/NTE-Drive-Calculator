# 验证主摘要只扣权威超杀，不把 HP 区间重叠诊断重复扣除。
from types import SimpleNamespace
import unittest

from src.features.battle_report.page import BattleReportPage


class BattleReportSummaryCorrectionUiTests(unittest.TestCase):
    def test_half_metrics_follow_analysis_and_partial_axis_preserves_observations(self) -> None:
        rendered = {}
        page = SimpleNamespace(
            _latest_summary=SimpleNamespace(total_damage=9000.0, duration_seconds=30.0),
            _marginal_baseline_by_scope={},
            metric_labels={key: SimpleNamespace(setText=lambda value, name=key: rendered.__setitem__(name, value))
                           for key in ("damage", "dps", "duration", "taken")},
            long_analysis_view=SimpleNamespace(set_analysis=lambda *_a, **_k: None),
            marginal_page=SimpleNamespace(set_source_analysis=lambda *_a, **_k: None),
        )
        analysis = SimpleNamespace(
            axis_complete=True, battle_start_us=0, battle_end_us=30_000_000,
            range_start_us=0, range_end_us=10_000_000, effective_damage=1200.0,
            hits=(SimpleNamespace(direction="incoming", damage=50.0),),
            time_stop_intervals=((2_000_000, 4_000_000), (15_000_000, 17_000_000)),
            time_stop_source_kind="nte_core",
        )
        BattleReportPage.set_analysis(page, analysis)
        self.assertEqual({"damage": "1,200", "dps": "150", "duration": "8.0s（10.0s）", "taken": "50"}, rendered)
        analysis.range_start_us, analysis.range_end_us = 10_000_000, 30_000_000
        analysis.effective_damage = 5400.0
        analysis.hits = ()
        BattleReportPage.set_analysis(page, analysis)
        self.assertEqual({"damage": "5,400", "dps": "300", "duration": "18.0s（20.0s）", "taken": "0"}, rendered)
        analysis.time_stop_source_kind = "nte_core_partial"
        BattleReportPage.set_analysis(page, analysis)
        self.assertEqual("270", rendered["dps"])
        self.assertEqual("20.0s（20.0s）", rendered["duration"])
        analysis.axis_complete = False
        BattleReportPage.set_analysis(page, analysis)
        self.assertEqual(("5,400", "270", "—"), (rendered["damage"], rendered["dps"], rendered["taken"]))
        analysis.effective_damage = 0.0
        BattleReportPage.set_analysis(page, analysis)
        self.assertEqual(("—", "—", "—"), (rendered["damage"], rendered["dps"], rendered["taken"]))

    def test_top_summary_reuses_active_clock_for_duration_and_dps(self) -> None:
        rendered: dict[str, str] = {}
        page = SimpleNamespace(
            _latest_summary=SimpleNamespace(
                total_damage=1_000.0,
                duration_seconds=10.0,
            ),
            _marginal_baseline_by_scope={},
            metric_labels={
                key: SimpleNamespace(
                    setText=lambda value, name=key: rendered.__setitem__(name, value)
                )
                for key in ("damage", "dps", "duration")
            },
            long_analysis_view=SimpleNamespace(
                set_analysis=lambda *_args, **_kwargs: None
            ),
            marginal_page=SimpleNamespace(
                set_source_analysis=lambda _analysis, hit_details=None: None
            ),
        )
        analysis = SimpleNamespace(
            axis_complete=True,
            battle_start_us=0,
            battle_end_us=10_000_000,
            time_stop_intervals=((2_000_000, 4_000_000),),
            time_stop_source_kind="inferred_q_action",
            timeline_damage_correction_total=0.0,
        )

        BattleReportPage.set_analysis(page, analysis)

        self.assertEqual("1,000", rendered["damage"])
        self.assertEqual("125", rendered["dps"])
        self.assertEqual("8.0s（10.0s）", rendered["duration"])

    def test_top_summary_does_not_double_subtract_core_duration(self) -> None:
        rendered: dict[str, str] = {}
        page = SimpleNamespace(
            _latest_summary=SimpleNamespace(
                total_damage=800.0,
                duration_seconds=8.0,
                dps_time_mode="subtract_time_stop",
            ),
            _marginal_baseline_by_scope={},
            metric_labels={
                key: SimpleNamespace(
                    setText=lambda value, name=key: rendered.__setitem__(name, value)
                )
                for key in ("damage", "dps", "duration")
            },
            long_analysis_view=SimpleNamespace(
                set_analysis=lambda *_args, **_kwargs: None
            ),
            marginal_page=SimpleNamespace(
                set_source_analysis=lambda _analysis, hit_details=None: None
            ),
        )
        analysis = SimpleNamespace(
            axis_complete=True,
            battle_start_us=0,
            battle_end_us=7_000_000,
            time_stop_intervals=((2_000_000, 4_000_000),),
            time_stop_source_kind="nte_core",
            timeline_damage_correction_total=0.0,
        )

        BattleReportPage.set_analysis(page, analysis)

        self.assertEqual("100", rendered["dps"])
        self.assertEqual("8.0s（10.0s）", rendered["duration"])

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
            _marginal_baseline_by_scope={},
            metric_labels=labels,
            long_analysis_view=SimpleNamespace(
                set_analysis=lambda _analysis, selected_character_id=None, hit_details=None: None
            ),
            marginal_page=SimpleNamespace(
                set_source_analysis=lambda _analysis, hit_details=None: None
            ),
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

    def test_complete_axis_adds_included_full_timeline_max_hp_settlement(self) -> None:
        rendered: dict[str, str] = {}
        page = SimpleNamespace(
            _latest_summary=SimpleNamespace(total_damage=1_000.0, duration_seconds=10.0),
            _marginal_baseline_by_scope={},
            metric_labels={
                key: SimpleNamespace(
                    setText=lambda value, name=key: rendered.__setitem__(name, value)
                )
                for key in ("damage", "dps", "duration")
            },
            long_analysis_view=SimpleNamespace(set_analysis=lambda *_a, **_k: None),
            marginal_page=SimpleNamespace(set_source_analysis=lambda _analysis, hit_details=None: None),
        )
        analysis = SimpleNamespace(
            axis_complete=True,
            battle_start_us=0,
            battle_end_us=10_000_000,
            time_stop_intervals=(),
            timeline_damage_correction_total=100.0,
            timeline_max_hp_events=(
                SimpleNamespace(effective_hp_loss=250.0, included_in_effective_damage=True),
                SimpleNamespace(effective_hp_loss=50.0, included_in_effective_damage=False),
            ),
        )

        BattleReportPage.set_analysis(page, analysis)

        self.assertEqual("1,150", rendered["damage"])
        self.assertEqual("115", rendered["dps"])

    def test_incomplete_axis_keeps_observed_max_hp_settlement_without_overkill_correction(
        self,
    ) -> None:
        rendered: dict[str, str] = {}
        page = SimpleNamespace(
            _latest_summary=SimpleNamespace(total_damage=1_000.0, duration_seconds=10.0),
            _marginal_baseline_by_scope={},
            metric_labels={
                key: SimpleNamespace(
                    setText=lambda value, name=key: rendered.__setitem__(name, value)
                )
                for key in ("damage", "dps", "duration")
            },
            long_analysis_view=SimpleNamespace(set_analysis=lambda *_args, **_kwargs: None),
            marginal_page=SimpleNamespace(
                set_source_analysis=lambda _analysis, hit_details=None: None
            ),
        )
        analysis = SimpleNamespace(
            axis_complete=False,
            battle_end_us=10_000_000,
            timeline_damage_correction_total=250.0,
            timeline_max_hp_events=(
                SimpleNamespace(effective_hp_loss=500.0, included_in_effective_damage=True),
            ),
        )

        BattleReportPage.set_analysis(page, analysis)

        self.assertEqual("1,500", rendered["damage"])
        self.assertEqual("150", rendered["dps"])


if __name__ == "__main__":
    unittest.main()
