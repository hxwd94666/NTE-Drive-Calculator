# 验证战斗机制图鉴的玩家投影、归属边界与四态覆盖。
from __future__ import annotations

import unittest
from pathlib import Path

from src.domain.static_catalog_terminology import LocalizedTermRecord
from src.services.static_catalog_mechanics_service import (
    StaticCatalogMechanicsService,
    decode_record,
    encode_record,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _InjectedTerminologySource:
    def lookup_localized_term(self, entity_kind, stable_id, *, context):
        del context
        if entity_kind == "gameplay_tag":
            return LocalizedTermRecord(
                entity_kind=entity_kind,
                canonical_id=stable_id,
                names={"zh-CN": "正式状态名称"},
                text_table="ST_Test",
                text_key="state_name",
            )
        return None


class StaticCatalogMechanicsServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.service = StaticCatalogMechanicsService(
            PROJECT_ROOT / "data" / "game_static.sqlite3"
        )

    def test_single_catalog_exposes_six_ordered_families(self) -> None:
        self.assertEqual(
            ("attributes", "reactions", "dot", "topple", "events", "formula"),
            tuple(family.key for family in self.service.families),
        )
        self.assertTrue(self.service.browse("reactions"))
        self.assertTrue(all(
            self.service.browse(family_key)
            for family_key in ("attributes", "dot", "topple", "events", "formula")
        ))

    def test_default_gallery_excludes_placeholder_only_effects(self) -> None:
        default_cards = [
            card
            for family in self.service.families
            for card in self.service.browse(family.key)
        ]

        self.assertTrue(default_cards)
        self.assertNotIn("名称暂未提供", {card.title for card in default_cards})
        effect_kinds = {
            self._effect_kind(card.record_id)
            for card in default_cards
            if card.card_kind == "effect"
        }
        self.assertTrue({
            "gameplay_ability",
            "skill_damage",
            "gameplay_effect",
            "buff",
            "combat_effect",
            "reaction",
        }.issubset(effect_kinds))
        searched = self.service.browse("dot", "State.Damage.Dot", limit=50)
        self.assertTrue(any(
            card.card_kind == "effect" and card.title == "名称暂未提供"
            for card in searched
        ))

    def test_composite_owners_redirect_to_real_object_pages(self) -> None:
        cases = (
            (
                "character_awaken:1036:Effect1",
                ("character", "1036", "owner", "Effect1"),
            ),
            (
                "fork_star:upgradestar_pack_fork_Arachne:1",
                ("fork", "fork_Arachne", "owner", "1"),
            ),
            (
                "equipment_suit:Suit1:4",
                ("equipment", "Suit1", "suit", "4"),
            ),
        )
        for effect_id, expected in cases:
            detail = self.service.detail(encode_record(
                "effect", f"combat_effect{chr(31)}{effect_id}"
            ))
            self.assertTrue(detail.redirect_only)
            self.assertIsNotNone(detail.owner_link)
            assert detail.owner_link is not None
            self.assertEqual(expected, (
                detail.owner_link.domain_key,
                detail.owner_link.record_id,
                detail.owner_link.relation_kind,
                detail.owner_link.anchor,
            ))

    def test_all_structured_effect_owners_resolve(self) -> None:
        self.assertEqual(
            (
                ("character_awaken", 184),
                ("fork_star", 245),
                ("equipment_suit", 24),
            ),
            self.service.owner_resolution_counts(),
        )

    def test_skill_damage_closes_formula_ge_buff_and_modifier_relations(self) -> None:
        detail = self.service.detail(encode_record(
            "effect",
            f"skill_damage{chr(31)}GE_Player_Oneiroi_Melee1_Damage",
        ))
        targets = {
            (link.domain_key, self._effect_kind(link.record_id), link.relation_kind)
            for _label, link in detail.related_links
        }
        values = {
            field.label: field.value
            for section in detail.sections
            for field in section.fields
        }

        self.assertIn(("combat_mechanics", "gameplay_ability", "related"), targets)
        self.assertIn(("combat_mechanics", "gameplay_effect", "related"), targets)
        self.assertIn(("combat_mechanics", "buff", "related"), targets)
        self.assertIn(("combat_mechanics", "formula", "formula"), targets)
        self.assertEqual("0.9", values["项目倍率修正系数"])
        self.assertEqual(
            (
                ("formal_modifiers", 14),
                ("named_buffs", 593),
                ("named_damage_items", 651),
                ("named_gameplay_effects", 645),
                ("readable_abilities", 93),
            ),
            self.service.skill_relation_counts(),
        )

    def test_default_internal_links_resolve_to_detail_or_nonempty_search(self) -> None:
        broken: list[tuple[str, str]] = []
        for family in self.service.families:
            for card in self.service.browse(family.key):
                detail = self.service.detail(card.record_id)
                for _label, link in detail.related_links:
                    if link.domain_key != "combat_mechanics":
                        continue
                    kind, key = decode_record(link.record_id)
                    if kind == "search":
                        if not self.service.browse(family.key, key, limit=50):
                            broken.append((family.key, link.record_id))
                    else:
                        self.service.detail(link.record_id)
        self.assertEqual([], broken)

    def test_player_fields_hide_paths_hashes_and_source_internals(self) -> None:
        cards = self.service.browse("attributes", "Ability.Actor", limit=50)
        effect = next(card for card in cards if card.card_kind == "effect")
        detail = self.service.detail(effect.record_id)
        visible = "\n".join(
            (section.title + "\n" + "\n".join(
                f"{field.label}\n{field.value}" for field in section.fields
            ))
            for section in detail.sections
        )

        self.assertNotIn("/Game/", visible)
        self.assertNotIn("资源路径", visible)
        self.assertNotIn("SHA", visible)
        self.assertNotIn("来源文件", visible)
        self.assertTrue(detail.audit_references)

    def test_unnamed_effects_use_placeholder_and_fold_professional_identity(self) -> None:
        for family, query in (("dot", "State.Damage.Dot"), ("topple", "Unbal")):
            for card in self.service.browse(family, query, limit=50):
                if card.card_kind != "effect":
                    continue
                self.assertTrue(
                    "\u3400" <= card.title[0] <= "\u9fff",
                    card.title,
                )
                self.assertNotIn("_", card.title)
                detail = self.service.detail(card.record_id)
                if detail.title == "名称暂未提供":
                    self.assertTrue(detail.identity_fields)
                for section in detail.sections:
                    for field in section.fields:
                        self.assertNotIn("/Game/", field.value)
                        self.assertFalse(field.label.endswith("ID"), field.label)
                        self.assertFalse(field.value.startswith(("{", "[", "$[")))

    def test_counterfactual_public_subset_keeps_four_states(self) -> None:
        self.assertEqual(
            (("complete", 4), ("partial", 7), ("unavailable", 2),
             ("not_applicable", 1)),
            self.service.status_counts(),
        )
        visible_model_keys = {
            card.record_id
            for family in self.service.families
            for card in self.service.browse(family.key)
            if card.card_kind == "model"
        }
        for object_owned in (
            "character_passives", "awakening_six_effects", "fork_and_weapon_skills",
        ):
            self.assertNotIn(encode_record("model", object_owned), visible_model_keys)

    def test_dot_uses_formal_tag_and_final_damage_up_stays_separate(self) -> None:
        dot_cards = self.service.browse("dot", "State.Damage.Dot", limit=50)
        final_detail = self.service.detail(
            encode_record("formula", "independent_final_damage")
        )
        dot_detail = self.service.detail(encode_record("formula", "dot_damage"))

        self.assertTrue(any(card.card_kind == "effect" for card in dot_cards))
        self.assertIn("DOT 专属最终乘区", dot_detail.title)
        self.assertIn("FinalDamageUp", final_detail.title)
        self.assertNotEqual(dot_detail.record_id, final_detail.record_id)

    def test_native_sidecar_is_partial_validation_not_production(self) -> None:
        detail = self.service.detail(
            encode_record("model", "native_counterfactual_core")
        )

        self.assertEqual("partial", detail.status)
        self.assertIn("差分验证组件", detail.notice)
        self.assertNotIn("生产入口", detail.notice)
        self.assertTrue(any(stage.key == "production" for stage in detail.evidence_stages))
        visible_values = [
            field.value for section in detail.sections for field in section.fields
        ]
        self.assertIn("防御无视（DefIgnore）", visible_values)
        self.assertIn("8 个 Buff、56 次逐击公开差分", visible_values)
        self.assertTrue(all("unavailable" not in value for value in visible_values))
        self.assertTrue(all("ratio=1" not in value for value in visible_values))

    def test_model_text_localizes_status_tokens_and_scalar_covered_entity(self) -> None:
        unknown = self.service.detail(encode_record("model", "unknown_preservation"))
        healing = self.service.detail(
            encode_record("model", "healing_without_damage_consumer")
        )
        unknown_values = [
            field.value for section in unknown.sections for field in section.fields
        ]
        healing_coverage = next(
            section for section in healing.sections if section.title == "当前覆盖"
        )

        self.assertTrue(all(
            token not in "\n".join(unknown_values)
            for token in ("complete", "partial", "unavailable", "not_applicable")
        ))
        self.assertEqual(1, len(healing_coverage.fields))
        self.assertEqual("纯治疗输出", healing_coverage.fields[0].value)

    def test_model_card_front_never_exposes_raw_status_enums(self) -> None:
        visible = "\n".join(
            f"{card.title}\n{card.subtitle}\n{' '.join(card.badges)}"
            for family in self.service.families
            for card in self.service.browse(family.key)
            if card.card_kind == "model"
        )

        for token in ("complete", "partial", "unavailable", "not_applicable"):
            self.assertNotIn(token, visible)

    def test_effect_names_consume_injected_public_terminology_service(self) -> None:
        service = StaticCatalogMechanicsService(
            PROJECT_ROOT / "data" / "game_static.sqlite3",
            terminology_service=StaticCatalogTerminologyService(
                _InjectedTerminologySource()
            ),
        )

        tag = next(
            card for card in service.browse("dot")
            if card.card_kind == "effect"
        )
        self.assertEqual("正式状态名称", tag.title)

    @staticmethod
    def _effect_kind(record_id: str) -> str:
        from urllib.parse import unquote

        kind, encoded = record_id.split("|", 1)
        if kind == "formula":
            return "formula"
        return unquote(encoded).split(chr(31), 1)[0]


if __name__ == "__main__":
    unittest.main()
