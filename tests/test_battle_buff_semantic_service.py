# 验证自定义 Calculation 只从明确登记的精炼参数取值。
from __future__ import annotations

import unittest

from src.services.battle_buff_semantic_service import (
    calculation_applies_to_damage,
    confirmed_buff_target_scope,
    render_buff_name,
    resolve_buff_calculation,
)


class BattleBuffSemanticServiceTests(unittest.TestCase):
    def test_runtime_buff_ids_render_as_player_facing_chinese_names(self) -> None:
        expected = {
            "Buff_Fork_mofeikesi_5": "「墨菲克斯」",
            "Buff_Fork_mofeikesi_1_3": "好狗狗走四方Ⅰ",
            "Buff_Fork_mofeikesi_1_4": "好狗狗走四方Ⅱ",
            "Buff_Fork_Rose_Lv1": "「落拓玫瑰」",
            "Buff_Fork_Rose_Effect": "「暗棘」",
            "Buff_Fork_DemonBlade_Lv1": "「妖刀·缚命」",
            "Buff_Fork_DemonBlade_CritDmgUp": "噬心诡刃",
            "Buff_Fork_Arachne_Lv1": "「阿拉克涅」",
            "Buff_Fork_Arachne_Effect": "永恒圆舞曲",
            "Buff_Fork_Time_Lv1": "「时间之外的时间」",
            "Buff_Fork_Time_Save": "「荒时」",
            "Buff_Fork_Time_State": "「荒时迷宫」",
            "Buff_Fork_TigerTally_Effect": "预备备Ⅰ",
            "Buff_Fork_TigerTally_E": "「左虎符」",
            "Buff_Fork_TigerTally_Q": "「右虎符」",
            "Buff_Equipment_Cosmos2_4_Effect": "「失落光芒」",
            "Buff_Equipment_Chaos2_4_Effect": "「迪亚波罗斯」",
            "Buff_Equipment_Chaos2_4_Effect_Power": "「迪亚波罗斯」",
            "Buff_Equipment_Incantation_4_1": "「真红：双生蝶」",
            "Buff_Equipment_GetEfficiency2_4_1": "「音速蓝刺猬」",
            "Buff_Fork_Butterfly_Effect": "现实避难所",
            "Buff_Fork_BlackBook_Lv5": "「黑之书」",
            "Buff_BlackBook_CantUse": "「黑之书」：锁链封锁",
            "Buff_Lacrimosa_MeleeTotal": "「噩梦」",
            "Buff_Fadia_NoDieShareDamage": "观众目击的祭献：分摊保护",
            "Buff_Fadia_ShareOutTeammatesDamage": "观众目击的祭献：伤害分摊",
            "Buff_DaffodillUnbalUp": "达芙蒂尔：倾陷伤害提升",
            "Buff_Fork_Butterfly_Lv5": "「斑蝶」",
            "Buff_Fork_Butterfly_Lv2": "「斑蝶」",
            "Buff_Fork_TigerTally_Lv1": "「司令虎符」",
            "Buff_Fork_TigerTally_Lv4": "「司令虎符」",
            "Buff_Fork_Time_Lv5": "「时间之外的时间」",
            "Buff_Fork_BlackBook_Lv3": "「黑之书」",
            "Buff_Female051_Level3": "奇异记叙",
            "Buff_Female051_Level4": "未决迷数",
            "Buff_Female051_Level5_1": "默示赋命",
            "Buff_Female051_LevelExtra1_1": "零示",
        }

        for definition_id, display_name in expected.items():
            with self.subTest(definition_id=definition_id):
                self.assertEqual(
                    display_name,
                    render_buff_name(definition_id, definition_id),
                )

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
        self.assertIn("尚未登记", result.reason)

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


if __name__ == "__main__":
    unittest.main()
