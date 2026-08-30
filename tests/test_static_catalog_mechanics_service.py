# 验证玩家战斗机制图鉴只展示中文伤害公式与可读内联关系。
from __future__ import annotations

import unittest
from pathlib import Path

from src.services.static_catalog_mechanics_service import (
    StaticCatalogMechanicsService,
    encode_record,
)


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class StaticCatalogMechanicsServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = StaticCatalogMechanicsService(
            PROJECT_ROOT / "data" / "game_static.sqlite3"
        )

    def test_catalog_uses_four_player_formula_families(self) -> None:
        self.assertEqual(
            ("damage", "multipliers", "states", "settlement"),
            tuple(family.key for family in self.service.families),
        )
        counts = {
            family.key: len(self.service.browse(family.key))
            for family in self.service.families
        }
        self.assertEqual(3, counts["damage"])
        self.assertEqual(5, counts["multipliers"])
        self.assertGreaterEqual(counts["states"], 13)
        self.assertGreaterEqual(counts["settlement"], 6)

    def test_default_gallery_contains_formulas_only(self) -> None:
        cards = [
            card
            for family in self.service.families
            for card in self.service.browse(family.key)
        ]
        self.assertGreaterEqual(len(cards), 27)
        self.assertEqual({"formula"}, {card.card_kind for card in cards})
        visible = "\n".join(
            f"{card.eyebrow}\n{card.title}\n{card.subtitle}" for card in cards
        )
        for token in (
            "GAMEPLAY", "BUFF", "GA_", "GE_", "反事实", "正式静态",
            "CoefModify", "FinalDamageUp", "SourceTierCoef",
        ):
            self.assertNotIn(token, visible)

    def test_formula_details_use_curated_chinese_names_and_symbols(self) -> None:
        detail = self.service.detail(encode_record("formula", "skill_multiplier"))
        visible = "\n".join(
            [detail.title, detail.subtitle]
            + [field.label for section in detail.sections for field in section.fields]
            + [field.value for section in detail.sections for field in section.fields]
        )
        self.assertIn("技能倍率 = 等级倍率", visible)
        self.assertIn("倍率修正", visible)
        self.assertNotIn("CoefModify", visible)
        self.assertNotIn("SourceTierCoef", visible)
        self.assertEqual((), detail.related_links)
        self.assertEqual((), detail.identity_fields)

    def test_direct_formula_lists_every_variable_and_which_side_supplies_it(self) -> None:
        detail = self.service.detail(encode_record("formula", "direct_damage"))
        self.assertEqual(
            ("完整公式", "计算顺序", "变量来源", "判定与限制"),
            tuple(section.title for section in detail.sections),
        )
        source_section = next(
            section for section in detail.sections if section.title == "变量来源"
        )
        visible = "\n".join(
            f"{field.label}\n{field.value}" for field in source_section.fields
        )
        for token in (
            "技能倍率", "攻击方", "受击目标", "防御区", "抗性区", "暴击",
        ):
            self.assertIn(token, visible)

    def test_ring_tier_catalog_uses_all_official_source_tiers(self) -> None:
        detail = self.service.detail(encode_record("formula", "reaction_tiers"))
        visible = "\n".join(
            [detail.title, detail.subtitle]
            + [
                f"{section.title}\n"
                + "\n".join(f"{field.label}\n{field.value}" for field in section.fields)
                for section in detail.sections
            ]
        )
        self.assertIn("源档 0 · 角色等级 1–5", visible)
        self.assertIn("创生 80", visible)
        self.assertIn("浊燃 20", visible)
        self.assertIn("残虹浊燃 20", visible)
        self.assertIn("黯星 400", visible)
        self.assertIn("源档 15 · 角色等级 76–80", visible)
        self.assertIn("创生 9,000", visible)
        self.assertIn("浊燃 2,700", visible)
        self.assertIn("残虹浊燃 2,700", visible)
        self.assertIn("黯星 45,000", visible)

    def test_every_ring_and_named_dot_has_its_own_formula_card(self) -> None:
        cards = [
            card
            for family in self.service.families
            for card in self.service.browse(family.key)
        ]
        titles = {card.title for card in cards}
        for title in (
            "环合归属与 16 档基础值",
            "环合·创生",
            "环合·覆纹",
            "环合·浊燃",
            "环合·黯星",
            "环合·浸染",
            "环合·延滞",
            "环合·盈蓄",
            "环合·失谐",
            "持续直伤·噩梦",
            "持续直伤·蚀心",
            "持续直伤·鸩火",
        ):
            self.assertIn(title, titles)

    def test_topple_card_lists_the_official_per_level_base_curve(self) -> None:
        detail = self.service.detail(encode_record("formula", "topple_damage"))
        visible = "\n".join(
            f"{section.title}\n"
            + "\n".join(f"{field.label}\n{field.value}" for field in section.fields)
            for section in detail.sections
        )
        self.assertIn("官方倾陷基础 · 1–10 级", visible)
        self.assertIn("1级 91", visible)
        self.assertIn("官方倾陷基础 · 71–80 级", visible)
        self.assertIn("80级 3,603", visible)

    def test_ring_details_explain_owner_attacker_and_target_sources(self) -> None:
        for formula_key in (
            "reaction_creation",
            "reaction_scorch",
            "reaction_nova",
        ):
            with self.subTest(formula_key=formula_key):
                detail = self.service.detail(encode_record("formula", formula_key))
                source_section = next(
                    section
                    for section in detail.sections
                    if section.title == "变量来源"
                )
                visible = "\n".join(field.value for field in source_section.fields)
                self.assertIn("两名环合参与者", visible)
                self.assertIn("受击目标", visible)

    def test_stain_is_a_complete_team_final_damage_formula(self) -> None:
        detail = self.service.detail(encode_record("formula", "reaction_infusion"))
        visible = "\n".join(
            [detail.subtitle]
            + [field.value for section in detail.sections for field in section.fields]
        )
        self.assertEqual("complete", detail.status)
        self.assertIn("1.20", visible)
        self.assertIn("本击来源环合强度 + 180", visible)
        self.assertIn("实际来源角色", visible)
        self.assertIn("12 秒", visible)
        self.assertIn("队伍所有角色", visible)
        self.assertIn("触发 QTE 本击不吃", visible)
        self.assertIn("不同乘区", visible)
        self.assertNotIn("最终伤害公式 = 未确认", visible)

    def test_weave_uses_the_recorded_damage_source_including_dot(self) -> None:
        detail = self.service.detail(encode_record("formula", "weave_followup"))
        visible = "\n".join(
            field.value for section in detail.sections for field in section.fields
        )
        self.assertIn("实际来源角色", visible)
        self.assertIn("正式 DOT", visible)
        self.assertIn("不取触发覆纹的 QTE 角色", visible)
        self.assertIn("不比较环合双方", visible)

    def test_pigeon_fire_common_misspelling_is_searchable(self) -> None:
        cards = self.service.browse("states", "鸠火")
        self.assertEqual(("持续直伤·鸩火",), tuple(card.title for card in cards))

    def test_search_indexes_player_chinese_projection_only(self) -> None:
        chinese = self.service.browse("states", "持续伤害")
        internal = self.service.browse("states", "State.Damage.Dot")
        self.assertTrue(chinese)
        self.assertEqual((), internal)
        self.assertTrue(all(card.card_kind == "formula" for card in chinese))

    def test_counterfactual_models_are_not_player_records(self) -> None:
        self.assertEqual((), self.service.status_counts())
        with self.assertRaisesRegex(LookupError, "不在玩家图鉴"):
            self.service.detail(encode_record("model", "formal_dot_classification"))

    def test_missing_buff_relation_is_a_normal_unavailable_record(self) -> None:
        record_id = encode_record("effect", f"buff{chr(31)}missing_buff")
        with self.assertRaisesRegex(LookupError, "关联机制不存在"):
            self.service.detail(record_id)

    def test_owned_effect_renders_owner_summary_without_outgoing_links(self) -> None:
        detail = self.service.detail(encode_record(
            "effect", f"combat_effect{chr(31)}character_awaken:1036:Effect1"
        ))
        owner = next(section for section in detail.sections if section.title == "所属对象")
        self.assertTrue(owner.fields[0].value)
        self.assertIsNotNone(detail.owner_link)
        self.assertFalse(detail.redirect_only)
        self.assertEqual((), detail.related_links)
        self.assertEqual((), detail.identity_fields)

    def test_player_fields_hide_paths_hashes_and_raw_identity(self) -> None:
        detail = self.service.detail(encode_record(
            "effect", f"combat_effect{chr(31)}character_awaken:1036:Effect1"
        ))
        visible = "\n".join(
            f"{section.title}\n" + "\n".join(
                f"{field.label}\n{field.value}" for field in section.fields
            )
            for section in detail.sections
        )
        for token in ("/Game/", "SHA", "来源文件", "正式 ID", "Gameplay Tag"):
            self.assertNotIn(token, visible)

    def test_continuous_and_independent_final_damage_remain_separate(self) -> None:
        dot = self.service.detail(encode_record("formula", "dot_damage"))
        final = self.service.detail(
            encode_record("formula", "independent_final_damage")
        )
        self.assertEqual("持续伤害", dot.title)
        self.assertEqual("独立最终增伤", final.title)
        self.assertNotEqual(dot.record_id, final.record_id)

    def test_structured_owner_and_skill_relation_audit_counts_are_preserved(self) -> None:
        self.assertEqual(
            (("character_awaken", 184), ("fork_star", 245),
             ("equipment_suit", 24)),
            self.service.owner_resolution_counts(),
        )
        self.assertEqual(14, dict(self.service.skill_relation_counts())["formal_modifiers"])


if __name__ == "__main__":
    unittest.main()
