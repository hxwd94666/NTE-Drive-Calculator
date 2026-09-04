# 验证固定轴边际候选只存在于会话内存。
from __future__ import annotations

import unittest

from src.domain.battle_report import BattleInferredCharacterFact
from src.services.battle_marginal_candidate_service import (
    BattleMarginalCandidateService,
)


def _editor_data(*, active: bool, equipment_editable: bool = True) -> dict:
    return {
        "battle_record_id": 7,
        "is_active": active,
        "equipment_editable": equipment_editable,
        "details": [
            {
                "character": {"character_id": 1004, "name_zh": "安魂曲"},
                "profile": {
                    "character_id": 1004,
                    "character_level": 70,
                    "ordinal": 0,
                    "equipment_override": [{"uid_slot": 2, "uid_serial": 20}],
                },
                "original_profile": {
                    "character_id": 1004,
                    "character_level": 60,
                },
                "selected_equipment_context_key": "edited",
                "equipment_contexts": {
                    "battle": {
                        "title": "本场原始冻结配装",
                        "items": [{"uid_slot": 1, "uid_serial": 10}],
                        "calculation_items": [{"uid_slot": 1, "uid_serial": 10}],
                    },
                    "edited": {
                        "title": "修改副本配装",
                        "items": [{"uid_slot": 2, "uid_serial": 20}],
                        "calculation_items": [{"uid_slot": 2, "uid_serial": 20}],
                    },
                },
                "replacement_items": [{"uid_slot": 3, "uid_serial": 30}],
            }
        ],
    }


class BattleMarginalCandidateServiceTests(unittest.TestCase):
    def test_active_edit_is_candidate_baseline_without_source_switching(self) -> None:
        prepared = BattleMarginalCandidateService.prepare_editor_data(
            _editor_data(active=True),
            equipment_editable=True,
        )

        detail = prepared["details"][0]
        self.assertEqual(70, detail["profile"]["character_level"])
        self.assertEqual(["candidate"], list(detail["equipment_contexts"]))
        self.assertEqual(
            20,
            detail["equipment_contexts"]["candidate"]["items"][0]["uid_serial"],
        )
        self.assertEqual("active_build_edit", prepared["marginal_baseline_kind"])

    def test_inactive_edit_falls_back_to_original_frozen_build(self) -> None:
        prepared = BattleMarginalCandidateService.prepare_editor_data(
            _editor_data(active=False),
            equipment_editable=True,
        )

        detail = prepared["details"][0]
        self.assertEqual(60, detail["profile"]["character_level"])
        self.assertEqual(
            10,
            detail["equipment_contexts"]["candidate"]["items"][0]["uid_serial"],
        )
        self.assertEqual("battle_frozen", prepared["marginal_baseline_kind"])

    def test_candidate_uses_only_selected_context_frozen_replacement_pool(self) -> None:
        editor_data = _editor_data(active=False)
        editor_data["details"][0]["equipment_contexts"]["battle"][
            "replacement_items"
        ] = [{"uid_slot": 4, "uid_serial": 40}]
        editor_data["details"][0]["equipment_contexts"]["edited"][
            "replacement_items"
        ] = [{"uid_slot": 5, "uid_serial": 50}]

        prepared = BattleMarginalCandidateService.prepare_editor_data(
            editor_data,
            equipment_editable=True,
        )

        replacements = prepared["details"][0]["equipment_contexts"][
            "candidate"
        ]["replacement_items"]
        self.assertEqual([40], [row["uid_serial"] for row in replacements])

    def test_explicit_effect_in_active_baseline_hides_redundant_inferred_fact(
        self,
    ) -> None:
        editor_data = _editor_data(active=True)
        editor_data["details"][0]["profile"]["selected_awaken_effect_ids"] = [
            "Effect5"
        ]
        editor_data["inferred_character_facts"] = (
            BattleInferredCharacterFact(
                fact_id="awaken-effect-active:1004:Effect5:Blood_Damage_LV6",
                character_id=1004,
                fact_kind="awaken_effect_active",
                fact_value="Effect5",
                source_gameplay_effect_id=(
                    "GE_Player_Lacrimosa_Blood_Damage_LV6"
                ),
                confidence="高",
                evidence_event_ids=("18:primary",),
                model_version="battle-inferred-character-fact-v1",
                inference_basis="test",
            ),
        )

        prepared = BattleMarginalCandidateService.prepare_editor_data(
            editor_data,
            equipment_editable=True,
        )

        self.assertEqual((), prepared["inferred_character_facts"])

    def test_read_only_equipment_policy_strips_candidate_override(self) -> None:
        prepared = BattleMarginalCandidateService.prepare_editor_data(
            _editor_data(active=True, equipment_editable=False),
            equipment_editable=False,
        )
        detail = prepared["details"][0]
        self.assertNotIn("equipment_override", detail["profile"])
        self.assertEqual(
            [],
            detail["equipment_contexts"]["candidate"]["replacement_items"],
        )
        candidate = BattleMarginalCandidateService.freeze(
            7,
            [{**detail["profile"], "equipment_override": [{"uid_slot": 9}]}],
            equipment_editable=False,
        )

        projected = BattleMarginalCandidateService.as_build_edit(candidate)

        self.assertNotIn(
            "equipment_override",
            projected["characters"][0]["profile"],
        )

    def test_replacement_mutates_only_the_given_candidate_context(self) -> None:
        context = {
            "items": [
                {"uid_slot": 1, "uid_serial": 10, "target_row": 2},
                {"uid_slot": 2, "uid_serial": 20},
            ],
            "calculation_items": [
                {"uid_slot": 1, "uid_serial": 10, "target_row": 2},
                {"uid_slot": 2, "uid_serial": 20},
            ],
        }

        BattleMarginalCandidateService.replace_equipment(
            context,
            {"uid_slot": 1, "uid_serial": 10, "target_row": 2},
            {"uid_slot": 3, "uid_serial": 30},
        )

        self.assertEqual([30, 20], [row["uid_serial"] for row in context["items"]])
        self.assertEqual(2, context["items"][0]["target_row"])
        self.assertEqual(
            [30, 20],
            [row["uid_serial"] for row in context["calculation_items"]],
        )

    def test_worker_candidate_carries_only_explicit_disabled_fact_ids(self) -> None:
        candidate = BattleMarginalCandidateService.freeze(
            7,
            [{"character_id": 1004}],
            equipment_editable=True,
            disabled_inferred_fact_ids=("effect5", "", "effect5"),
        )

        self.assertEqual(frozenset({"effect5"}), candidate.disabled_inferred_fact_ids)


if __name__ == "__main__":
    unittest.main()
