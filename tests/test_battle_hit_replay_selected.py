# 验证逐击子集重放只消费已完成全轴审计冻结的安全分支。
from __future__ import annotations

import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

from src.domain.battle_report import (
    BattleHitReplayResult,
    BattleInferredBuffInterval,
    BattleSkillDamageEvidence,
    BattleTargetCondition,
)
from src.services.battle_hit_replay_audit_service import BattleHitReplayAuditService
from src.services.battle_hit_local_crit_inference_service import (
    BattleHitLocalCritInferenceService,
)
from src.services.battle_hit_replay_service import BattleHitReplayService
from src.services.battle_selected_hit_replay_context import (
    PreparedReplayAuditContext,
    PreparedReplayAuditInputs,
)
from src.services.battle_target_instance_mapping_service import (
    BattleTargetInstanceMappingService,
)
from src.services.battle_topple_hit_replay_service import (
    BattleToppleHitReplayService,
)


@dataclass(frozen=True)
class _ReplayAnalysis:
    hits: tuple[object, ...]
    baselines: tuple[object, ...] = ()
    buff_intervals: tuple[object, ...] = ()
    target_condition: BattleTargetCondition | None = None


def _hit(event_id: str, *, classification: str = "direct") -> SimpleNamespace:
    return SimpleNamespace(
        event_id=event_id,
        direction="outgoing",
        classification=classification,
        is_follow_up=False,
        gameplay_effect_id="",
        ability_id="",
        attack_type="普攻",
        damage_component="skill",
        damage_attribute="chaos",
        damage_name="普攻",
        skill_name="普攻",
        damage=100.0,
        character_id=1,
        character_name="角色",
        relative_time_us=1,
        scope_half="upper",
        target_id="target",
        target_name="目标",
        raw_damage=None,
        damage_correction_kind="",
        damage_correction_basis="",
    )


def _replay(
    event_id: str,
    *,
    state: str = "non_critical",
    policy: str = "character",
) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id=event_id,
        observed_damage=100.0,
        non_critical_damage=100.0,
        critical_damage=150.0,
        selected_damage=100.0,
        selected_error_percent=0.0,
        critical_state=state,
        confidence="高",
        factors=(),
        critical_policy=policy,
        expected_damage=110.0,
    )


def _interval(interval_id: str) -> BattleInferredBuffInterval:
    return BattleInferredBuffInterval(
        interval_id=interval_id,
        buff_asset_path=f"/Game/{interval_id}",
        buff_name=interval_id,
        source_effect_definition_id=interval_id,
        source_kind="fixture",
        source_character_id=1,
        source_character_name="角色",
        target_scope="self",
        start_us=0,
        end_us=10,
        stacks=1,
        duration_policy="HasDuration",
        state_confidence="中",
        value_confidence="中",
        inference_basis="fixture",
        trigger_event_type="fixture",
        evidence_action_ids=(),
        evidence_event_ids=(),
        modifiers=(),
    )


def _evidence(event_id: str, *, state_label: str = "") -> BattleSkillDamageEvidence:
    return BattleSkillDamageEvidence(
        event_id=event_id,
        damage_id="damage",
        ability_id="ability",
        damage_attribute="chaos",
        damage_source_category="skill",
        fixed_crit_rate=0.0,
        scaling_property_id="Atk",
        scaling_multiplier=1.0,
        multiplier_coefficient=1.0,
        effective_skill_level=1,
        evidence_basis="fixture",
        state_multiplier=2.0 if state_label else 1.0,
        state_multiplier_label=state_label,
    )


class BattleSelectedHitReplayTests(unittest.TestCase):
    @staticmethod
    def _condition() -> BattleTargetCondition:
        return BattleTargetCondition(
            target_name="目标",
            enemy_level=80.0,
            scene="outer_realm",
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=(("chaos", 0.2),),
        )

    def test_default_replay_still_visits_every_outgoing_hit(self) -> None:
        analysis = SimpleNamespace(
            hits=(_hit("a"), _hit("b")),
            baselines=(),
        )

        results = BattleHitReplayService.replay(
            analysis,
            (),
            apply_observed_refinements=False,
        )

        self.assertEqual(["a", "b"], [row.event_id for row in results])

    def test_prepared_context_limits_replay_to_selected_events(self) -> None:
        analysis = SimpleNamespace(
            hits=(_hit("a"), _hit("b")),
            baselines=(),
        )
        context = PreparedReplayAuditContext.prepare(
            analysis,
            (),
            (_replay("a", state="unreplayable"), _replay("b")),
            {"a"},
        )

        with (
            patch.object(
                BattleHitLocalCritInferenceService,
                "apply",
                side_effect=AssertionError("selected replay must keep frozen branch"),
            ),
            patch.object(
                BattleHitReplayAuditService,
                "postprocess",
                side_effect=AssertionError("selected replay must skip a new audit"),
            ),
        ):
            results = BattleHitReplayService.replay(
                analysis,
                (),
                prepared_audit_context=context,
            )

        self.assertFalse(context.requires_full_axis)
        self.assertEqual(["a"], [row.event_id for row in results])
        self.assertEqual("unreplayable", results[0].critical_state)
        self.assertIsNone(results[0].selected_damage)

    def test_candidate_uses_critical_branch_frozen_by_full_axis_baseline(self) -> None:
        analysis = SimpleNamespace(hits=(_hit("a"),), baselines=())
        context = PreparedReplayAuditContext.prepare(
            analysis,
            (_evidence("a"),),
            (_replay("a", state="critical"),),
            {"a"},
        )
        candidate = _replay("a", state="non_critical")

        (frozen,) = context.freeze_candidate_branches((candidate,))

        self.assertEqual("critical", frozen.critical_state)
        self.assertEqual(150.0, frozen.selected_damage)
        self.assertAlmostEqual(50.0, frozen.selected_error_percent)

    def test_stateful_evidence_requires_full_axis(self) -> None:
        analysis = SimpleNamespace(hits=(_hit("a"),), baselines=())
        context = PreparedReplayAuditContext.prepare(
            analysis,
            (_evidence("a", state_label="正式状态层数"),),
            (_replay("a"),),
            {"a"},
        )

        self.assertTrue(context.requires_full_axis)
        self.assertIn("stateful_skill_evidence:a", context.full_axis_reasons)
        with self.assertRaisesRegex(ValueError, "requires full axis"):
            BattleHitReplayService.replay(
                analysis,
                (_evidence("a", state_label="正式状态层数"),),
                prepared_audit_context=context,
            )

    def test_special_formula_family_requires_full_axis(self) -> None:
        analysis = SimpleNamespace(hits=(_hit("a", classification="dot"),), baselines=())
        context = PreparedReplayAuditContext.prepare(
            analysis,
            (),
            (_replay("a"),),
            {"a"},
        )

        self.assertTrue(context.requires_full_axis)
        self.assertIn(
            "stateful_or_unsupported_channel:a:dot",
            context.full_axis_reasons,
        )

    def test_target_route_cache_reuses_one_mapping_for_same_target(self) -> None:
        analysis = SimpleNamespace(
            hits=(_hit("a"), _hit("b")),
            baselines=(),
            target_condition=self._condition(),
        )

        with patch.object(
            BattleTargetInstanceMappingService,
            "analysis_for_hit",
            return_value=analysis,
        ) as route:
            BattleHitReplayService.replay(
                analysis,
                (),
                apply_observed_refinements=False,
            )

        route.assert_called_once()

    def test_prepared_target_route_keeps_current_candidate_intervals(self) -> None:
        hit = _hit("topple", classification="topple")
        condition = self._condition()
        original = _ReplayAnalysis(
            hits=(hit,),
            baselines=(),
            buff_intervals=(_interval("original-buff"),),
            target_condition=condition,
        )
        candidate = _ReplayAnalysis(
            hits=(hit,),
            baselines=(),
            buff_intervals=(_interval("candidate-without-buff"),),
            target_condition=condition,
        )
        inputs = PreparedReplayAuditInputs.prepare(
            original,
            (),
            (_replay("topple"),),
        )
        seen = []

        def replay_topple(*, analysis, **_kwargs):
            seen.append((
                tuple(row.interval_id for row in analysis.buff_intervals),
                analysis.target_condition,
            ))
            return _replay("topple")

        with patch.object(
            BattleToppleHitReplayService,
            "replay",
            side_effect=replay_topple,
        ):
            BattleHitReplayService.replay(
                candidate,
                (),
                prepared_audit_inputs=inputs,
                apply_observed_refinements=False,
            )

        self.assertEqual([(("candidate-without-buff",), condition)], seen)


if __name__ == "__main__":
    unittest.main()
