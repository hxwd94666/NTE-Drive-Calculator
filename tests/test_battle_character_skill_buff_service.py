# 验证角色技能固定攻击 Buff 的来源口径、技能等级和生效时点。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleInferredAction, BattleBuffModifierEvidence
from src.services.battle_buff_inference_service import (
    BattleBuffInferenceService,
    BattleStaticBuffRule,
)
from src.services.battle_character_skill_buff_service import (
    BattleCharacterSkillBuffService,
)


class _Dao:
    @staticmethod
    def get_combat_curve(_table: str, curve_id: str):
        values = {
            "Sagiri_QDamUp_AtkAdd": (0.30,),
            "Sagiri_QDamUp_AtkAddDur": (20.0,),
            "Haniel_Skill_AtkAdd": tuple(0.10 + index * 0.01 for index in range(13)),
            "Haniel_UltraSkill_AtkAdd": tuple(
                0.10 + index * 0.01 for index in range(13)
            ),
            "Haniel_Skill_ActorDuaration": (8.0, 8.0),
            "Haniel_UltraSkillDuaration": (10.0, 10.0),
        }[curve_id]
        return {"points": tuple({"value": value} for value in values)}

    @staticmethod
    def list_character_awaken_effects(_character_id: int):
        return ()


def _character(character_id: int, name: str, effects: tuple[str, ...], skills):
    return {
        "character_id": character_id,
        "observed_name": name,
        "awakening_level": len(effects),
        "profile": {
            "awakening_selection_initialized": True,
            "selected_awaken_effect_ids": list(effects),
        },
        "skills": [
            {"skill_id": skill_id, "skill_level": level}
            for skill_id, level in skills
        ],
        "stats": [
            {"source_group": "character", "property_id": "AtkBase", "value": 600},
            {"source_group": "fork", "property_id": "AtkBase", "value": 400},
            {"source_group": "equipment", "property_id": "AtkBase", "value": 999},
        ],
    }


def _action(character_id: int, name: str, kind: str, start: int, end: int):
    return BattleInferredAction(
        action_id=f"{character_id}:{kind}:{start}",
        character_id=character_id,
        character_name=name,
        action_name=kind,
        input_kind=kind,
        input_sequence="1",
        start_us=start,
        end_us=end,
        hits=1,
        damage=100.0,
        identity_confidence="高",
        timing_confidence="中",
        inference_basis="fixture",
        evidence_event_ids=(f"hit:{character_id}:{kind}",),
        gameplay_effect_ids=(),
    )


class BattleCharacterSkillBuffServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.build = {"characters": [
            _character(
                1003,
                "早雾",
                ("Effect1", "Effect2", "Effect3", "Effect4"),
                (("GA_Sagiri_UltraSkill", 10),),
            ),
            _character(
                1020,
                "哈尼娅",
                ("Effect1", "Effect2"),
                (("GA_Haniel_Skill", 3), ("GA_Haniel_UltraSkill", 2)),
            ),
        ]}

    def _rules(self):
        return BattleCharacterSkillBuffService.load_rules(
            _Dao(), self.build, BattleStaticBuffRule, BattleBuffModifierEvidence
        )

    def test_values_use_only_character_and_fork_base_attack(self) -> None:
        rules = {row.rule_id: row for row in self._rules()}

        self.assertEqual(300.0, rules["character-skill:1003:q-team-atk"].modifiers[0].magnitude_value)
        self.assertEqual(
            300.0,
            rules["character-awaken:1003:effect4-team-atk"].modifiers[0].magnitude_value,
        )
        self.assertAlmostEqual(
            120.0,
            rules["character-skill:1020:e-team-atk"].modifiers[0].magnitude_value,
        )
        self.assertEqual(110.0, rules["character-skill:1020:q-team-atk"].modifiers[0].magnitude_value)
        self.assertEqual(12.0, rules["character-skill:1020:e-team-atk"].duration_seconds)

    def test_sagiri_starts_after_q_end_and_haniel_uses_montage_offsets(self) -> None:
        intervals = BattleBuffInferenceService.infer(
            self._rules(),
            actions=(
                _action(1003, "早雾", "Q", 1_000_000, 2_000_000),
                _action(1020, "哈尼娅", "E", 3_000_000, 4_000_000),
                _action(1020, "哈尼娅", "Q", 5_000_000, 9_000_000),
            ),
            hits=(),
            battle_end_us=30_000_000,
        )
        starts = {row.source_effect_definition_id: row.start_us for row in intervals}

        self.assertEqual(2_000_001, starts["character_skill:1003:GA_Sagiri_UltraSkill"])
        self.assertEqual(3_719_000, starts["character_skill:1020:GA_Haniel_Skill"])
        self.assertEqual(7_867_000, starts["character_skill:1020:GA_Haniel_UltraSkill"])


if __name__ == "__main__":
    unittest.main()
