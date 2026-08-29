# 覆盖 Buff 区间索引与旧逐击筛选的逐字段等价边界。
from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
)
from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_buff_inference_service import BattleBuffInferenceService
from src.services.battle_buff_interval_index import BattleBuffIntervalIndex
from src.services.battle_hit_buff_projection_cache import (
    BattleHitBuffProjectionCache,
)


def _hit(
    time_us: int,
    *,
    character_id: int | None = 1001,
) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=f"hit:{time_us}:{character_id}",
        sequence=time_us,
        relative_time_us=time_us,
        character_id=character_id,
        character_name="角色",
        skill_name="技能",
        damage_name="伤害",
        damage_component="skill",
        attack_type="skill",
        damage_attribute="nature",
        target_id="target",
        target_name="目标",
        damage=1000.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
    )


def _interval(
    interval_id: str,
    *,
    start_us: int,
    end_us: int,
    target_scope: str,
    source_character_id: int = 1001,
) -> BattleInferredBuffInterval:
    return BattleInferredBuffInterval(
        interval_id=interval_id,
        buff_asset_path=f"/Game/Test/{interval_id}",
        buff_name=interval_id,
        source_effect_definition_id=f"test:{interval_id}",
        source_kind="test",
        source_character_id=source_character_id,
        source_character_name="来源",
        target_scope=target_scope,
        start_us=start_us,
        end_us=end_us,
        stacks=1,
        duration_policy="HasDuration",
        state_confidence="中",
        value_confidence="中",
        inference_basis="fixture",
        trigger_event_type="fixture",
        evidence_action_ids=(),
        evidence_event_ids=(),
        modifiers=(BattleBuffModifierEvidence(
            property_id="AtkUp",
            modifier_operation="EGameplayModOp::Additive",
            magnitude_kind="ScalableFloat",
            magnitude_value=0.1,
            calculation_asset_path="",
            value_confidence="中",
        ),),
    )


class BattleBuffIntervalIndexTests(unittest.TestCase):
    def test_index_matches_legacy_boundaries_scopes_order_and_duplicates(
        self,
    ) -> None:
        shared = _interval(
            "shared",
            start_us=10,
            end_us=20,
            target_scope="team",
        )
        intervals = (
            _interval("late", start_us=15, end_us=30, target_scope="self"),
            shared,
            _interval(
                "other",
                start_us=0,
                end_us=100,
                target_scope="team_others",
            ),
            _interval(
                "explicit",
                start_us=5,
                end_us=25,
                target_scope="character:1001",
                source_character_id=2002,
            ),
            _interval("target", start_us=0, end_us=12, target_scope="target"),
            _interval("unknown", start_us=0, end_us=100, target_scope="unknown"),
            _interval("zero", start_us=10, end_us=10, target_scope="team"),
            shared,
        )
        index = BattleBuffIntervalIndex(intervals)

        for time_us in (0, 9, 10, 11, 15, 19, 20, 24, 25, 30, 100):
            for character_id in (1001, 2002, None):
                hit = _hit(time_us, character_id=character_id)
                expected = BattleBuffInferenceService.active_for_hit(
                    intervals,
                    hit,
                )
                self.assertEqual(expected, index.active_for_hit(hit))
                self.assertEqual(
                    expected,
                    BattleBuffInferenceService.active_for_hit(index, hit),
                )

    def test_projection_accepts_index_and_explicit_prefiltered_intervals(
        self,
    ) -> None:
        hit = _hit(15)
        intervals = (
            _interval("active", start_us=10, end_us=20, target_scope="self"),
            _interval("expired", start_us=0, end_us=10, target_scope="self"),
        )
        index = BattleBuffIntervalIndex(intervals)
        expected = BattleBuffAttributeProjectionService.project_hit(
            hit,
            intervals,
        )

        self.assertEqual(
            expected,
            BattleBuffAttributeProjectionService.project_hit(hit, index),
        )
        self.assertEqual(
            expected,
            BattleBuffAttributeProjectionService.project_hit(
                hit,
                intervals,
                active_intervals=index.active_for_hit(hit),
            ),
        )

    def test_index_is_a_read_only_original_order_sequence(self) -> None:
        intervals = (
            _interval("b", start_us=10, end_us=20, target_scope="team"),
            _interval("a", start_us=0, end_us=30, target_scope="team"),
        )
        index = BattleBuffIntervalIndex(intervals)

        self.assertEqual(intervals, index.intervals)
        self.assertEqual(intervals, tuple(index))
        self.assertEqual(intervals[0], index[0])
        self.assertEqual(intervals, index[:])

    def test_temporal_adjuster_query_matches_full_interval_scan(self) -> None:
        # The indexed temporal subset must remain equivalent to the adjuster's
        # legacy full scan, including its half-open interval boundary.
        hit = replace(_hit(15), target_hp_before=25.0, target_max_hp=100.0)
        interval = _interval(
            "ordinary",
            start_us=10,
            end_us=20,
            target_scope="self",
        )
        index = BattleBuffIntervalIndex((interval,))

        self.assertEqual(
            BattleBuffAttributeProjectionService.project_hit(hit, (interval,)),
            BattleBuffAttributeProjectionService.project_hit(hit, index),
        )

    def test_excluded_view_matches_rebuilt_index_without_removed_intervals(
        self,
    ) -> None:
        intervals = (
            _interval("a", start_us=0, end_us=30, target_scope="team"),
            _interval("b", start_us=10, end_us=20, target_scope="self"),
            _interval("c", start_us=15, end_us=40, target_scope="target"),
        )
        source = BattleBuffIntervalIndex(intervals)
        view = source.excluding(frozenset({"b"}))
        rebuilt = BattleBuffIntervalIndex((intervals[0], intervals[2]))

        self.assertEqual((intervals[0], intervals[2]), view.intervals)
        for time_us in (0, 10, 15, 19, 20, 39, 40):
            hit = _hit(time_us)
            self.assertEqual(
                rebuilt.active_for_hit(hit),
                view.active_for_hit(hit),
            )
            self.assertEqual(
                rebuilt.temporal_for_hit(hit),
                view.temporal_for_hit(hit),
            )
            self.assertEqual(
                BattleBuffAttributeProjectionService.project_hit(hit, rebuilt),
                BattleBuffAttributeProjectionService.project_hit(hit, view),
            )

    def test_projection_cache_reuses_identical_hit_and_interval_semantics(self) -> None:
        index = BattleBuffIntervalIndex((
            _interval("a", start_us=0, end_us=30, target_scope="team"),
        ))
        cache = BattleHitBuffProjectionCache(index)
        first = _hit(10)
        second = _hit(20)

        with patch.object(
            BattleBuffAttributeProjectionService,
            "project_hit",
            wraps=BattleBuffAttributeProjectionService.project_hit,
        ) as project:
            first_projection = cache.project(first)
            second_projection = cache.project(second)

        project.assert_called_once()
        self.assertEqual(first.event_id, first_projection.event_id)
        self.assertEqual(second.event_id, second_projection.event_id)
        self.assertEqual(first_projection.modifiers, second_projection.modifiers)


if __name__ == "__main__":
    unittest.main()
