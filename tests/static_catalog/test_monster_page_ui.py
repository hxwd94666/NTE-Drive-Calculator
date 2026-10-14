# 游戏资料库怪物与玩法独立页面的公共 UI 行为测试。
from __future__ import annotations

import os
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
)

from src.domain.static_catalog_terminology import LocalizedTermRecord
from src.features.static_catalog.domain_pages.monsters.controller import MonsterCatalogPageController
from src.features.static_catalog.domain_pages.monsters.page import (
    build_monster_catalog_page,
)
from src.services.static_catalog.monsters.service import (
    FORMULA,
    CatalogDetail,
    CatalogEntry,
    CatalogPage,
    CatalogSection,
    CatalogValue,
    StaticCatalogMonsterService,
)
from src.services.static_catalog.common.terminology import (
    StaticCatalogTerminologyService,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"
ASSET_ROOT = PROJECT_ROOT / "data" / "role_catalog" / "game_ui"
MAINLAND_SNAPSHOT = datetime(2026, 8, 30, 12, 0, 0)


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


def _terminology_service() -> StaticCatalogTerminologyService:
    return StaticCatalogTerminologyService(_MonsterTerminologySource())


class StaticCatalogMonsterPageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.terminology = _terminology_service()
        self.service = StaticCatalogMonsterService.from_database(
            STATIC_DATABASE,
            terminology_service=self.terminology,
            mainland_now=MAINLAND_SNAPSHOT,
        )
        self.controller = MonsterCatalogPageController(self.service)

    def tearDown(self) -> None:
        self.service.close()

    def test_home_entry_and_back_preserve_public_navigation(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        self.assertIsNone(page.catalog_back_label())
        page.play_group_cards[0].model.action()
        self.assertIsNotNone(page.catalog_back_label())
        self.assertTrue(page.catalog_go_back())
        self.assertIsNone(page.catalog_back_label())
        page.deleteLater()


    def test_outer_realm_groups_scheduled_and_unscheduled_configs(self) -> None:
        rotations = self.controller.outer_rotations()
        current = [row for row in rotations if row.release_state == "current"]
        upcoming = [row for row in rotations if row.release_state in {"next", "scheduled"}]
        history = [row for row in rotations if row.release_state == "historical"]
        unscheduled = [row for row in rotations if row.release_state == "unscheduled"]
        self.assertEqual(["Abyss_8"], [row.primary_id for row in current])
        self.assertEqual(["Abyss_9", "Abyss_10", "Abyss_11", "Abyss_12"], [row.primary_id for row in upcoming])
        self.assertEqual(
            sorted((row.primary_id for row in history), reverse=True),
            [row.primary_id for row in history],
        )
        self.assertEqual(
            ["Abyss_1", "Abyss_4", "Abyss_7"],
            [row.primary_id for row in unscheduled],
        )

    def test_high_risk_home_scope_keeps_only_difficulty_formal_pools(self) -> None:
        entries = self.controller.entries_for("high_risk")
        self.assertTrue(entries)
        for entry in entries:
            detail = self.controller.detail(entry.key)
            self.assertIsNotNone(detail)
            value = self.controller.value(detail, "逐难度怪物池")
            self.assertNotIn(value, {None, "", "不可用"})


    def test_formal_icon_binding_uses_profile_relation_not_display_name(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        entry = self.controller.entries_for("world_boss")[0]
        detail = self.controller.detail(entry.key)
        self.assertIsNotNone(detail)
        candidates = page.formal_icon_candidates(detail)
        self.assertTrue(all(key.startswith("profile_monster|") for key in candidates))
        self.assertNotIn(detail.entry.title, " ".join(candidates))
        page.deleteLater()

    def test_feast_stage_defaults_to_highest_difficulty_and_no_conditions(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        page.open_mode("feast")
        self.assertEqual(1, len(page._active_state.sections[0].cards))
        page._active_state.sections[0].cards[0].action()
        self.assertEqual(8, len(page._active_state.sections[0].cards))
        page._active_state.sections[0].cards[0].action()
        self.assertIs(page.feast_view, page.stack.currentWidget())
        self.assertIn("1.3 期 · 挑战 1", page.feast_view.heading.text())
        self.assertIn("积分倍率x5", page.feast_view.difficulty_combo.currentText())
        self.assertTrue(all(
            combo.currentData() == "" for combo in page.feast_view._option_combos
        ))
        self.assertTrue(page.feast_view.options_host.isHidden())
        self.assertEqual(
            "未启用挑战条件",
            page.feast_view.condition_summary.text(),
        )
        self.assertFalse(page.feast_view.detail.buff_cards)
        page.feast_view.conditions_toggle.setChecked(True)
        self.assertFalse(page.feast_view.options_host.isHidden())
        page.feast_view._option_combos[0].setCurrentIndex(1)
        self.assertEqual("已启用 1 项", page.feast_view.condition_summary.text())
        self.assertEqual(1, len(page.feast_view.detail.buff_cards))
        self.assertEqual(8, page.feast_view.blessing_combo.count())
        page.feast_view.blessing_toggle.setChecked(True)
        self.assertFalse(page.feast_view.blessing_host.isHidden())
        page.feast_view.blessing_combo.setCurrentIndex(1)
        self.assertNotEqual(
            "未选择魔女赐福", page.feast_view.blessing_summary.text(),
        )
        page.deleteLater()


    def test_historical_feast_preserves_unavailable_conditions(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        page.open_mode("feast")
        page._active_state.sections[1].cards[0].action()
        cards = page._active_state.sections[0].cards
        cards[1].action()
        self.assertFalse(page.feast_view.conditions_toggle.isVisible())
        self.assertIn("未保留", page.feast_view.condition_summary.text())
        self.assertTrue(page.feast_view.detail.stat_cards)
        page.deleteLater()


    def test_profile_detail_has_gameplay_stats_and_resistance_cards(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        entry = self.controller.entries_for("world_boss")[0]
        encounter = self.controller.detail(entry.key)
        target = page.formal_icon_candidates(encounter)[0]
        profile = self.controller.detail(target)
        self.assertIsNotNone(profile)
        page.open_detail(profile)
        labels = {
            card.property("statLabel")
            for card in page.detail_view.stat_cards
        }
        self.assertTrue({"生命", "防御", "倾陷"}.issubset(labels))
        self.assertTrue(page.detail_view.resistance_cards)
        hero = page.detail_view.findChild(QFrame, "monsterDetailHero")
        visible_labels = " ".join(
            label.text() for label in hero.findChildren(type(page.detail_view.crumb))
        )
        self.assertNotIn(profile.entry.primary_id, visible_labels)
        page.deleteLater()

    def test_default_player_views_hide_raw_identifiers_and_internal_terms(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        feast = self.controller.entries_for("feast")[0]
        page._open_encounter(feast)
        buff_text = " ".join(
            label.text()
            for card in page.feast_view.detail.buff_cards
            for label in card.findChildren(type(page.feast_view.detail.crumb))
        )
        self.assertNotIn(feast.primary_id, buff_text)
        for raw_term in (
            "attack_up", "health_up", "resistance_up", "time_limit",
            "/Game/", "_BP", "GameplayTag",
        ):
            self.assertNotIn(raw_term.casefold(), buff_text.casefold())

        page.open_mode("outer_realm")
        page._open_rotation((
            next(row for row in self.controller.outer_rotations()
                 if row.release_state == "current"),
        ))
        state_text = " ".join((
            page._active_state.title,
            page._active_state.subtitle,
            *(card.title for section in page._active_state.sections for card in section.cards),
            *(card.subtitle for section in page._active_state.sections for card in section.cards),
        ))
        self.assertNotIn("Abyss_", state_text)
        self.assertNotIn("FirstHalf", state_text)
        self.assertNotIn("SecondHalf", state_text)
        page.deleteLater()

    def test_missing_monster_names_never_fall_back_to_formal_id(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        cards = []
        for mode in ("outer_realm", "clone", "high_risk"):
            for entry in self.controller.entries_for(mode):
                detail = self.controller.detail(entry.key)
                cards.extend(page._monster_cards(detail, entry))
        self.assertTrue(cards)
        self.assertTrue(all(card.title != card.formal_id for card in cards))
        self.assertTrue(all("_BP" not in card.title for card in cards))
        self.assertTrue(any(card.title == "名称暂未提供" for card in cards))
        page.deleteLater()


    def test_outer_realm_members_keep_formal_profiles_and_family_icons(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        cards = []
        for entry in self.controller.entries_for("outer_realm"):
            detail = self.controller.detail(entry.key)
            cards.extend(page._monster_cards(detail, entry))
        self.assertTrue(cards)
        self.assertTrue(all(not card.unavailable for card in cards))
        self.assertTrue(all(card.icon is not None for card in cards))
        manish = next(card for card in cards if card.title == "胶卷-MANISH")
        manish.action()
        self.assertIn(
            "胶卷-MANISH",
            {label.text() for label in page.detail_view.findChildren(QLabel)},
        )
        self.assertTrue(page.detail_view.stat_cards)
        page.deleteLater()

    def test_open_world_profile_uses_one_highest_level_selection(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        entry = self.controller.entries_for("official_illustrated")[0]
        page._open_encounter(entry)
        card = next(
            card for section in page._active_state.sections for card in section.cards
            if card.action is not None
        )
        card.action()
        combo = page.detail_view.world_level_combo
        levels = [float(combo.itemText(index).split()[-1]) for index in range(combo.count())]
        self.assertEqual(len(levels), len(set(levels)))
        self.assertEqual(max(levels), float(combo.currentText().split()[-1]))
        page.deleteLater()


class _StubMonsterService:
    def __init__(self, terminology_service: StaticCatalogTerminologyService) -> None:
        self.terminology_service = terminology_service
        entry = CatalogEntry(
            key="feast|typed-stage|1",
            domain="encounter",
            play_mode="feast",
            title="测试挑战对象",
            subtitle="争锋 · 难度 1",
            primary_id="typed-stage",
        )
        profile = CatalogSection("本难度公式画像", (
            CatalogValue("怪物等级", "72", FORMULA),
            CatalogValue("生命基础", "1000", FORMULA),
            CatalogValue("防御基础", "500", FORMULA),
            CatalogValue("倾陷上限", "40", FORMULA),
        ))
        self.detail = CatalogDetail(entry, (profile,))

    def list_entries(self, filters) -> CatalogPage:
        return CatalogPage((), 0, filters.offset, filters.page_size, False)

    def list_feast_periods(self) -> tuple[object, ...]:
        return ()

    def get_detail(self, key: str) -> CatalogDetail | None:
        return self.detail if key == self.detail.entry.key else None


class StaticCatalogMonsterOpenRecordUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_public_factory_and_open_record_keep_page_navigation_contract(self) -> None:
        terminology = _terminology_service()
        service = _StubMonsterService(terminology)
        page = build_monster_catalog_page(
            service=service,
            terminology_service=terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        self.assertTrue(page.open_record(service.detail.entry.key))
        self.assertIs(page.detail_view, page.stack.currentWidget())
        self.assertTrue(page.detail_view.stat_cards)
        self.assertFalse(page.open_record("manual_monster|missing"))
        page.deleteLater()


if __name__ == "__main__":
    unittest.main()
