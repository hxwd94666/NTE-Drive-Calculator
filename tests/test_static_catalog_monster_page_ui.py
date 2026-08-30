# 游戏资料库怪物与玩法独立页面的公共 UI 行为测试。
from __future__ import annotations

import os
import unittest
from collections import Counter
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QPushButton,
    QTableView,
    QTableWidget,
)

from src.domain.static_catalog_terminology import LocalizedTermRecord
from src.features.static_catalog.domain_pages.monster_page import (
    MonsterCatalogPageController,
    build_monster_catalog_page,
)
from src.services.static_catalog_monster_service import (
    FORMULA,
    CatalogDetail,
    CatalogEntry,
    CatalogPage,
    CatalogSection,
    CatalogValue,
    StaticCatalogMonsterService,
)
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_DATABASE = PROJECT_ROOT / "data" / "game_static.sqlite3"
ASSET_ROOT = PROJECT_ROOT / "assets" / "game_ui"
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

    def test_home_has_six_formal_play_groups_and_no_tables(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        labels = {
            card.property("playModeLabel")
            for card in page.findChildren(type(page.play_group_cards[0]))
            if card.property("playModeLabel")
        }
        self.assertEqual(
            {
                "大世界图鉴",
                "争锋赏宴",
                "当前 / 下一期轨外之境",
                "材料与养成副本",
                "异象追猎",
                "具有正式怪物池的高危委托",
            },
            labels,
        )
        self.assertFalse(page.findChildren(QTableView))
        self.assertFalse(page.findChildren(QTableWidget))
        visible_text = " ".join(
            label.text() for label in page.home.findChildren(type(page.result_count))
        ) if hasattr(page, "result_count") else " ".join(
            label.text() for label in page.home.findChildren(type(page.browser_title))
        )
        self.assertNotIn("schema", visible_text.casefold())
        self.assertNotIn("importer", visible_text.casefold())
        self.assertNotIn("条正式记录", visible_text)
        self.assertIsNone(page.catalog_back_label())
        page.play_group_cards[0].model.action()
        self.assertIsNotNone(page.catalog_back_label())
        self.assertTrue(page.catalog_go_back())
        self.assertIsNone(page.catalog_back_label())
        page.deleteLater()

    def test_home_reflows_to_two_then_one_column(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        page.resize(700, 900)
        page._layout_home(force=True)
        self.assertEqual("narrow", page._home_layout_bucket)
        self.assertEqual((0, 1), page.home_grid.getItemPosition(1)[:2])
        page.resize(480, 900)
        page._layout_home(force=True)
        self.assertEqual("compact", page._home_layout_bucket)
        self.assertEqual((1, 0), page.home_grid.getItemPosition(1)[:2])
        page.deleteLater()

    def test_outer_realm_groups_current_next_and_history_in_formal_order(self) -> None:
        rotations = self.controller.outer_rotations()
        current = [row for row in rotations if row.release_state == "current"]
        upcoming = [row for row in rotations if row.release_state in {"next", "scheduled"}]
        history = [row for row in rotations if row.release_state == "historical"]
        self.assertEqual(["Abyss_8"], [row.primary_id for row in current])
        self.assertEqual(["Abyss_9"], [row.primary_id for row in upcoming])
        self.assertEqual(
            sorted((row.primary_id for row in history), reverse=True),
            [row.primary_id for row in history],
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
        self.assertEqual(8, len(page._active_state.sections[0].cards))
        page._active_state.sections[0].cards[0].action()
        self.assertIs(page.feast_view, page.stack.currentWidget())
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

    def test_feast_narrow_layout_clears_stale_column_stretch(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        page.open_mode("feast")
        page._active_state.sections[0].cards[0].action()
        page.feast_view.resize(1100, 900)
        page.feast_view._layout_options(force=True)
        self.assertEqual(1, page.feast_view.options_layout.columnStretch(2))
        page.feast_view.resize(600, 900)
        page.feast_view._layout_options(force=True)
        self.assertEqual(0, page.feast_view.options_layout.columnStretch(1))
        self.assertEqual(0, page.feast_view.options_layout.columnStretch(2))
        page.deleteLater()

    def test_home_and_encounter_cards_use_packaged_formal_images(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        self.assertTrue(all(card.model.icon for card in page.play_group_cards))
        feast = self.controller.entries_for("feast")[0]
        detail = self.controller.detail(feast.key)
        self.assertTrue(page._formal_icon(detail).is_file())
        page.deleteLater()

    def test_search_and_secondary_filters_are_progressively_disclosed(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        page.open_mode("outer_realm")
        self.assertFalse(page.more_filters.isVisible())
        self.assertEqual("搜索怪物或玩法", page.browser_search.placeholderText())
        self.assertEqual(3, len(page._active_state.sections[-1].cards[:3]))
        self.assertEqual(3, page._active_state.sections[-1].initial_limit)
        page.deleteLater()

    def test_primary_category_filters_use_formal_card_categories(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        page.open_mode("clone")
        expected = {
            card.category
            for section in page._active_state.sections
            for card in section.cards
            if card.category
        }
        button_text = {
            button.text() for button in page._category_group.buttons()
            if button.text() != "全部"
        }
        self.assertEqual(expected, button_text)
        self.assertGreater(len(button_text), 1)
        page.deleteLater()

    def test_outer_cards_switch_to_narrow_layout_bucket(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        page.resize(760, 900)
        page.show()
        self.app.processEvents()
        page.open_mode("outer_realm")
        self.app.processEvents()
        self.assertEqual("narrow", page._browser_layout_bucket)
        page.close()
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
        self.assertLessEqual(hero.height(), 160)
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

    def test_witch_blessing_is_embedded_without_mechanism_navigation(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        page.open_mode("feast")
        page._active_state.sections[0].cards[0].action()
        page.feast_view.blessing_toggle.setChecked(True)
        page.feast_view.blessing_combo.setCurrentIndex(1)
        self.assertTrue(page.feast_view.blessing_summary.text())
        self.assertIsNone(page.feast_view.blessing_host.findChild(
            QPushButton, "monsterMechanicsLink",
        ))
        page.deleteLater()

    def test_enemy_relation_icon_coverage_uses_formal_variant_fallbacks(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        cards = []
        for mode in (
            "official_illustrated", "outer_realm", "clone",
            "world_boss", "high_risk",
        ):
            for entry in self.controller.entries_for(mode):
                detail = self.controller.detail(entry.key)
                cards.extend(
                    (mode, card) for card in page._monster_cards(detail, entry)
                )
        self.assertEqual(956, len(cards))
        self.assertEqual(43, sum(card.unavailable for _mode, card in cards))
        self.assertEqual(872, sum(
            card.icon is not None for _mode, card in cards
        ))
        missing = Counter(
            (mode, _formal_family(card.formal_id))
            for mode, card in cards
            if card.icon is None and not card.unavailable
        )
        self.assertEqual(
            {
                ("clone", "boss:16"): 4,
                ("clone", "boss:32"): 1,
                ("clone", "mon:8"): 3,
                ("high_risk", "boss:6"): 6,
                ("outer_realm", "boss:6"): 4,
                ("outer_realm", "mon:8"): 14,
                ("outer_realm", "mon:22"): 5,
                ("outer_realm", "mon:39"): 1,
                ("outer_realm", "mon:51"): 3,
            },
            dict(missing),
        )
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
        self.assertEqual(1, page.detail_view.profile_layout.count())
        self.assertNotIn(
            "其他难度 / 档位",
            " ".join(label.text() for label in page.detail_view.findChildren(QLabel)),
        )
        page.deleteLater()

    def test_outer_rotation_opens_season_buff_with_structured_components(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        current = next(
            row for row in self.controller.outer_rotations()
            if row.release_state == "current"
        )
        rows = tuple(
            row for row in self.controller.entries_for("outer_realm")
            if row.primary_id == current.primary_id
        )
        page._open_rotation(rows)
        self.assertEqual("本期规则", page._active_state.sections[0].title)
        page._active_state.sections[0].cards[0].action()
        self.assertEqual(2, len(page.detail_view.buff_cards))
        self.assertTrue(all(
            card.findChild(QPushButton, "monsterMechanicsLink") is None
            for card in page.detail_view.buff_cards
        ))
        self.assertTrue(all(
            "#231f13" not in card.styleSheet().casefold()
            for card in page.detail_view.buff_cards
        ))
        page.deleteLater()

    def test_clone_detail_renders_drop_cards_without_raw_drop_identity(self) -> None:
        page = build_monster_catalog_page(
            service=self.service,
            terminology_service=self.terminology,
            game_ui_asset_root=ASSET_ROOT,
        )
        entry = self.controller.entries_for("clone")[0]
        page.open_record(entry.key)
        self.assertTrue(page.detail_view.drop_cards)
        visible = " ".join(
            label.text()
            for card in page.detail_view.drop_cards
            for label in card.findChildren(type(page.detail_view.crumb))
        )
        self.assertNotIn("drop_id", visible.casefold())
        self.assertNotIn("/Game/", visible)
        detail_text = " ".join(
            label.text() for label in page.detail_view.findChildren(QLabel)
        )
        self.assertNotIn("正式 ID", detail_text)
        self.assertNotIn("更多信息", detail_text)
        self.assertFalse(any(
            button.text().startswith("查看正式关联画像")
            for button in page.detail_view.findChildren(QPushButton)
        ))
        page.deleteLater()


def _formal_family(formal_id: str) -> str:
    parts = str(formal_id).strip().casefold().split("_")
    if len(parts) < 2 or parts[0] not in {"mon", "boss"}:
        return "unavailable"
    try:
        return f"{parts[0]}:{int(parts[1])}"
    except ValueError:
        return "unavailable"


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
