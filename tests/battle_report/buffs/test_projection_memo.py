# 验证请求内投影复用保持叠层、目标状态、供体身份与区间顺序。
from __future__ import annotations

NTE_TEST_TIER = "core"

from dataclasses import replace
import unittest
from unittest.mock import patch

from src.services.battle_report.buffs.attribute_projection import BattleBuffAttributeProjectionService
from src.services.battle_report.buffs.interval_index import BattleBuffIntervalIndex
from src.services.battle_report.buffs.projection_memo import BattleBuffProjectionMemo
from src.services.battle_report.replay.buff_cache import BattleHitBuffProjectionCache
from tests.battle_report.buffs.test_attribute_projection import _hit, _interval


class BattleBuffProjectionMemoTests(unittest.TestCase):
    def assert_projection(self, hit, intervals, memo):
        expected = BattleBuffAttributeProjectionService.project_hit(hit, intervals)
        actual = BattleHitBuffProjectionCache(BattleBuffIntervalIndex(intervals), memo=memo).project(hit)
        self.assertEqual(expected, actual)
        return actual

    def test_removal_reuses_rules_but_restores_older_stack_and_evidence(self):
        older = _interval("older", start_us=0, value=0.1, stacks=2, stack_limit_count=3)
        newer = _interval("newer", start_us=1_000_000, value=0.2, stacks=2, stack_limit_count=3)
        memo = BattleBuffProjectionMemo()
        both = self.assert_projection(_hit(), (older, newer), memo)
        self.assertEqual(0.5, both.modifiers[0].additive_value)
        removed = self.assert_projection(_hit(), (older,), memo)
        self.assertEqual(0.2, removed.modifiers[0].additive_value)
        self.assertEqual(("older",), removed.applied_interval_ids)
        self.assertEqual(2, memo.rule_evaluations)
        self.assertGreaterEqual(memo.rule_reuses, 1)


    def test_target_hp_and_unknown_state_are_not_merged(self):
        interval = _interval(
            "low-hp",
            start_us=0,
            property_id="DamageUpGeneralBase",
            application_requirement_asset_path="/Game/Condition/Con_Mint_Lv6",
        )
        memo = BattleBuffProjectionMemo()
        for hp in (39.0, 40.0, None, 39.0):
            result = self.assert_projection(
                replace(_hit(), target_hp_before=hp, target_max_hp=100.0), (interval,), memo
            )
            self.assertEqual(bool(result.modifiers), hp == 39.0)
        self.assertEqual(3, memo.rule_evaluations)
        self.assertGreaterEqual(memo.projection_reuses, 1)

    def test_boundaries_targets_donors_and_consumer_states_match_uncached(self):
        base = _interval("self", start_us=0)
        team = replace(base, interval_id="others", target_scope="team_others")
        target = _interval(
            "target", start_us=0, target_scope="target", property_id="DamageResistNatureAdd", value=-0.1, target_id="a"
        )
        follow = _interval(
            "follow", start_us=0, application_requirement_asset_path="battle-passive|follow-up-consumer=true"
        )
        weave = _interval("weave", start_us=0, application_requirement_asset_path="battle-passive|target-weave=true")
        intervals = (base, team, target, follow, weave)
        memo = BattleBuffProjectionMemo()
        for now in (-1, 0, 1, 4_999_999, 5_000_000):
            for character in (1072, 1004, None):
                for target_id in ("a", "b"):
                    for context, formal, layered in (
                        ("", False, False),
                        ("", True, False),
                        ("linko_coattack:fixture", False, False),
                        ("", True, True),
                    ):
                        hit = replace(
                            _hit(),
                            relative_time_us=now,
                            character_id=character,
                            target_id=target_id,
                            formula_context_kind=context,
                            is_formal_follow_up=formal,
                            is_follow_up=layered,
                            target_has_weave=formal,
                        )
                        self.assert_projection(hit, intervals, memo)
                        self.assert_projection(hit, intervals[1:], memo)

    def test_reanchor_preserves_observation_independence_and_interval_guard(self):
        interval = _interval("buff", start_us=0)
        index = BattleBuffIntervalIndex((interval,))
        cache = BattleHitBuffProjectionCache(index)
        first = cache.project(_hit())
        second = cache.project(replace(_hit(), event_id="second", damage=999.0, sequence=99))
        self.assertEqual(replace(first, event_id="second"), second)
        self.assertEqual(1, cache.memo.rule_evaluations)
        with self.assertRaises(ValueError):
            cache.require_intervals(BattleBuffIntervalIndex((interval,)))
        cache.require_intervals(index)

    def test_same_frozen_hit_and_query_skip_repeated_interval_search(self):
        interval = _interval("buff", start_us=0)
        index = BattleBuffIntervalIndex((interval,))
        hit = _hit()
        cache = BattleHitBuffProjectionCache(index)
        first = cache.project(hit)
        with patch.object(BattleBuffIntervalIndex, "temporal_for_hit", side_effect=AssertionError("repeated interval query")):
            self.assertIs(first, cache.project(hit))

    def test_single_candidate_retains_stack_minimum_cap_and_empty_limit_behavior(self):
        cases = (
            ("", 12, 1, 0.1),
            ("", 12, 3, 0.3),
            ("AggregateBySource", 12, 3, 0.3),
            ("AggregateBySource", 0, 3, 0.1),
            ("AggregateBySource", -2, 3, 0.1),
            ("AggregateBySource", 12, 0, None),
            ("AggregateBySource", 12, -1, None),
            ("", 12, 0, 0.1),
        )
        for stacking_type, stacks, limit, expected in cases:
            with self.subTest(stacking_type=stacking_type, stacks=stacks, limit=limit):
                interval = replace(_interval("single", start_us=0, value=0.1), stacking_type=stacking_type, stacks=stacks, stack_limit_count=limit)
                actual = self.assert_projection(_hit(), (interval,), BattleBuffProjectionMemo())
                if expected is None:
                    self.assertEqual((), actual.modifiers)
                    self.assertEqual("not_applied", actual.decisions[0].status)
                else:
                    self.assertAlmostEqual(expected, actual.modifiers[0].additive_value)
                    self.assertEqual(("single",), actual.applied_interval_ids)
                    self.assertEqual("applied", actual.decisions[0].status)

    def test_equal_independent_interval_objects_share_complete_value_token(self):
        first = _interval("same", start_us=0, value=0.1)
        equal_copy = replace(first, modifiers=tuple(replace(row) for row in first.modifiers))
        self.assertIsNot(first, equal_copy)
        memo = BattleBuffProjectionMemo()
        self.assertEqual(memo.interval_token(first), memo.interval_token(equal_copy))
        original = self.assert_projection(_hit(), (first,), memo)
        copied = self.assert_projection(replace(_hit(), event_id="copied"), (equal_copy,), memo)
        self.assertEqual(replace(original, event_id="copied"), copied)
        self.assertEqual(1, memo.rule_evaluations)
        self.assertEqual(1, memo.projection_reuses)

    def test_full_interval_evidence_and_value_changes_do_not_alias(self):
        first = _interval("same", start_us=0, value=0.1)
        variants = (
            replace(first, modifiers=(replace(first.modifiers[0], magnitude_value=0.2),)),
            replace(first, state_confidence="高"),
            replace(first, evidence_event_ids=("other-evidence",)),
            replace(first, source_character_id=1004),
            replace(first, stack_limit_count=3),
            replace(first, end_us=4_000_000),
        )
        memo = BattleBuffProjectionMemo()
        original_token = memo.interval_token(first)
        for variant in variants:
            self.assertNotEqual(original_token, memo.interval_token(variant))
            self.assert_projection(_hit(), (variant,), memo)
        self.assertEqual(len(variants), len({memo.interval_token(row) for row in variants}))

    def test_equal_object_interning_preserves_duplicate_slots_and_tie_order(self):
        first = replace(_interval("same", start_us=0, value=0.1), stacking_type="")
        equal_copy = replace(first)
        changed = replace(first, modifiers=(replace(first.modifiers[0], magnitude_value=0.2),))
        memo = BattleBuffProjectionMemo()
        duplicated = self.assert_projection(_hit(), (first, equal_copy), memo)
        self.assertEqual(2, len(duplicated.decisions))
        forward = self.assert_projection(_hit(), (equal_copy, changed), memo)
        backward = self.assert_projection(_hit(), (changed, equal_copy), memo)
        self.assertEqual(0.1, forward.modifiers[0].additive_value)
        self.assertEqual(0.2, backward.modifiers[0].additive_value)
