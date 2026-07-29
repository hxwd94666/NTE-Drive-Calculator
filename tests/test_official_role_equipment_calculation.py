# 覆盖角色页额外形状、满级投影与异能边际收益的统一计算边界。
import unittest
from dataclasses import replace
from unittest.mock import patch

from src.services.damage_calculation_service import DamageScalingStat, DirectDamageInput
from src.services import official_role_page_service as role_service
from src.services import official_role_replacement_service as replacement_service
from src.services import official_role_scoring_service as scoring_service
from src.features.official_role.role_calculation import normalized_marginal_weights


def _direct_input() -> DirectDamageInput:
    return DirectDamageInput(
        skill_multiplier=1.0,
        scaling_stat=DamageScalingStat.ATTACK,
        attack_base=100.0,
        attack_up=0.0,
        attack_add=0.0,
        health_base=100.0,
        health_up=0.0,
        health_add=0.0,
        defense_base=100.0,
        defense_up=0.0,
        defense_add=0.0,
        character_level=80.0,
        enemy_level=80.0,
        crit_rate=0.05,
        crit_damage=0.50,
        defense_penetration=0.0,
        defense_reduction=0.0,
    )


class OfficialRoleEquipmentCalculationTests(unittest.TestCase):
    def _detail(self) -> dict:
        raw_core = {
            "uid_slot": 1,
            "uid_serial": 1,
            "kind": "core",
            "quality": "orange",
            "main_stats": ({"property_id": "CritBase", "value": 0.10, "percent": True},),
            "sub_stats": (),
        }
        full_level_core = {
            **raw_core,
            "main_stats": ({"property_id": "CritBase", "value": 0.30, "percent": True},),
        }
        module = {
            "uid_slot": 2,
            "uid_serial": 1,
            "kind": "module",
            "quality": "orange",
            "grid_count": 2,
            "main_stats": (),
            "sub_stats": (),
        }
        return {
            "attributes": {
                "CritBase": {"show_percent": True, "display_name_zh": "暴击率"},
            },
            "shape_bonus": {
                "shape_label": "Type-2",
                "properties": ({"property_id": "CritBase", "display_value": 6.0},),
            },
            "equipment_contexts": {
                "current": {
                    "items": (raw_core, module),
                    "calculation_items": (full_level_core, module),
                },
            },
        }

    def test_calculation_context_uses_max_level_main_stat_and_extra_shape(self) -> None:
        _fork, equipment, combined = role_service._property_stats_by_source(
            self._detail(), "current"
        )

        self.assertAlmostEqual(0.36, equipment["CritBase"])
        self.assertAlmostEqual(0.36, combined["CritBase"])

    def test_attribute_summary_includes_extra_shape_bonus(self) -> None:
        detail = self._detail()
        summary = role_service.calculate_official_role_attribute_summaries(
            detail,
            detail["equipment_contexts"]["current"]["calculation_items"],
        )
        values = {row.key: row.value for row in summary["equipment"]}

        self.assertAlmostEqual(0.36, values["CritBase"])

    def test_replacement_context_never_scores_a_stale_projection(self) -> None:
        previous_projection = {
            "uid_slot": 1,
            "uid_serial": 1,
            "kind": "core",
        }
        replacement_projection = {
            "uid_slot": 2,
            "uid_serial": 1,
            "kind": "core",
        }
        context = {
            "items": (replacement_projection,),
            "calculation_items": (previous_projection,),
        }

        self.assertEqual(
            [replacement_projection],
            role_service._context_calculation_items(context),
        )

    def test_replacement_direct_damage_uses_the_candidate_not_the_old_item(self) -> None:
        previous_projection = {
            "uid_slot": 1,
            "uid_serial": 1,
            "kind": "core",
            "attack_bonus": 0.0,
        }
        replacement_projection = {
            "uid_slot": 2,
            "uid_serial": 1,
            "kind": "core",
            "attack_bonus": 20.0,
        }
        detail = {
            "equipment_contexts": {
                "replacement": {
                    "items": (replacement_projection,),
                    # This is the old bug's exact state: ``items`` changed,
                    # but the full-level projection was not refreshed.
                    "calculation_items": (previous_projection,),
                },
            },
        }

        def inputs(source_detail, context_key):
            total = sum(
                item.get("attack_bonus", 0.0)
                for item in role_service._context_calculation_items(
                    source_detail["equipment_contexts"][context_key]
                )
            )
            return (replace(_direct_input(), attack_add=total),)

        with patch.object(
            replacement_service,
            "_role_panel_damage_inputs",
            side_effect=inputs,
        ):
            gain = role_service.calculate_official_role_item_gain(
                detail, "replacement", replacement_projection,
            )

        self.assertIsNotNone(gain)
        self.assertGreater(gain["gain_percent"], 0.0)

    def test_damage_formula_lists_extra_shape_as_a_distinct_source(self) -> None:
        with patch.object(
            role_service,
            "_role_panel_damage_inputs",
            return_value=(_direct_input(),),
        ):
            breakdown = role_service.calculate_official_role_damage_breakdown(
                self._detail(), "current"
            )

        self.assertIsNotNone(breakdown)
        shape_rows = [
            row for row in breakdown["bonuses"]
            if row["source"] == "额外形状" and row.get("property_id") == "CritBase"
        ]
        self.assertEqual(1, len(shape_rows))
        self.assertAlmostEqual(0.06, shape_rows[0]["value"])

    def test_element_damage_margin_is_always_available_at_one_point_two_five_percent(self) -> None:
        detail = {
            "character": {"element_type": "Element_CHAOS"},
            "property_weights": {},
        }
        combined = {"DamageUpChaosBase": 0.20}
        with patch.object(
            scoring_service,
            "_role_panel_damage_inputs",
            return_value=(_direct_input(),),
        ), patch.object(
            scoring_service,
            "_property_stats_by_source",
            return_value=({}, {}, combined),
        ):
            margins = role_service.calculate_official_role_margins(detail, "current")

        self.assertIsNotNone(margins)
        row = next(
            row for row in margins["rows"]
            if row["property_id"] == "DamageUpChaosBase"
        )
        self.assertEqual("异能伤害%", row["label"])
        self.assertAlmostEqual(0.0125, row["unit"])

    def test_formula_scaling_stat_controls_which_base_stats_have_margins(self) -> None:
        detail = {"character": {"element_type": ""}, "property_weights": {}}
        health_input = replace(_direct_input(), scaling_stat=DamageScalingStat.HEALTH)
        with patch.object(
            scoring_service,
            "_role_panel_damage_inputs",
            return_value=(health_input,),
        ), patch.object(
            scoring_service,
            "_property_stats_by_source",
            return_value=({}, {}, {}),
        ):
            margins = role_service.calculate_official_role_margins(detail, "current")

        self.assertIsNotNone(margins)
        property_ids = {row["property_id"] for row in margins["rows"]}
        self.assertTrue({"HPMaxUp", "HPMaxAdd"} <= property_ids)
        self.assertNotIn("AtkUp", property_ids)

    def test_marginal_weight_keeps_formula_zero_at_zero_and_uses_base_elsewhere(self) -> None:
        weights, formula_ids = normalized_marginal_weights(
            {"CritBase": 0.4, "DamageUpGeneralBase": 0.7, "MagBase": 0.6},
            {
                "rows": [
                    {"property_id": "CritBase", "gain_percent": 4.0},
                    {"property_id": "DamageUpGeneralBase", "gain_percent": 2.0},
                    {"property_id": "AtkUp", "gain_percent": 0.0},
                ]
            },
        )

        self.assertEqual({"CritBase", "DamageUpGeneralBase", "AtkUp"}, formula_ids)
        self.assertAlmostEqual(1.0, weights["CritBase"])
        self.assertAlmostEqual(0.5, weights["DamageUpGeneralBase"])
        self.assertEqual(0.0, weights["AtkUp"])
        self.assertAlmostEqual(0.6, weights["MagBase"])

    def test_hidden_replacement_score_keeps_non_direct_formula_properties(self) -> None:
        detail = {
            "attributes": {
                "CritBase": {"display_name_zh": "暴击率%"},
                "MagBase": {"display_name_zh": "环合强度"},
            },
        }
        support_item = {
            "kind": "module",
            "quality": "gold",
            "grid_count": 2,
            "sub_stats": [{"property_id": "MagBase", "value": 60}],
        }
        score = role_service.calculate_official_role_hidden_equipment_score(
            detail,
            support_item,
            property_weights={"CritBase": 1.0, "MagBase": 0.8},
        )

        self.assertGreater(score, 0.0)


if __name__ == "__main__":
    unittest.main()
