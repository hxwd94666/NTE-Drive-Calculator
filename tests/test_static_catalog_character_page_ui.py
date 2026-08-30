from __future__ import annotations

import os
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QTableWidget

from src.domain.progression_stamina import (
    IdentificationLevelProjection,
    ProgressionStaminaResult,
    StaminaPlanStatus,
)
from src.features.static_catalog.domain_pages.character_card import CharacterGalleryCard
from src.features.static_catalog.domain_pages.character_page import (
    CharacterCatalogPage,
    build_character_catalog_page,
)
from src.features.static_catalog.domain_pages.character_progression import (
    project_skill_level_requirements,
)
from src.services.static_catalog_character_release_metadata import (
    CharacterReleaseMetadataService,
)
from src.features.static_catalog.domain_pages.character_skills import SkillActionCard
from src.services.static_catalog_character_service import StaticCatalogCharacterService
from src.services.static_catalog_character_models import CostItem
from src.services.static_catalog_terminology_service import (
    LocalizedTermRecord,
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_catalog_character_queries import StaticCatalogCharacterQueries

NTE_TEST_TIER = "core"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class _TerminologySource:
    def lookup_localized_term(self, entity_kind, stable_id, *, context):
        acquisition_names = {
            "permanent": "常驻",
            "limited": "限定",
            "free": "免费获取",
        }
        if entity_kind == "character_acquisition_type":
            name = acquisition_names.get(stable_id)
            if name is None:
                return None
            return LocalizedTermRecord(
                entity_kind=entity_kind,
                canonical_id=stable_id,
                names={"zh-CN": name},
                source_kind=(
                    "reviewed_annotation"
                    if stable_id == "free"
                    else "formal_localization"
                ),
            )
        if (entity_kind, stable_id, context) == (
            "item", "gold", "progression_cost",
        ):
            return LocalizedTermRecord(
                entity_kind="item",
                canonical_id="Fons",
                names={"zh-CN": "方斯"},
                text_table="/Game/Text/ST_Item.ST_Item",
                text_key="item_Fons_name",
            )
        if (entity_kind, stable_id, context) == ("item", "Gold", None):
            return LocalizedTermRecord(
                entity_kind="item",
                canonical_id="Gold",
                names={"zh-CN": "甲硬币"},
                text_table="/Game/Text/ST_Ui.ST_Ui",
                text_key="gold_name",
            )
        return None


class _ReleaseSource:
    _DATES = {
        1004: "2026-05-28",
        1010: "2026-04-23",
        1036: "2026-08-13",
        1052: "2026-05-07",
        1071: "2026-06-18",
        1072: "2026-09-03",
        1075: "2026-07-23",
        1076: "2026-07-02",
    }
    _A_PERMANENT = {1008, 1019, 1020, 1021, 1033, 1070}
    _S_PERMANENT = {1003, 1023, 1025, 1039, 1054, 1055}
    _FREE = {1046, 1051, 1073}

    def list_catalog_character_release_annotations(self):
        rows = []
        for character_id in sorted(
            self._A_PERMANENT
            | self._S_PERMANENT
            | self._FREE
            | set(self._DATES)
        ):
            if character_id in self._A_PERMANENT:
                quality, acquisition_type = "A", "permanent"
            elif character_id in self._S_PERMANENT:
                quality, acquisition_type = "S", "permanent"
            elif character_id in self._FREE:
                quality, acquisition_type = "S", "free"
            else:
                quality, acquisition_type = "S", "limited"
            rows.append({
                "character_id": character_id,
                "quality": quality,
                "quality_source_kind": "reviewed_fallback",
                "acquisition_type": acquisition_type,
                "acquisition_source_kind": "official",
                "mainland_release_date": self._DATES.get(
                    character_id,
                    "2026-04-23",
                ),
                "release_source_kind": "official",
                "evidence_keys": (f"evidence_{character_id}",),
            })
        return rows


class StaticCatalogCharacterPageUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.queries = StaticCatalogCharacterQueries(
            PROJECT_ROOT / "data" / "game_static.sqlite3"
        )
        self.service = StaticCatalogCharacterService(self.queries)
        self.terminology = StaticCatalogTerminologyService(_TerminologySource())
        self.release_service = CharacterReleaseMetadataService(
            _ReleaseSource(),
            self.terminology,
        )
        self.page = build_character_catalog_page(
            service=self.service,
            release_metadata_service=self.release_service,
            game_ui_asset_root=PROJECT_ROOT / "assets" / "game_ui",
            terminology_service=self.terminology,
        )
        self.page.resize(1280, 900)
        self.page.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.page.close()
        self.page.deleteLater()
        self.queries.close()

    def test_gallery_uses_character_cards_and_no_tables(self) -> None:
        cards = self.page.findChildren(CharacterGalleryCard)

        self.assertEqual(22, len(cards))
        self.assertEqual([], self.page.findChildren(QTableWidget))
        self.assertIn("22 位角色", self.page.result_count.text())
        self.assertIsNone(self.page.findChild(QFrame, "characterGalleryHero"))
        self.assertNotIn(1056, self.page._cards)
        self.assertNotIn(1091, self.page._cards)
        visible_text = "\n".join(
            label.text() for label in self.page.findChildren(QLabel)
        )
        self.assertNotIn("schema 29", visible_text)
        self.assertNotIn("importer 34", visible_text)
        self.assertNotIn("Actor 路径", visible_text)
        self.assertNotIn("资源路径", visible_text)
        self.assertNotIn("战斗形态", visible_text)
        protagonist = "\n".join(
            label.text() for label in self.page._cards[1051].findChildren(QLabel)
        )
        self.assertIn("2 种形象", protagonist)
        self.assertIn("ID 1046 / 1051", protagonist)

    def test_filters_name_id_element_quality_and_acquisition(self) -> None:
        self.page.search.setText("1036")
        self.app.processEvents()
        self.assertEqual((1036,), self.page._visible_ids)

        self.page.search.setText("1046")
        self.app.processEvents()
        self.assertEqual((1051,), self.page._visible_ids)

        self.page.search.clear()
        element_button = next(
            button for button in self.page.element_group.buttons()
            if button.property("filterKey") == "咒"
        )
        element_button.click()
        self.app.processEvents()
        self.assertTrue(self.page._visible_ids)
        self.assertTrue(all(
            self.page._cards[character_id].summary.element_label == "咒"
            for character_id in self.page._visible_ids
        ))
        next(
            button for button in self.page.element_group.buttons()
            if button.property("filterKey") == "all"
        ).click()
        self.app.processEvents()

        facets = {
            button.text(): button
            for button in self.page.findChildren(QPushButton)
            if button.text() in {"S 级", "A 级", "常驻", "限定"}
        }
        self.assertEqual({"S 级", "A 级", "常驻", "限定"}, set(facets))
        self.assertTrue(all(button.isEnabled() for button in facets.values()))

        facets["A 级"].click()
        self.app.processEvents()
        self.assertEqual(6, len(self.page._visible_ids))
        self.assertTrue(all(
            self.release_service.metadata(character_id).quality == "A"
            for character_id in self.page._visible_ids
        ))

        facets["S 级"].click()
        facets["限定"].click()
        self.app.processEvents()
        self.assertEqual(8, len(self.page._visible_ids))
        self.assertTrue(all(
            self.release_service.metadata(character_id).acquisition_type
            == "limited"
            for character_id in self.page._visible_ids
        ))

        next(
            button for button in self.page.quality_group.buttons()
            if button.property("filterKey") == "all"
        ).click()
        facets["常驻"].click()
        self.app.processEvents()
        self.assertEqual(12, len(self.page._visible_ids))
        self.assertTrue(all(
            self.release_service.metadata(character_id).acquisition_type
            == "permanent"
            for character_id in self.page._visible_ids
        ))
        self.assertTrue({1046, 1051, 1073}.isdisjoint(self.page._visible_ids))

        free = next(
            button for button in self.page.acquisition_group.buttons()
            if button.property("filterKey") == "free"
        )
        self.assertTrue(free.isEnabled())
        free.click()
        self.app.processEvents()
        self.assertEqual({1051, 1073}, set(self.page._visible_ids))
        self.assertTrue(all(
            self.release_service.metadata(character_id).acquisition_type
            == "free"
            for character_id in self.page._visible_ids
        ))

    def test_filter_panel_is_compact_by_default_and_can_expand(self) -> None:
        filters = self.page.findChild(QFrame, "characterGalleryFilters")
        self.assertIsNotNone(filters)
        assert filters is not None
        self.assertFalse(self.page.filter_body.isVisible())
        collapsed_height = filters.sizeHint().height()
        self.assertIn("展开筛选", self.page.filter_toggle.text())
        self.assertIn("全部角色", self.page.filter_summary.text())

        self.page.filter_toggle.click()
        self.app.processEvents()
        self.assertTrue(self.page.filter_body.isVisible())
        self.assertIn("收起筛选", self.page.filter_toggle.text())
        self.assertGreater(filters.sizeHint().height(), collapsed_height)

        self.page.filter_toggle.click()
        self.app.processEvents()
        self.assertFalse(self.page.filter_body.isVisible())

    def test_scheduled_card_leads_the_shared_responsive_grid(self) -> None:
        visible_text = "\n".join(
            label.text() for label in self.page.gallery_host.findChildren(QLabel)
        )
        self.assertNotIn("待上线预告", visible_text)
        self.assertNotIn("已上线角色", visible_text)
        self.assertNotIn("预告角色独立展示", visible_text)
        self.assertTrue(self.page._cards[1072].property("scheduledCharacter"))
        self.assertFalse(self.page._cards[1036].property("scheduledCharacter"))
        scheduled_position = self.page.card_grid.getItemPosition(
            self.page.card_grid.indexOf(self.page._cards[1072])
        )
        active_position = self.page.card_grid.getItemPosition(
            self.page.card_grid.indexOf(self.page._cards[1036])
        )
        self.assertEqual((0, 0), scheduled_position[:2])
        self.assertEqual((0, 1), active_position[:2])

    def test_scheduled_limited_character_uses_future_facing_copy(self) -> None:
        limited = next(
            button for button in self.page.acquisition_group.buttons()
            if button.property("filterKey") == "limited"
        )
        scheduled = next(
            button for button in self.page.availability_group.buttons()
            if button.property("filterKey") == "scheduled"
        )
        limited.click()
        scheduled.click()
        self.app.processEvents()

        self.assertEqual((1072,), self.page._visible_ids)
        card_text = "\n".join(
            label.text() for label in self.page._cards[1072].findChildren(QLabel)
        )
        self.assertIn("待上线", card_text)
        self.assertIn("预计上线 2026-09-03", card_text)

        self.page.open_character(1072)
        self.app.processEvents()
        detail_text = "\n".join(
            label.text() for label in self.page.detail_view.findChildren(QLabel)
        )
        self.assertIn("限定 · 待上线", detail_text)
        self.assertIn("预计上线 2026-09-03", detail_text)

    def test_gallery_defaults_to_newest_release_first(self) -> None:
        self.assertEqual(
            (1072, 1036, 1075, 1076, 1071, 1004, 1052),
            self.page._visible_ids[:7],
        )
        self.assertEqual(
            tuple(sorted(
                self.page._visible_ids,
                key=lambda character_id: (
                    self.release_service.metadata(character_id).release_date,
                    -character_id,
                ),
                reverse=True,
            )),
            self.page._visible_ids,
        )

    def test_release_metadata_is_data_backed_and_projects_public_terms(self) -> None:
        self.assertTrue(all(
            self.release_service.metadata(character_id) is not None
            for character_id in self.page._cards
        ))
        self.assertIsNone(self.release_service.metadata(1056))
        self.assertIsNone(self.release_service.metadata(1091))
        lacrimosa = self.release_service.metadata(1004)
        self.assertIsNotNone(lacrimosa)
        assert lacrimosa is not None
        self.assertEqual(("S", "limited", "2026-05-28"), (
            lacrimosa.quality,
            lacrimosa.acquisition_type,
            lacrimosa.release_date,
        ))
        self.assertEqual("限定", lacrimosa.acquisition_term.display_name)
        chaos = self.release_service.metadata(1071)
        self.assertIsNotNone(chaos)
        assert chaos is not None
        self.assertEqual("2026-06-18", chaos.release_date)
        for character_id in self.page._cards:
            metadata = self.release_service.metadata(character_id)
            assert metadata is not None
            self.assertEqual((f"evidence_{character_id}",), metadata.evidence_keys)

    def test_profile_uses_official_skill_order_and_only_adds_formal_g(self) -> None:
        self.page.open_character(1036)
        self.app.processEvents()

        self.assertIs(self.page.stack.currentWidget(), self.page.detail_view)
        self.assertEqual("残虹", self.page.detail_view.name.text())
        self.assertLessEqual(self.page.detail_view.hero.maximumHeight(), 214)
        action_cards = self.page.detail_view.skill_view.findChildren(SkillActionCard)
        slots = [card.action.slot for card in action_cards]
        self.assertEqual(
            ["A", "E", "Q", "QTE", "G", "PASSIVE", "PASSIVE"], slots,
        )
        passives = tuple(card for card in action_cards if card.action.passive is not None)
        self.assertEqual(("暮落残阳", "殷红幻景"), tuple(
            card.action.title for card in passives
        ))
        self.assertNotIn("闪避反击", slots)
        self.assertNotIn("R", slots)
        self.assertNotIn("Z", slots)

    def test_profile_projects_player_facing_release_metadata_compactly(self) -> None:
        self.page.open_character(1004)
        self.app.processEvents()

        visible_text = "\n".join(
            label.text() for label in self.page.detail_view.findChildren(QLabel)
        )
        self.assertIn("S 级", visible_text)
        self.assertIn("限定", visible_text)
        self.assertIn("2026-05-28", visible_text)
        self.assertNotIn("ECharacterElementType::", visible_text)
        self.assertNotIn("组别枚举", visible_text)
        for raw_source in (
            "formal_localization",
            "reviewed_annotation",
            "official",
            "reviewed_fallback",
        ):
            self.assertNotIn(raw_source, visible_text)

    def test_profile_uses_formal_full_art_for_all_catalog_variants(self) -> None:
        self.page.open_character(1004)
        self.app.processEvents()

        full_art = self.page.detail_view.art
        self.assertFalse(full_art.pixmap().isNull())
        variants = tuple(
            item for item in self.service.list_characters(limit=200).items
            if item.classification != "combat_transformation"
        )
        self.assertEqual(23, len(variants))
        self.assertTrue(all(
            self.page._asset_catalog.character_art(item.character_id) is not None
            for item in variants
        ))

    def test_protagonist_variants_switch_inside_one_profile(self) -> None:
        self.page.open_character(1051)
        self.app.processEvents()
        buttons = {
            button.text(): button
            for button in self.page.detail_view.findChildren(QPushButton)
            if button.text() in {"男性形象", "女性形象"}
        }
        self.assertEqual({"男性形象", "女性形象"}, set(buttons))
        self.assertFalse(buttons["女性形象"].isEnabled())
        self.assertTrue(buttons["男性形象"].isEnabled())
        buttons["男性形象"].click()
        self.app.processEvents()
        self.assertEqual(1046, self.page._active_character_id)
        self.assertEqual("「零」", self.page.detail_view.name.text())
        self.assertFalse(self.page.detail_view.art.pixmap().isNull())

    def test_profile_reflows_quick_facts_at_narrow_width(self) -> None:
        self.page.open_character(1004)
        self.page.resize(700, 820)
        self.app.processEvents()

        detail = self.page.detail_view
        self.assertTrue(detail._compact)
        self.assertIsNone(detail.findChild(QFrame, "characterQuickFacts"))
        self.assertLessEqual(detail.hero.maximumHeight(), 160)
        self.assertEqual(
            0,
            detail.hero_grid.getItemPosition(
                detail.hero_grid.indexOf(detail.art_panel)
            )[0],
        )

    def test_level_planner_emits_public_progression_request_without_local_result(self) -> None:
        self.page.open_character(1036)
        growth = self.page.detail_view.growth_view
        requests: list[object] = []
        self.page.progression_requested.connect(requests.append)
        growth.start_level.setCurrentIndex(4)
        growth.end_level.setCurrentIndex(69)
        growth.include_breakthroughs.setChecked(False)
        growth._request_progression()

        request = requests[-1]
        self.assertIsInstance(request, dict)
        assert isinstance(request, dict)
        self.assertEqual("character_level", request["kind"])
        self.assertEqual(1036, request["character_id"])
        self.assertEqual((5, 70), (
            request["from_level"],
            request["to_level"],
        ))
        self.assertFalse(request["include_breakthroughs"])
        self.assertEqual((), request["requirements"])
        self.assertEqual("unavailable", request["requirement_status"])
        self.assertEqual(
            ("character_level_cost_unavailable",),
            tuple(gap.reason_code for gap in request["requirement_gaps"]),
        )
        self.assertIn("材料体力计算服务尚未接入", growth.progression_result.text())

        self.assertTrue(self.page.set_progression_result(
            target="character_level",
            character_id=1036,
            result=ProgressionStaminaResult(
                status=StaminaPlanStatus.UNAVAILABLE,
                identification=IdentificationLevelProjection(60, 7, 7, False),
                deficits=(),
                runs=(),
                known_stamina=0,
                total_stamina=None,
                unresolved_item_ids=("CharacterExp",),
                gaps=("material_yield_unavailable",),
            ),
        ))
        self.assertIn("名称暂未提供", growth.progression_result.text())
        self.assertNotIn("CharacterExp", growth.progression_result.text())
        self.assertFalse(growth.findChildren(
            QPushButton, "characterMoreInfoToggle",
        ))

    def test_skill_training_lists_formal_rows_and_delegates_stamina_planning(self) -> None:
        self.page.open_character(1036)
        training = self.page.detail_view.skill_training_view
        requests: list[object] = []
        self.page.progression_requested.connect(requests.append)

        training._request_progression()

        request = requests[-1]
        self.assertIsInstance(request, dict)
        assert isinstance(request, dict)
        self.assertEqual("skill", request["kind"])
        self.assertEqual(1036, request["character_id"])
        self.assertEqual("GA_Zankou_Melee", request["skill_id"])
        self.assertEqual(1, request["from_level"])
        self.assertEqual(10, request["to_level"])
        requirements = {
            item.item_id: item.required_quantity
            for item in request["requirements"]
        }
        self.assertEqual("complete", request["requirement_status"])
        self.assertEqual((), request["requirement_gaps"])
        self.assertEqual(437_000, requirements["Fons"])
        self.assertEqual(10, requirements["SkillUpMaterial_03_lv1"])
        self.assertEqual(10, requirements["OrdinaryMonMaterial_03_lv1"])
        self.assertEqual(8, requirements["weeklycloneboss_a03_01"])
        self.assertIn("升至 Lv.10", " ".join(
            label.text() for label in training.findChildren(QLabel)
        ))

    def test_progression_result_drops_stale_character_and_skill_identity(self) -> None:
        self.page.open_character(1036)
        training = self.page.detail_view.skill_training_view
        result = ProgressionStaminaResult(
            status=StaminaPlanStatus.UNAVAILABLE,
            identification=IdentificationLevelProjection(60, 7, 7, False),
            deficits=(),
            runs=(),
            known_stamina=0,
            total_stamina=None,
            unresolved_item_ids=("SkillMaterial",),
            gaps=("material_yield_unavailable",),
        )
        label = training.findChild(QLabel, "skillProgressionResult")
        self.assertIsNotNone(label)
        assert label is not None
        before = label.text()

        self.assertFalse(self.page.set_progression_result(
            target="skill",
            character_id=1004,
            skill_id="GA_Zankou_Melee",
            result=result,
        ))
        self.assertFalse(self.page.set_progression_result(
            target="skill",
            character_id=1036,
            skill_id="GA_Zankou_Skill",
            result=result,
        ))
        self.assertEqual(before, label.text())
        self.assertTrue(self.page.set_progression_result(
            target="skill",
            character_id=1036,
            skill_id="GA_Zankou_Melee",
            result=result,
        ))
        self.assertNotEqual(before, label.text())

        self.page.open_character(1004)
        self.assertFalse(self.page.set_progression_result(
            target="skill",
            character_id=1036,
            skill_id="GA_Zankou_Melee",
            result=result,
        ))

    def test_skill_requirement_projection_keeps_hidden_quantity_as_gap(self) -> None:
        self.page.open_character(1036)
        action = next(
            card.action
            for card in self.page.detail_view.skill_view.findChildren(
                SkillActionCard
            )
            if card.action.slot == "A"
        )
        assert action.skill is not None
        level_one = next(
            level for level in action.skill.levels if level.level == 1
        )
        hidden_level = replace(
            level_one,
            costs=(CostItem(
                item_id="HiddenFormalItem",
                quantity=0,
                hidden_amount=True,
            ),),
        )
        skill = replace(action.skill, levels=(hidden_level,))

        projection = project_skill_level_requirements(
            skill,
            from_level=1,
            to_level=2,
            terminology=self.terminology,
        )

        self.assertEqual("unavailable", projection.status.value)
        self.assertEqual((), projection.requirements)
        self.assertEqual(
            (("skill_cost_quantity_hidden", 2, "HiddenFormalItem"),),
            tuple(
                (gap.reason_code, gap.level, gap.item_id)
                for gap in projection.gaps
            ),
        )

    def test_raw_skill_and_awakening_ids_are_omitted(self) -> None:
        self.page.open_character(1036)
        detail = self.page.detail_view
        detail.tabs.setCurrentWidget(detail.skill_view)
        a_card = next(
            card for card in detail.skill_view.findChildren(SkillActionCard)
            if card.action.slot == "A"
        )
        a_card.toggle.click()
        self.app.processEvents()

        visible_skill_text = "\n".join(
            label.text()
            for label in a_card.findChildren(QLabel)
            if label.isVisibleTo(a_card)
        )
        skill_raw_ids = tuple(
            skill.skill_id for skill in detail._detail.skills
        ) if detail._detail is not None else ()
        self.assertTrue(all(
            raw_id not in visible_skill_text for raw_id in skill_raw_ids
        ))
        self.assertFalse(a_card.findChildren(
            QPushButton, "characterMoreInfoToggle",
        ))

        detail.tabs.setCurrentWidget(detail.awakening_view)
        self.app.processEvents()
        profile = detail._detail
        self.assertIsNotNone(profile)
        assert profile is not None
        raw_effect_ids = tuple(
            value
            for awakening in profile.awakenings
            for value in (
                awakening.effect_id,
                awakening.awaken_type,
                *awakening.gameplay_effect_ids,
            )
            if value
        )
        visible_awaken_text = "\n".join(
            label.text()
            for label in detail.awakening_view.findChildren(QLabel)
            if label.isVisibleTo(detail.awakening_view)
        )
        self.assertTrue(all(
            raw_id not in visible_awaken_text for raw_id in raw_effect_ids
        ))
        self.assertFalse(detail.awakening_view.findChildren(
            QPushButton, "characterMoreInfoToggle",
        ))
        self.assertFalse(any(
            button.text().startswith(("查看 Buff", "查看 Gameplay"))
            for button in detail.awakening_view.findChildren(QPushButton)
        ))

        detail.tabs.setCurrentWidget(detail.affinity_host)
        self.app.processEvents()
        affinity_text = "\n".join(
            label.text()
            for label in detail.affinity_host.findChildren(QLabel)
            if label.isVisibleTo(detail.affinity_host)
        )
        if profile.likeability is not None:
            affinity_raw = (
                profile.likeability.modify_data_id,
                *(item.property_id for item in profile.likeability.properties),
                *(item.modifier_operation for item in profile.likeability.properties),
            )
            self.assertTrue(all(
                raw_id not in affinity_text for raw_id in affinity_raw
            ))

        detail.tabs.setCurrentWidget(detail.route_view)
        self.app.processEvents()
        route_text = "\n".join(
            label.text()
            for label in detail.route_view.findChildren(QLabel)
            if label.isVisibleTo(detail.route_view)
        )
        route_raw: list[str] = []
        if profile.cultivation is not None:
            for stage in profile.cultivation.stages:
                route_raw.append(stage.core_item_id)
                route_raw.extend(
                    skill_id
                    for _slot, skill_id, _level in stage.recommended_skills
                )
        if profile.graduation is not None:
            route_raw.extend(filter(None, (
                profile.graduation.fork_id,
                profile.graduation.core_suit_id,
                profile.graduation.core_main_property_id,
            )))
        self.assertTrue(all(raw_id not in route_text for raw_id in route_raw))
        self.assertNotIn("default ", route_text)
        self.assertFalse(detail.route_view.findChildren(
            QPushButton, "characterCatalogLink",
        ))

    def test_skill_rows_and_drawers_fit_common_widths(self) -> None:
        self.page.open_character(1036)
        detail = self.page.detail_view
        detail.tabs.setCurrentWidget(detail.skill_view)
        for width in (1180, 720):
            self.page.resize(width, 820)
            self.app.processEvents()
            cards = tuple(
                card for card in detail.skill_view.findChildren(SkillActionCard)
                if card.action.character_id == 1036
            )
            slots = {card.action.slot for card in cards}
            self.assertEqual({"A", "E", "Q", "QTE", "G", "PASSIVE"}, slots)
            for card in cards:
                self.assertGreater(card.width(), detail.skill_view.width() * 0.8)
            a_card = next(card for card in cards if card.action.slot == "A")
            a_card.set_expanded(True)
            self.app.processEvents()
            drawer = a_card.drawer
            self.assertTrue(drawer.isVisibleTo(detail.skill_view))
            self.assertLessEqual(drawer.width(), a_card.width())

    def test_factory_returns_public_character_page(self) -> None:
        self.assertIsInstance(self.page, CharacterCatalogPage)


if __name__ == "__main__":
    unittest.main()
