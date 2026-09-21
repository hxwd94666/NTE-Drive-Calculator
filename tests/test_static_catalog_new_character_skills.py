# 验证新角色技能不被空或重复标签吞掉，被动按正式目录展示。
from dataclasses import replace
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from src.features.static_catalog.domain_pages.character_skills import build_action_cards
from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.storage.sqlite.static_catalog_character_queries import StaticCatalogCharacterQueries

NTE_TEST_TIER = "core"
ROOT = Path(__file__).resolve().parents[1]
DATABASE = Path(os.environ.get("NTE_ROLE_CATALOG_TEST_ROOT", str(ROOT / "data/role_catalog"))) / "game_static.sqlite3"


class NewCharacterSkillsTests(unittest.TestCase):
    def setUp(self):
        self.queries = StaticCatalogCharacterQueries(DATABASE)
        self.addCleanup(self.queries.close)
        self.service = StaticCatalogCharacterService(self.queries)

    def test_official_actions_and_passives_are_visible_for_both_characters(self):
        expected = {
            1042: ["掠影", "葬羽冥", "恳请众魂安息", "断罪于我", "鸫歌", "长路", "巡翼"],
            1057: ["弦刃", "律动音浪", "音障爆鸣", "变奏和弦", "绝对音准", "烁星激昂", "随心拨弦"],
        }
        for identity, names in expected.items():
            with self.subTest(identity=identity):
                detail = self.service.get_character_detail(identity)
                cards = build_action_cards(detail, ())
                self.assertEqual(names, [card.title for card in cards])
                self.assertEqual(["A", "E", "Q", "QTE"], [card.slot for card in cards[:4]])
                self.assertEqual([2, 4, None], [passive.unlock_stage for passive in detail.passives])
                self.assertTrue(all(skill.descriptions for skill in detail.skills))
                self.assertTrue(all(passive.descriptions for passive in detail.passives))

    def test_conflicting_or_missing_tags_never_discard_formal_skill_rows(self):
        detail = self.service.get_character_detail(1057)
        for tag in (None, "Ability.Melee"):
            changed = replace(detail, skills=tuple(replace(skill, gameplay_tag=tag) for skill in detail.skills))
            cards = build_action_cards(changed, ())
            self.assertEqual([skill.skill_id for skill in detail.skills],
                             [card.ability_id for card in cards if card.skill is not None])

    def test_logical_character_shared_passives_are_not_duplicated(self):
        for character in self.queries.list_catalog_characters(limit=200):
            detail = self.service.get_character_detail(character["character_id"])
            identities = [passive.ability_id for passive in detail.passives]
            self.assertEqual(len(identities), len(set(identities)), character["character_id"])

    def test_new_catalog_missing_binding_does_not_reuse_legacy_battle_list(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "catalog.sqlite3"
            with closing(sqlite3.connect(f"{DATABASE.resolve().as_uri()}?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(target)) as copy:
                    source.backup(copy)
                    copy.execute("DELETE FROM catalog_character_passive WHERE character_id=1003")
                    copy.commit()
            queries = StaticCatalogCharacterQueries(target)
            try:
                self.assertEqual([], queries.list_catalog_passive_bindings((1003,)))
            finally:
                queries.close()
