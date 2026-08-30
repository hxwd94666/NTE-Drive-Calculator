# 游戏资料库怪物与玩法域的公共行为测试。
from __future__ import annotations

import sqlite3
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.domain.static_catalog_terminology import LocalizedTermRecord
from src.services.static_catalog_monster_service import (
    CatalogFilter,
    FORMULA,
    OFFICIAL,
    UNAVAILABLE,
    StaticCatalogMonsterService,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_catalog_monster_queries import (
    StaticCatalogMonsterQueries,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"
MAINLAND_SNAPSHOT = datetime(2026, 8, 30, 12, 0, 0)
CURRENT_FEAST_PERIOD = "cn_1_3_20260813"
HISTORICAL_FEAST_PERIOD = "cn_1_1_20260612"


class _MonsterTerminologySource:
    def lookup_localized_term(
        self,
        entity_kind: str,
        stable_id: str,
        *,
        context: str | None,
    ) -> LocalizedTermRecord | None:
        if entity_kind == "outer_realm_fight_stage":
            names = {
                "EAbyssFightStage::FirstHalf": "上半场",
                "EAbyssFightStage::SecondHalf": "下半场",
            }
            name = names.get(stable_id)
            return (
                LocalizedTermRecord(
                    entity_kind=entity_kind,
                    canonical_id=stable_id,
                    names={"zh-CN": name},
                    source_kind="ui_state",
                )
                if name is not None
                else None
            )
        if entity_kind == "damage_resistance":
            return LocalizedTermRecord(
                entity_kind=entity_kind,
                canonical_id=stable_id,
                names={} if stable_id == "normal" else {"zh-CN": "中央抗性名称"},
                source_kind=(
                    "name_missing" if stable_id == "normal" else "formal_localization"
                ),
            )
        if entity_kind in {"equipment_attribute", "item"}:
            return LocalizedTermRecord(
                entity_kind=entity_kind,
                canonical_id=stable_id,
                names={"zh-CN": "中央正式名称"},
            )
        return None

    def list_fork_campaigns(self):
        return ()


class StaticCatalogMonsterDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.terminology = StaticCatalogTerminologyService(
            _MonsterTerminologySource()
        )
        self.service = StaticCatalogMonsterService.from_database(
            STATIC_DATABASE,
            terminology_service=self.terminology,
            mainland_now=MAINLAND_SNAPSHOT,
        )

    def tearDown(self) -> None:
        self.service.close()

    def _page(self, play_mode: str, **changes):
        filters = CatalogFilter(play_mode=play_mode, page_size=1, **changes)
        return self.service.list_entries(filters)

    def test_release_schema_coverage_is_exposed_without_mutating_static_data(self):
        self.assertEqual(35, self._page("official_illustrated").total)
        self.assertEqual(4311, self._page("template_profile").total)
        self.assertEqual(7, self._page("world_boss").total)
        self.assertEqual(32, self._page("feast").total)
        self.assertEqual(218, self._page("clone").total)
        self.assertEqual(78, self._page("high_risk").total)

        with StaticCatalogMonsterQueries(STATIC_DATABASE) as queries:
            with self.assertRaises(sqlite3.OperationalError):
                queries._connection.execute("DELETE FROM monster_catalog")

    def test_current_and_next_outer_realm_use_mainland_effective_time(self):
        page = self.service.list_entries(CatalogFilter(
            play_mode="outer_realm",
            release_scope="current_next",
            page_size=200,
        ))
        self.assertEqual(48, page.total)
        states = {entry.release_state for entry in page.items}
        config_ids = {entry.primary_id for entry in page.items}
        self.assertEqual({"current", "next"}, states)
        self.assertEqual({"Abyss_8", "Abyss_9"}, config_ids)

    def test_all_numbered_outer_realm_configs_remain_visible_without_schedule(self):
        page = self.service.list_entries(CatalogFilter(
            play_mode="outer_realm", page_size=200,
        ))
        entries = list(page.items)
        if page.has_more:
            entries.extend(self.service.list_entries(CatalogFilter(
                play_mode="outer_realm", page_size=200, offset=200,
            )).items)
        self.assertEqual(284, page.total)
        self.assertEqual(
            {f"Abyss_{ordinal}" for ordinal in range(1, 13)},
            {entry.primary_id for entry in entries},
        )
        self.assertEqual(
            {"Abyss_1", "Abyss_4", "Abyss_7", "Abyss_10", "Abyss_11", "Abyss_12"},
            {
                entry.primary_id for entry in entries
                if entry.release_state == "unscheduled"
            },
        )
        self.assertNotIn("Abyss_Common", {entry.primary_id for entry in entries})

    def test_outer_member_detail_keeps_spawn_name_and_exact_profile(self):
        detail = self.service.get_detail(
            "outer_member|Abyss_8|12|EAbyssFightStage%3A%3AFirstHalf|0|"
            "Abyss_8_12_0_1|0"
        )
        self.assertIsNotNone(detail)
        self.assertEqual("胶卷-MANISH", detail.entry.title)
        profile = next(
            section for section in detail.sections
            if section.title == "本次出场公式画像"
        )
        values = {value.label: value for value in profile.values}
        self.assertEqual("Abyss_8_12_0_1_Boss_06_BP", values["pack_id"].value)
        self.assertEqual(FORMULA, values["生命基础"].provenance)

    def test_manual_identity_and_formula_profile_are_labeled_separately(self):
        manual = self._page("official_illustrated").items[0]
        manual_detail = self.service.get_detail(manual.key)
        self.assertIsNotNone(manual_detail)
        self.assertEqual(
            OFFICIAL,
            manual_detail.sections[0].values[0].provenance,
        )

        profile = self.service.list_entries(CatalogFilter(
            play_mode="template_profile",
            search="boss_04_BP_DiyBoss",
            page_size=10,
        )).items[0]
        profile_detail = self.service.get_detail(profile.key)
        self.assertIsNotNone(profile_detail)
        formula_values = [
            value
            for section in profile_detail.sections
            for value in section.values
            if value.provenance == FORMULA
        ]
        self.assertTrue(formula_values)
        self.assertIn("共用数值画像", profile_detail.notices[0])

    def test_formula_profile_reports_attack_tier_as_unavailable(self):
        feast = self._page("feast").items[0]
        detail = self.service.get_detail(feast.key)
        attack_tier = next(
            value
            for section in detail.sections
            for value in section.values
            if value.label == "攻击档"
        )
        self.assertEqual(UNAVAILABLE, attack_tier.provenance)
        self.assertIn("schema v30", attack_tier.value)

    def test_profile_to_gameplay_jump_uses_exact_official_reference(self):
        profile = self.service.list_entries(CatalogFilter(
            play_mode="template_profile",
            search="boss_04_BP_DiyBoss",
            page_size=10,
        )).items[0]
        detail = self.service.get_detail(profile.key)
        feast_links = [
            relation
            for relation in detail.relations
            if relation.target_key.startswith("feast|")
        ]
        self.assertEqual(4, len(feast_links))
        self.assertTrue(all(
            relation.relation_kind == "exact_official_template_id"
            for relation in feast_links
        ))

    def test_high_risk_without_difficulty_pool_is_explicitly_unavailable(self):
        page = self.service.list_entries(CatalogFilter(
            play_mode="high_risk",
            search="AdvVision_HeheBear",
            page_size=10,
        ))
        self.assertEqual(6, page.total)
        detail = self.service.get_detail(page.items[0].key)
        unavailable = [
            value
            for section in detail.sections
            for value in section.values
            if value.provenance == UNAVAILABLE
        ]
        self.assertTrue(unavailable)
        self.assertTrue(any("通用回退池" in value.value for value in unavailable))

    def test_exact_ids_and_paths_are_searchable_and_copyable(self):
        feast = self.service.list_entries(CatalogFilter(
            search="DiyBossStage8",
            play_mode="feast",
            page_size=10,
        ))
        self.assertEqual(4, feast.total)
        detail = self.service.get_detail(feast.items[0].key)
        official_ids = [
            value
            for section in detail.sections
            for value in section.values
            if value.copyable and value.provenance == OFFICIAL
        ]
        self.assertTrue(any(value.value == "DiyBossStage8" for value in official_ids))

    def test_clone_difficulty_without_category_remains_visible(self):
        page = self.service.list_entries(CatalogFilter(
            play_mode="clone",
            search="BidKing1",
            page_size=10,
        ))
        self.assertEqual(1, page.total)
        detail = self.service.get_detail(page.items[0].key)
        category = next(
            value
            for section in detail.sections
            for value in section.values
            if value.label == "类目"
        )
        self.assertEqual(UNAVAILABLE, category.provenance)

    def test_player_terms_are_projected_without_overwriting_raw_facts(self):
        feast = self._page("feast").items[0]
        setup = self.service.get_feast_setup(
            CURRENT_FEAST_PERIOD, feast.primary_id
        )
        self.assertIsNotNone(setup)
        first_option = setup.option_groups[0].options[0]
        detail = self.service.get_feast_detail(
            CURRENT_FEAST_PERIOD,
            feast.primary_id,
            setup.default_difficulty_id,
            selected_option_ids=(first_option.option_id,),
        )
        options = next(
            section for section in detail.sections
            if section.title == "已选挑战条件"
        )
        visible_options = [
            value for value in options.values if "路径" not in value.label
        ]
        self.assertTrue(all(value.display_label for value in visible_options))
        self.assertTrue(all(value.display_value for value in visible_options))
        self.assertTrue(all(
            "attack_up" not in value.display_value
            and "resistance_up" not in value.display_value
            and "effect_kind" not in value.value
            for value in visible_options
        ))

        outer = self._page("outer_realm").items[0]
        self.assertIn(outer.secondary_label, {"上半场", "下半场"})

        profile = next(
            self.service.get_detail(relation.target_key)
            for relation in detail.relations
            if relation.target_key.startswith("profile_monster|")
        )
        resistances = [
            value
            for section in profile.sections
            for value in section.values
            if value.label.startswith("抗性 ")
        ]
        self.assertTrue(resistances)
        self.assertTrue(all(value.display_label for value in resistances))
        self.assertTrue(all("_" not in value.display_label for value in resistances))

    def test_feast_setup_defaults_to_x5_and_applies_only_selected_conditions(self):
        setup = self.service.get_feast_setup(
            CURRENT_FEAST_PERIOD, "DiyBossStage8"
        )
        self.assertIsNotNone(setup)
        default = next(
            item for item in setup.difficulties
            if item.difficulty_id == setup.default_difficulty_id
        )
        self.assertEqual(5.0, default.score_rate)
        base = self.service.get_feast_detail(
            CURRENT_FEAST_PERIOD,
            setup.stage_id, setup.default_difficulty_id
        )
        self.assertFalse(any(
            section.title == "已选挑战条件" for section in base.sections
        ))
        health_group = next(
            group for group in setup.option_groups
            if group.display_name == "敌方生命值提升"
        )
        selected = self.service.get_feast_detail(
            CURRENT_FEAST_PERIOD,
            setup.stage_id,
            setup.default_difficulty_id,
            selected_option_ids=(health_group.options[-1].option_id,),
        )
        base_health_up = next(
            value for section in base.sections for value in section.values
            if value.label == "生命加成"
        )
        selected_health_up = next(
            value for section in selected.sections for value in section.values
            if value.label == "生命加成"
        )
        self.assertGreater(float(selected_health_up.value), float(base_health_up.value))
        self.assertEqual(
            1,
            len(next(
                section for section in selected.sections
                if section.title == "已选挑战条件"
            ).values),
        )

    def test_feast_periods_keep_activity_and_challenge_identity_separate(self):
        periods = self.service.list_feast_periods()
        self.assertEqual(
            [CURRENT_FEAST_PERIOD, HISTORICAL_FEAST_PERIOD],
            [period.period_id for period in periods],
        )
        self.assertEqual(["current", "historical"], [
            period.release_state for period in periods
        ])
        self.assertEqual([8, 7], [len(period.challenge_ids) for period in periods])
        self.assertEqual(
            "DiyBossStage111", periods[1].challenge_ids[1]
        )

    def test_historical_feast_restores_formal_boss_profile_and_family_identity(self):
        setup = self.service.get_feast_setup(
            HISTORICAL_FEAST_PERIOD, "DiyBossStage111"
        )
        self.assertIsNotNone(setup)
        self.assertEqual(2, setup.challenge_ordinal)
        self.assertEqual("随心所欲", setup.title)
        self.assertEqual("随心泥", setup.boss_name)
        self.assertEqual("Boss_016_BP_DiyBoss", setup.boss_monster_id)
        self.assertEqual([45, 55, 65, 75], [
            choice.monster_level for choice in setup.difficulties
        ])
        self.assertFalse(setup.option_groups)
        self.assertIn("未保留", setup.condition_note)

        detail = self.service.get_feast_detail(
            HISTORICAL_FEAST_PERIOD,
            setup.stage_id,
            setup.default_difficulty_id,
        )
        self.assertIsNotNone(detail)
        self.assertEqual("争锋赏宴 · 1.1 往期 · 极难 积分", detail.entry.subtitle)
        activity = next(
            section for section in detail.sections if section.title == "活动期"
        )
        self.assertEqual(
            "2026-06-12 10:00—2026-07-02 05:59",
            next(
                value.value for value in activity.values
                if value.label == "大陆服排期"
            ),
        )
        profile = next(
            section for section in detail.sections
            if section.title == "当前选择画像"
        )
        values = {value.label: value.value for value in profile.values}
        self.assertEqual("Boss_016_BP_DiyBoss_DataPack3", values["pack_id"])
        self.assertTrue(any(
            relation.target_key.startswith("profile_monster|")
            for relation in detail.relations
        ))

    def test_witch_blessings_are_complete_choices_with_direct_descriptions(self):
        blessings = self.service.list_witch_blessings()
        self.assertEqual(7, len(blessings))
        details = tuple(self.service.get_detail(entry.key) for entry in blessings)
        self.assertTrue(all(detail is not None for detail in details))
        values = tuple(
            detail.sections[0].values[0]
            for detail in details
            if detail is not None
        )
        self.assertTrue(all(
            value.display_label and value.display_value
            for value in values
        ))
        self.assertTrue(all("<" not in detail.sections[0].note for detail in details))

    def test_outer_season_buffs_expose_two_seasons_and_four_components(self):
        details = tuple(
            self.service.get_detail(f"outer_buff|Abyss_{ordinal}")
            for ordinal in (8, 9)
        )
        self.assertTrue(all(detail is not None for detail in details))
        components = tuple(
            value
            for detail in details
            if detail is not None
            for value in detail.sections[0].values
        )
        self.assertEqual(4, len(components))
        self.assertTrue(all(
            value.display_label and value.display_value
            for value in components
        ))
        self.assertTrue(all("trigger_" not in value.display_value for value in components))

    def test_every_clone_difficulty_has_an_honest_drop_projection_status(self):
        self.assertEqual(
            {"complete": 122, "partial": 36, "unavailable": 60},
            self.service.clone_drop_status_counts(),
        )
        entries = []
        offset = 0
        while True:
            page = self.service.list_entries(CatalogFilter(
                play_mode="clone", page_size=200, offset=offset,
            ))
            entries.extend(page.items)
            if not page.has_more:
                break
            offset += len(page.items)
        statuses = Counter()
        for entry in entries:
            detail = self.service.get_detail(entry.key)
            drops = next(
                section for section in detail.sections
                if section.title == "正式掉落"
            )
            statuses[drops.values[0].value] += 1
            self.assertNotIn("掉落 ID", tuple(
                value.label for section in detail.sections for value in section.values
            ))
        self.assertEqual(self.service.clone_drop_status_counts(), dict(statuses))

    def test_gameplay_buffs_project_complete_rules_without_navigation_contract(self):
        feast_entries = self.service.list_entries(CatalogFilter(
            play_mode="feast", page_size=200,
        )).items
        representatives = {
            entry.primary_id: entry for entry in reversed(feast_entries)
        }
        option_values = []
        for entry in representatives.values():
            setup = self.service.get_feast_setup(
                CURRENT_FEAST_PERIOD, entry.primary_id
            )
            for group in setup.option_groups:
                for option in group.options:
                    detail = self.service.get_feast_detail(
                        CURRENT_FEAST_PERIOD,
                        setup.stage_id,
                        setup.default_difficulty_id,
                        selected_option_ids=(option.option_id,),
                    )
                    option_values.extend(next(
                        section.values for section in detail.sections
                        if section.title == "已选挑战条件"
                    ))
        option_values = tuple(option_values)
        self.assertEqual(144, len(option_values))
        self.assertTrue(all(
            value.display_label and value.display_value
            for value in option_values
        ))
        timed = tuple(value for value in option_values if value.note)
        self.assertEqual(24, len(timed))
        self.assertTrue(all(
            value.note == "挑战时间规则不属于 Buff 乘区。"
            for value in timed
        ))


if __name__ == "__main__":
    unittest.main()
