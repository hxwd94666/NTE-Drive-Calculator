# 验证多配装候选共享投影时仍保留各自数值与未量化边界。
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from src.domain.battle_report import BattleBuffModifierEvidence, BattleCharacterBaseline, BattleCharacterStat
from src.services.battle_buff_projection_memo import BattleBuffProjectionMemo
from src.services.battle_build_counterfactual_service import BattleBuildCounterfactualService
from src.services.battle_marginal_candidate_service import BattleMarginalCandidateService
from src.services.battle_report_analysis_load_service import BattleReportAnalysisLoadRequest, BattleReportAnalysisLoadService
from tests.test_battle_build_counterfactual_service import _buff_interval, _hit, _replay, _snapshot


class BattleBuildProjectionReuseTests(unittest.TestCase):
    def test_candidates_with_equal_labels_keep_values_and_partial_evidence(self):
        hit = _hit("hit1", character_id=1, character_name="甲", skill_name="技能甲", damage=100)
        baseline = BattleCharacterBaseline(
            1, "甲", "fixture", (BattleCharacterStat("AtkBase", "攻击力", 100, False),),
        )
        replay = replace(
            _replay("hit1", 100), non_critical_damage=None, selected_damage=None,
            expected_damage=None, critical_state="unreplayable",
        )
        original = _snapshot(hits=(hit,), baselines=(baseline,), replays=(replay,))
        memo = BattleBuffProjectionMemo()
        for value in (0.1, 0.2, 0.1):
            with self.subTest(value=value):
                modifier = BattleBuffModifierEvidence(
                    property_id="DamageUpGeneralBase", modifier_operation="EGameplayModOp::Additive",
                    magnitude_kind="ScalableFloat", magnitude_value=value,
                    calculation_asset_path="", value_confidence="高",
                )
                candidate = replace(original, buff_intervals=(_buff_interval((modifier,)),))
                result = BattleBuildCounterfactualService.compare(
                    original=original, candidate=candidate, projection_memo=memo,
                )
                independent = BattleBuildCounterfactualService.compare(original=original, candidate=candidate)
                self.assertEqual(independent, result)
                self.assertEqual("partial", result.hits[0].quantification.status)
                self.assertIsNone(result.hits[0].candidate_damage)
                self.assertAlmostEqual(100 * (1 + value), result.hits[0].known_projection_damage)
        self.assertGreater(memo.projection_reuses, 0)

    def test_page_requests_share_only_their_own_variant_projection_memo(self):
        analysis = _snapshot(hits=(), baselines=(), replays=())
        candidate = BattleMarginalCandidateService.freeze(7, [{"character_id": 1}], equipment_editable=True)
        request = BattleReportAnalysisLoadRequest(
            battle_record_id=7, detail_level="marginal",
            marginal_benefit_candidate=candidate, selected_character_id=1,
        )
        history = Mock(native_page_loader=None)
        history.new_projection_memo.side_effect = lambda **_kwargs: BattleBuffProjectionMemo()
        history.load_analysis.return_value = analysis
        request_memos = []

        def benefits(**kwargs):
            memo = kwargs["projection_memo"]
            request_memos.append(memo)
            for _ in range(2):
                kwargs["load_variant"](candidate)
                self.assertIs(memo, history.load_analysis.call_args.kwargs["projection_memo"])
            return "benefits"

        with (
            patch.object(BattleReportAnalysisLoadService, "_materialize_marginal_baseline", return_value=analysis),
            patch("src.services.battle_report_analysis_load_service.BattleMarginalBenefitService.calculate", side_effect=benefits),
        ):
            for _ in range(2):
                self.assertEqual("benefits", BattleReportAnalysisLoadService.load_legacy_for_differential(history, request).marginal_benefits)
        self.assertIsNot(request_memos[0], request_memos[1])
