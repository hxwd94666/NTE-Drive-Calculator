# 验证逐击公式首轮暴击结论只转换一次为糖果骑士触发证据。
from __future__ import annotations

from dataclasses import replace
import unittest

from src.domain.battle_report import BattleAnalysisHit, BattleHitReplayResult
from src.services.battle_fork_critical_inference_service import (
    BattleForkCriticalInferenceService,
)


def _hit(event_id: str, character_id: int | None) -> BattleAnalysisHit:
    return BattleAnalysisHit(
        event_id=event_id,
        sequence=1,
        relative_time_us=1_000_000,
        character_id=character_id,
        character_name="角色",
        skill_name="技能",
        damage_name="伤害",
        damage_component="skill",
        attack_type="普攻",
        damage_attribute="cosmos",
        target_id="target",
        target_name="目标",
        damage=1000.0,
        direction="outgoing",
        is_follow_up=False,
        classification="direct",
        ability_id="GA_Test",
        gameplay_effect_id="GE_Test",
    )


def _replay(event_id: str, state: str) -> BattleHitReplayResult:
    return BattleHitReplayResult(
        event_id=event_id,
        observed_damage=1000.0,
        non_critical_damage=800.0,
        critical_damage=1000.0,
        selected_damage=1000.0,
        selected_error_percent=0.0,
        critical_state=state,
        confidence="中",
        factors=(),
    )


class BattleForkCriticalInferenceServiceTests(unittest.TestCase):
    def test_only_critical_replay_with_known_source_becomes_trigger(self) -> None:
        hits = (_hit("crit", 1001), _hit("normal", 1001), _hit("unknown", None))
        replays = (
            _replay("crit", "critical"),
            _replay("normal", "non_critical"),
            _replay("unknown", "critical"),
            replace(_replay("missing", "critical"), observed_damage=50.0),
        )

        events = BattleForkCriticalInferenceService.infer(hits, replays)

        self.assertEqual(1, len(events))
        self.assertEqual("crit", events[0].event_id)
        self.assertEqual(1001, events[0].source_character_id)
        self.assertEqual("hit_replay_inferred", events[0].evidence_kind)

    def test_non_knight_rules_do_not_request_a_second_pass(self) -> None:
        rule = type("Rule", (), {
            "source_effect_definition_id": "fork_star:other:1"
        })()

        events = BattleForkCriticalInferenceService.infer(
            (_hit("crit", 1001),),
            (_replay("crit", "critical"),),
            (rule,),
        )

        self.assertEqual((), events)


if __name__ == "__main__":
    unittest.main()
