# 验证精确运行时证据只生成非持久化养成事实。
"""Verify exact runtime evidence creates non-persisted cultivation facts."""

from __future__ import annotations

import unittest
from copy import deepcopy

from src.services.battle_inferred_character_fact_service import (
    BattleInferredCharacterFactService,
)


class BattleInferredCharacterFactServiceTests(unittest.TestCase):
    def test_exact_outgoing_lacrimosa_lv6_enables_effect5_in_memory(self) -> None:
        evidence = {
            "hits": [
                {
                    "sequence_text": "18",
                    "direction": "outgoing",
                    "character_id": 1004,
                    "gameplay_effect_name": (
                        "GE_Player_Lacrimosa_Blood_Damage_LV6"
                    ),
                }
            ]
        }
        frozen = {
            "characters": [
                {
                    "character_id": 1004,
                    "profile": {
                        "selected_awaken_effect_ids": [],
                        "awakening_selection_initialized": True,
                    },
                }
            ]
        }
        original = deepcopy(frozen)

        facts = BattleInferredCharacterFactService.infer(evidence)
        effective = deepcopy(frozen)
        BattleInferredCharacterFactService.apply_to_build(effective, facts)

        self.assertEqual(frozen, original)
        self.assertEqual("Effect5", facts[0].fact_value)
        self.assertEqual(("18:primary",), facts[0].evidence_event_ids)
        profile = effective["characters"][0]["profile"]
        self.assertEqual(["Effect5"], profile["selected_awaken_effect_ids"])
        self.assertEqual(["Effect5"], profile["inferred_awaken_effect_ids"])

    def test_non_lv6_or_disabled_fact_does_not_enable_effect5(self) -> None:
        ordinary = {
            "hits": [
                {
                    "sequence_text": "1",
                    "direction": "outgoing",
                    "character_id": 1004,
                    "gameplay_effect_name": "GE_Player_Lacrimosa_Blood_Damage",
                }
            ]
        }
        self.assertEqual((), BattleInferredCharacterFactService.infer(ordinary))

        lv6 = deepcopy(ordinary)
        lv6["hits"][0]["gameplay_effect_name"] = (
            "GE_Player_Lacrimosa_Blood_Damage_LV6"
        )
        facts = BattleInferredCharacterFactService.infer(lv6)
        build = {
            "characters": [
                {
                    "character_id": 1004,
                    "profile": {"selected_awaken_effect_ids": []},
                }
            ]
        }
        BattleInferredCharacterFactService.apply_to_build(
            build,
            facts,
            disabled_fact_ids={facts[0].fact_id},
        )
        self.assertEqual([], build["characters"][0]["profile"][
            "selected_awaken_effect_ids"
        ])

    def test_only_facts_missing_from_effective_profiles_are_applicable(self) -> None:
        evidence = {
            "hits": [
                {
                    "sequence_text": "18",
                    "direction": "outgoing",
                    "character_id": 1004,
                    "gameplay_effect_name": (
                        "GE_Player_Lacrimosa_Blood_Damage_LV6"
                    ),
                }
            ]
        }
        facts = BattleInferredCharacterFactService.infer(evidence)

        missing = BattleInferredCharacterFactService.applicable_to_profiles(
            [{"character_id": 1004, "selected_awaken_effect_ids": []}],
            facts,
        )
        explicit = BattleInferredCharacterFactService.applicable_to_profiles(
            [{"character_id": 1004, "selected_awaken_effect_ids": ["Effect5"]}],
            facts,
        )

        self.assertEqual(facts, missing)
        self.assertEqual((), explicit)


if __name__ == "__main__":
    unittest.main()
