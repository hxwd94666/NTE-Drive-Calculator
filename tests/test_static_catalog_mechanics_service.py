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
        self.assertEqual(
            {"damage": 3, "multipliers": 5, "states": 3, "settlement": 3},
            counts,
        )

    def test_default_gallery_contains_formulas_only(self) -> None:
        cards = [
            card
            for family in self.service.families
            for card in self.service.browse(family.key)
        ]
        self.assertEqual(14, len(cards))
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
