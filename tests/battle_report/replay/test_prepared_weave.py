# 验证预投影沿用正式覆纹前置击角色，角色声明不能补造来源。
NTE_TEST_TIER = "core"
from dataclasses import replace
import unittest

from tests.test_native_direct_replay import frozen_fixture
from tests.battle_report.buffs.test_attribute_projection import _interval
from src.domain.battle_report.build.inputs import BattleCharacterStat
from src.services.battle_report.replay.preparation import BattleHitProjectionPreparationService
from src.services.battle_report.replay.service import BattleHitReplayService
from src.services.battle_report.buffs.candidate_projection import PreparedBuffCandidateProjection
from src.services.battle_report.buffs.interval_index import BattleBuffIntervalIndex
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.damage.weave_source import BattleWeaveSourceIndex


def fixture(*, same_character=False, paired=True):
    analysis, _ = frozen_fixture()
    source = replace(analysis.hits[0], character_id=1072, relative_time_us=1_000_000)
    weave = replace(source, event_id="weave", character_id=1072 if same_character else 1036,
                    sequence=source.sequence if paired else 2, classification="weave",
                    is_follow_up=True, damage_component="follow_up", skill_name="覆纹",
                    damage_name="覆纹", attack_type="覆纹", damage=50.0)
    baseline = replace(analysis.baselines[0], character_id=1072,
                       stats=(BattleCharacterStat("MagBase", "环合强度", 360.0, False),))
    other = replace(baseline, character_id=1036)
    interval = _interval("source-ring", start_us=0, property_id="MagBase", value=100.0)
    analysis.hits = source, weave
    analysis.baselines = baseline, other
    analysis.buff_intervals = interval,
    return analysis, source, weave, interval


class WeavePreparedProjectionTests(unittest.TestCase):
    def test_cross_character_preparation_matches_direct_replay_and_does_not_mutate_hits(self):
        analysis, source, weave, _ = fixture()
        prepared = BattleHitProjectionPreparationService.prepare(analysis, ())
        projection = prepared.formula_by_event[weave.event_id]
        self.assertEqual([100.0], [row.additive_value for row in projection.modifiers
                                 if row.property_id == "MagBase"])
        self.assertNotIn(weave.event_id, prepared.beneficiary_by_event)
        direct = BattleHitReplayService.replay(analysis, (), apply_observed_refinements=False)
        cached = BattleHitReplayService.replay(analysis, (), apply_observed_refinements=False,
                                              projection_by_event=prepared.formula_by_event)
        self.assertEqual(direct, cached)
        strength = next(f for f in cached[1].factors if f.factor_id == "weave_strength")
        self.assertIn("460", strength.evidence_basis)
        self.assertEqual(1036, weave.character_id)
        self.assertEqual((source, weave), analysis.hits)

    def test_same_character_source_keeps_existing_projection_and_beneficiary_alias(self):
        analysis, _, weave, _ = fixture(same_character=True)
        prepared = BattleHitProjectionPreparationService.prepare(analysis, ())
        self.assertIn(weave.event_id, prepared.beneficiary_by_event)
        self.assertEqual([100.0], [r.additive_value for r in prepared.formula_by_event[weave.event_id].modifiers])

    def test_no_formal_pair_keeps_declared_role_without_borrowing_source_buff(self):
        analysis, _, weave, _ = fixture(paired=False)
        prepared = BattleHitProjectionPreparationService.prepare(analysis, ())
        self.assertEqual((), prepared.formula_by_event[weave.event_id].modifiers)
        self.assertIn(weave.event_id, prepared.beneficiary_by_event)
        replay = BattleHitReplayService.replay(analysis, (), apply_observed_refinements=False,
                                              projection_by_event=prepared.formula_by_event)
        self.assertIsNone(replay[1].expected_damage)

    def test_candidate_without_prebuilt_batch_uses_full_axis_for_pairing(self):
        analysis, source, weave, interval = fixture()
        pair = PreparedBuffCandidateProjection.create(
            interval_index=BattleBuffIntervalIndex(analysis.buff_intervals),
            group_intervals=(), active_hits=(weave,), memo=BattleBuffProjectionMemo(),
            evidence_by_event={}, weave_sources=BattleWeaveSourceIndex(analysis.hits),
        )
        self.assertEqual(source.character_id, pair.formula_hits[weave.event_id].character_id)
        projected = pair.candidate_cache.project(pair.formula_hits[weave.event_id])
        self.assertIn(interval.interval_id, projected.applied_interval_ids)

    def test_duplicate_formal_sources_keep_the_first_axis_entry(self):
        analysis, source, weave, _ = fixture()
        later = replace(source, event_id="later", character_id=1036)
        analysis.hits = source, later, weave
        prepared = BattleHitProjectionPreparationService.prepare(analysis, ())
        self.assertEqual([100.0], [r.additive_value for r in prepared.formula_by_event[weave.event_id].modifiers])
