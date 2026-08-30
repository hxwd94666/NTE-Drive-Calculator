# 验证完整遭遇证据以一对一 HP 相容图缩小环境候选并稳定选择默认。
from __future__ import annotations

import unittest

from src.domain.battle_encounter import (
    BattleEncounterCandidate,
    BattleEncounterTargetPreset,
)
from src.services.battle_encounter_candidate_selection_service import (
    BattleEncounterCandidateSelectionService,
)


def _target(
    monster_id: str,
    hp: float,
    *,
    count: int = 1,
    defense: float = 1000.0,
) -> BattleEncounterTargetPreset:
    return BattleEncounterTargetPreset(
        target_id=monster_id,
        target_name=monster_id,
        monster_class_path=monster_id,
        monster_count=count,
        max_hp=hp,
        monster_level=90.0,
        profile_set="fixture",
        pack_id=monster_id,
        defense_base=defense,
        defense_up=0.0,
        defense_add=0.0,
        topple_limit=50.0,
        resistances=(("chaos", 0.2),),
    )


def _candidate(
    environment_ref: str,
    *targets: BattleEncounterTargetPreset,
    order: int,
    half: str = "",
) -> BattleEncounterCandidate:
    return BattleEncounterCandidate(
        environment_kind="outer_realm" if half else "clone",
        environment_ref=environment_ref,
        environment_name=environment_ref,
        scope_half=half,
        outer_realm_floor=10 if half else None,
        difficulty_id=None,
        feast_options=(),
        targets=targets,
        catalog_order=order,
    )


def _evidence(*rows: tuple[str, str, float, float, str]) -> dict:
    return {"hits": [
        {
            "direction": "outgoing",
            "abyss_half": half,
            "target_id": target_id,
            "target_max_hp": hp,
            "max_hp_reduction": reduction,
            "target_monster_id": monster_id,
            "relative_time_us": index,
        }
        for index, (half, target_id, hp, reduction, monster_id) in enumerate(rows)
    ]}


class BattleEncounterCandidateSelectionServiceTests(unittest.TestCase):
    def test_partial_observation_keeps_candidate_with_unobserved_slots(self) -> None:
        observed = BattleEncounterCandidateSelectionService.observe(
            _evidence(("upper", "a", 1000.0, 0.0, "")),
            combat_context_kind="abyss",
        )
        candidate = _candidate(
            "env-extra",
            _target("mon_001", 1000.0),
            _target("mon_002", 2000.0),
            order=0,
            half="upper",
        )

        matches = BattleEncounterCandidateSelectionService.strict_matches(
            observed,
            (candidate,),
        )

        self.assertEqual(("env-extra",), tuple(
            row.candidate.environment_ref for row in matches
        ))
        self.assertEqual(1, matches[0].unobserved_slot_count)

    def test_duplicate_hp_observations_consume_two_static_slots(self) -> None:
        observed = BattleEncounterCandidateSelectionService.observe(
            _evidence(
                ("", "a", 5000.0, 0.0, ""),
                ("", "b", 5000.0, 0.0, ""),
            ),
            combat_context_kind="non_abyss",
        )
        one_slot = _candidate("one-slot", _target("mon_001", 5000.0), order=0)
        two_slots = _candidate(
            "two-slots",
            _target("mon_001", 5000.0, count=2),
            order=1,
        )

        matches = BattleEncounterCandidateSelectionService.strict_matches(
            observed,
            (one_slot, two_slots),
        )

        self.assertEqual(("two-slots",), tuple(
            row.candidate.environment_ref for row in matches
        ))

    def test_any_unmatched_target_eliminates_environment(self) -> None:
        observed = BattleEncounterCandidateSelectionService.observe(
            _evidence(
                ("", "a", 1000.0, 0.0, ""),
                ("", "b", 3000.0, 0.0, ""),
            ),
            combat_context_kind="non_abyss",
        )
        candidate = _candidate(
            "missing-target",
            _target("mon_001", 1000.0),
            _target("mon_002", 2000.0),
            order=0,
        )

        self.assertEqual((), BattleEncounterCandidateSelectionService.strict_matches(
            observed,
            (candidate,),
        ))

    def test_large_hp_uses_small_absolute_tolerance(self) -> None:
        observed = BattleEncounterCandidateSelectionService.observe(
            _evidence(("", "a", 10_000_002.0, 0.0, "")),
            combat_context_kind="non_abyss",
        )
        candidate = _candidate(
            "ten-million",
            _target("boss_001", 10_000_000.0),
            order=0,
        )

        self.assertEqual((), BattleEncounterCandidateSelectionService.strict_matches(
            observed,
            (candidate,),
        ))

    def test_core_v4_reduction_delta_does_not_raise_initial_hp(self) -> None:
        observed = BattleEncounterCandidateSelectionService.observe(
            _evidence(
                ("lower", "a", 10_000.0, 0.0, "boss_001"),
                ("lower", "a", 9000.0, 1500.0, "boss_001"),
            ),
            combat_context_kind="non_abyss",
        )

        self.assertEqual(1, len(observed))
        self.assertEqual("", observed[0].scope_half)
        self.assertEqual(10_000.0, observed[0].initial_max_hp)

    def test_multiple_candidates_choose_stable_default_and_keep_alternatives(self) -> None:
        observed = BattleEncounterCandidateSelectionService.observe(
            _evidence(("", "a", 1000.0, 0.0, "")),
            combat_context_kind="non_abyss",
        )
        formal_first = _candidate(
            "environment-b",
            _target("mon_001", 1000.0),
            order=2,
        )
        fewer_unobserved = _candidate(
            "environment-z",
            _target("mon_002", 1000.0),
            order=9,
        )
        extra_slot = _candidate(
            "environment-a",
            _target("mon_003", 1000.0),
            _target("mon_004", 2000.0),
            order=0,
        )

        first = BattleEncounterCandidateSelectionService.select_default(
            BattleEncounterCandidateSelectionService.strict_matches(
                observed,
                (extra_slot, formal_first, fewer_unobserved),
            )
        )
        reversed_input = BattleEncounterCandidateSelectionService.select_default(
            BattleEncounterCandidateSelectionService.strict_matches(
                observed,
                (fewer_unobserved, formal_first, extra_slot),
            )
        )

        assert first is not None and reversed_input is not None
        self.assertEqual("environment-b", first.default.candidate.environment_ref)
        self.assertEqual(
            first.default.candidate.environment_ref,
            reversed_input.default.candidate.environment_ref,
        )
        self.assertEqual(
            ("environment-z", "environment-a"),
            tuple(row.candidate.environment_ref for row in first.alternatives),
        )
        self.assertEqual("ambiguous_default", first.selection_mode)
        self.assertEqual("低", first.confidence)


if __name__ == "__main__":
    unittest.main()
