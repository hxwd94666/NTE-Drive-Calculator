# 验证自定义 Calculation 只从明确登记的精炼参数取值。
from __future__ import annotations

NTE_TEST_TIER = "core"

import unittest

from src.services.battle_report.buffs.semantics import (
    BattleBuffTargetScopeService,
    calculation_applies_to_damage,
    confirmed_buff_target_scope,
    render_buff_name,
    resolve_buff_calculation,
)


class BattleBuffSemanticServiceTests(unittest.TestCase):

    def test_unknown_runtime_buff_keeps_raw_id_as_auditable_fallback(self) -> None:
        self.assertEqual(
            "Buff_NotYetMapped",
            render_buff_name("Buff_NotYetMapped", "Buff_NotYetMapped"),
        )

    def test_zero_six_awaken_attack_buff_targets_the_whole_team(self) -> None:
        self.assertEqual(
            "team",
            confirmed_buff_target_scope(
                "Buff_Female051_LevelExtra1_1",
                "self",
            ),
        )

    def test_mofeikesi_refinement_calculations_resolve_distinct_parameters(self) -> None:
        source = {
            "parameters": [
                {"name_id": "buff_mofeikesi_ChargeGetEfficiency", "value": 0.30},
                {"name_id": "buff_mofeikesi_Atk", "value": 0.16},
                {"name_id": "buff_mofeikesi_Up", "value": 0.10},
            ]
        }
        root = "/Game/Blueprints/Abilities/Calculation/Fork/Fork_mofeikesi/"

        charge = resolve_buff_calculation(root + "Cau_Fork_mofeikesi1_1", source)
        attack = resolve_buff_calculation(root + "Cau_Fork_mofeikesi1_2", source)
        extra = resolve_buff_calculation(root + "Cau_Fork_mofeikesi1_3", source)

        self.assertEqual((0.30, "buff_mofeikesi_ChargeGetEfficiency"), (
            charge.value, charge.parameter_id,
        ))
        self.assertEqual((0.16, "buff_mofeikesi_Atk"), (
            attack.value, attack.parameter_id,
        ))
        self.assertEqual((0.10, "buff_mofeikesi_Up"), (
            extra.value, extra.parameter_id,
        ))
        self.assertEqual("中", attack.confidence)

    def test_unknown_calculation_remains_unresolved_instead_of_using_importer_zero(self) -> None:
        result = resolve_buff_calculation(
            "/Game/Calculation/Cau_Unknown",
            {"parameters": []},
        )

        self.assertIsNone(result.value)
        self.assertEqual("低", result.confidence)

    def test_time_fork_attack_uses_current_refinement_parameter(self) -> None:
        result = resolve_buff_calculation(
            "/Game/Blueprints/Abilities/Calculation/Fork/Fork_Time/"
            "Cau_Fork_Time_AtkUp",
            {"parameters": [{"name_id": "buff_Time_AtkUp", "value": 0.16}]},
        )

        self.assertEqual((0.16, "buff_Time_AtkUp", "中"), (
            result.value,
            result.parameter_id,
            result.confidence,
        ))

    def test_specialized_coefficient_has_exact_damage_scope(self) -> None:
        calculation = (
            "/Game/Blueprints/Abilities/Calculation/Zankou/"
            "Calc_ZankouDotStackCoef"
        )

        self.assertTrue(calculation_applies_to_damage(
            calculation,
            "GE_Player_Zankou_DotDamage",
        ))
        self.assertFalse(calculation_applies_to_damage(
            calculation,
            "GE_Player_Zankou_Skill2_Damage",
        ))
        self.assertIsNone(calculation_applies_to_damage(
            "/Game/Calculation/Unknown",
            "GE_Player_Zankou_DotDamage",
        ))


class BattleBuffTargetScopeServiceTests(unittest.TestCase):
    def test_explicit_team_marker_projects_team_scope(self) -> None:
        scope = BattleBuffTargetScopeService.for_skill_binding({
            "binding_kind": "active",
            "target_type_asset_path": "/Script/NTE.TargetType_AllPlayer",
        })

        self.assertEqual("team", scope)

    def test_empty_or_generic_target_type_does_not_default_to_self(self) -> None:
        self.assertEqual(
            "unknown",
            BattleBuffTargetScopeService.for_skill_binding({
                "binding_kind": "active",
                "target_type_asset_path": "",
            }),
        )
        self.assertEqual(
            "unknown",
            BattleBuffTargetScopeService.for_skill_binding({
                "binding_kind": "active",
                "target_type_asset_path": "/Script/NTE.DamageRangeTargetType",
            }),
        )

    def test_explicit_self_enemy_and_passive_binding_remain_distinct(self) -> None:
        self.assertEqual(
            "self",
            BattleBuffTargetScopeService.for_skill_binding({
                "binding_kind": "active",
                "target_type_asset_path": "/Script/NTE.TargetType_Owner",
            }),
        )
        self.assertEqual(
            "target",
            BattleBuffTargetScopeService.for_skill_binding({
                "binding_kind": "active",
                "target_type_asset_path": "/Script/NTE.TargetType_Enemy",
            }),
        )
        self.assertEqual(
            "self",
            BattleBuffTargetScopeService.for_skill_binding({
                "binding_kind": "passive_buff",
                "target_type_asset_path": "",
            }),
        )


if __name__ == "__main__":
    unittest.main()
