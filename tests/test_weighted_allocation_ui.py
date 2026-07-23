# 覆盖精简词条配装页面的角色选择和内部上下文请求边界。
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

from src.features.weighted_allocation import page
from src.features.inventory import page as inventory_page
from src.features.weighted_allocation.runner import (
    WeightedAllocationPersistence, WeightedAllocationRequest,
    WeightedSavedAssignmentSignature, WeightedSavedPlanSignature,
    read_weighted_allocation_persistence, run_weighted_allocation,
    replace_weighted_allocation_assignment, save_weighted_allocation_preview,
)
from src.services.allocation_context import (
    AllocationCandidate, OfficialShape, OfficialShapeCell, OfficialStat,
    StaticDatasetReference,
)
from src.services.allocation_solver import AllocationAssignment, AllocationSolveResult, RoleAllocationOption, UnifiedAllocation
from src.ui.main_window_mixins import AllocationResultsMixin
from src.storage.sqlite.user_data_dao import UserDataDao


class WeightedAllocationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_page_exposes_only_role_selection_and_primary_actions(self):
        class Host(QWidget):
            def _card(self, _title):
                card = QWidget(); QVBoxLayout(card); return card
        host = Host()
        with patch.object(page, "refresh_weighted_allocation_page"):
            built = page.build_weighted_allocation_page(host)
        texts = [button.text() for button in built.findChildren(page.QPushButton)]
        self.assertIn("开始计算", texts)
        self.assertIn("保存方案", texts)
        self.assertIn("一键装配", texts)
        self.assertIn("自动装配", texts)
        self.assertNotIn("新建 / 复制配装配置", texts)

    def test_selection_rows_follow_legacy_role_selector_order_and_groups(self):
        host = MagicMock()
        selector = MagicMock()
        selector.get_selected.return_value = ["早雾", "娜娜莉", "九原"]
        selector.get_priority_groups.return_value = [["早雾"], ["娜娜莉", "九原"]]
        host.weighted_role_selector = selector
        host._weighted_role_ids = {"早雾": 1003, "娜娜莉": 1010, "九原": 1055}
        host._weighted_default_suits = {}
        host._weighted_default_property_weights = {}
        host._weighted_preference_overrides = {}
        rows = page._selection_rows(host)
        self.assertEqual([1003, 1010, 1055], [row["character_id"] for row in rows])
        self.assertEqual([0, 1, 1], [row["priority_group"] for row in rows])
        self.assertTrue(all(row["suit_requirement_mode"] == "none" for row in rows))

    def test_selection_rows_keep_the_official_set_but_do_not_default_the_main_stat(self):
        host = MagicMock()
        selector = MagicMock()
        selector.get_selected.return_value = ["\u300c\u96f6\u300d", "\u4e5d\u539f"]
        selector.get_priority_groups.return_value = [["\u300c\u96f6\u300d", "\u4e5d\u539f"]]
        host.weighted_role_selector = selector
        host._weighted_role_ids = {"\u300c\u96f6\u300d": 1051, "\u4e5d\u539f": 1055}
        host._weighted_default_suits = {1051: "Suit6", 1055: "Suit2"}
        host._weighted_default_property_weights = {
            1051: {"CritBase": 1.0}, 1055: {"CritDamageBase": 1.0},
        }
        host._weighted_preference_overrides = {}

        rows = page._selection_rows(host)

        self.assertEqual(["four_piece", "four_piece"], [row["suit_requirement_mode"] for row in rows])
        self.assertEqual(["Suit6", "Suit2"], [row["target_suit_id"] for row in rows])
        self.assertEqual([None, None], [row["core_main_property_id"] for row in rows])

    def test_explicit_empty_curtain_preference_does_not_rewrite_numeric_weights(self):
        host = MagicMock()
        selector = MagicMock()
        selector.get_selected.return_value = ["\u300c\u96f6\u300d"]
        selector.get_priority_groups.return_value = [["\u300c\u96f6\u300d"]]
        host.weighted_role_selector = selector
        host._weighted_role_ids = {"\u300c\u96f6\u300d": 1051}
        host._weighted_default_suits = {1051: "Suit6"}
        host._weighted_default_property_weights = {1051: {"DamageUpCosmosBase": 1.0}}
        host._weighted_preference_overrides = {1051: {
            "target_suit_id": "Suit4",
            "core_main_property_id": "CritBase",
            "substat_priorities": ["CritBase", "AtkUp"],
        }}

        row = page._selection_rows(host)[0]

        self.assertEqual("four_piece", row["suit_requirement_mode"])
        self.assertEqual("Suit4", row["target_suit_id"])
        self.assertEqual("CritBase", row["core_main_property_id"])
        self.assertEqual(["CritBase", "AtkUp"], row["substat_priorities"])
        self.assertEqual(
            {"DamageUpCosmosBase": 1.0}, row["property_weights"]
        )

    def test_selection_rows_preserve_hidden_v5_fields_loaded_from_sqlite(self):
        host = MagicMock()
        host.weighted_role_selector.get_selected.return_value = ["「零」"]
        host.weighted_role_selector.get_priority_groups.return_value = [["「零」"]]
        host._weighted_role_ids = {"「零」": 1051}
        host._weighted_default_suits = {1051: "Suit6"}
        host._weighted_default_property_weights = {1051: {"CritBase": 1.0}}
        host._weighted_preference_overrides = {1051: {
            "target_suit_id": "Suit6",
            "suit_requirement_mode": "two_piece",
            "property_weights": {"CritBase": 0.9, "AtkUp": 0.6},
            "substat_priorities": [],
            "property_limits": {"CritBase": {"minimum": None, "maximum": 0.8}},
        }}

        row = page._selection_rows(host)[0]

        self.assertEqual("two_piece", row["suit_requirement_mode"])
        self.assertEqual({"CritBase": 0.9, "AtkUp": 0.6}, row["property_weights"])
        self.assertEqual(
            {"CritBase": {"minimum": None, "maximum": 0.8}}, row["property_limits"],
        )

    def test_sqlite_preferences_restore_selector_order_groups_and_overrides(self):
        selector = MagicMock(selected=[], priority_links=[])
        selector.search.text.return_value = ""
        host = MagicMock(
            weighted_role_selector=selector,
            _weighted_role_names={1051: "「零」", 1055: "九原", 1003: "早雾"},
        )
        state = WeightedAllocationPersistence(
            Path("account.sqlite"), 2, 16,
            (
                {"character_id": 1051, "ordinal": 0, "priority_group": 0,
                 "target_suit_id": "Suit6", "suit_requirement_mode": "four_piece",
                 "core_main_property_id": None, "property_weights": {"CritBase": 1.0},
                 "substat_priorities": [], "property_limits": {}},
                {"character_id": 1055, "ordinal": 1, "priority_group": 0,
                 "target_suit_id": "Suit2", "suit_requirement_mode": "four_piece",
                 "core_main_property_id": "CritDamageBase", "property_weights": {},
                 "substat_priorities": ["AtkUp"], "property_limits": {}},
                {"character_id": 1003, "ordinal": 2, "priority_group": 1,
                 "target_suit_id": None, "suit_requirement_mode": "none",
                 "core_main_property_id": None, "property_weights": {},
                 "substat_priorities": [], "property_limits": {}},
            ),
            None, (), None,
        )

        page._apply_weighted_persisted_preferences(host, state)

        self.assertEqual(["「零」", "九原", "早雾"], selector.selected)
        self.assertEqual(["=", ">>"], selector.priority_links)
        self.assertEqual("CritDamageBase", host._weighted_preference_overrides[1055]["core_main_property_id"])
        selector._render_grid.assert_called_once_with("")

    def test_changed_account_weights_invalidate_old_saved_preview(self):
        state = WeightedAllocationPersistence(
            Path("account.sqlite"), 2, 16,
            ({"character_id": 1075, "property_weights": {"CritBase": 1.0}},),
            MagicMock(), (), None,
        )
        host = MagicMock(
            _weighted_default_property_weights={1075: {"CritBase": 1.2}},
        )
        self.assertFalse(page._persistence_weights_match_account(host, state))

    def test_empty_curtain_preference_uses_the_established_main_and_sub_stat_pools(self):
        main = list(page._MAIN_PROPERTY_CHOICES)
        substats = list(page._SUBSTAT_PROPERTY_CHOICES)

        self.assertEqual(15, len(main))
        self.assertEqual(11, len(substats))
        self.assertEqual("CritBase", dict(main)["暴击率"])
        self.assertEqual("DamageUpGeneralBase", dict(substats)["伤害增加%"])
        self.assertEqual("AtkAdd", dict(substats)["攻击力"])
        self.assertEqual("HPMaxAdd", dict(substats)["生命值"])
        self.assertEqual("MagBase", dict(substats)["环合强度"])

    def test_role_management_saves_editable_weights_to_account_database(self):
        class Host(QWidget):
            def _card(self, _title):
                card = QWidget(); QVBoxLayout(card); return card

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user.sqlite3"
            with UserDataDao(database, account_id="weights-ui"):
                pass
            host = Host()
            with patch.object(page.runtime, "USER_DATABASE_PATH", database, create=True), \
                 patch.object(page.runtime, "ASSET_DIR", Path("assets"), create=True):
                built = page.build_weighted_allocation_page(host)
                accepted = page.QDialog.Accepted
                class InspectingDialog(page.QDialog):
                    instances = []

                    def exec(self):
                        self.instances.append(self)
                        return accepted

                with patch.object(page, "QDialog", InspectingDialog), \
                     patch.object(page.NoWheelDoubleSpinBox, "value", return_value=1.25):
                    page._show_empty_curtain_preferences(host, "伊洛伊")
                self.assertIsNotNone(built)
                weights_scroll = InspectingDialog.instances[0].findChild(
                    QScrollArea, "weightedRoleWeightScroll"
                )
                self.assertIsNotNone(weights_scroll)
                self.assertEqual(210, weights_scroll.height())
            with UserDataDao(database) as dao:
                saved = dao.get_character_weight_preferences(1075)

        self.assertTrue(saved["property_weights"])
        self.assertTrue(all(value == 1.25 for value in saved["property_weights"].values()))
        self.assertEqual(
            saved["property_weights"], host._weighted_default_property_weights[1075]
        )

    def test_result_uses_legacy_puzzle_and_equipment_cards(self):
        class Host(AllocationResultsMixin, QWidget):
            def __init__(self):
                super().__init__()
                self.stats_config = {}
                self.roles_db = {}
                self.weapons_db = {}
                self._shape_areas = {"V_2": 2}
                self.scoring_engine = None
                self._weighted_role_names = {1055: "九原"}
                self._weighted_property_names = {
                    "CritBase": "暴击率%", "AtkUp": "攻击力%", "Atk": "攻击力",
                }
                self._weighted_item_names = {"Lakshana_orange": "街头拳王", "module": "横向 Type-2"}
                self._weighted_item_icons = {
                    "Lakshana_orange": Path("assets/game_ui/equipment/core/Lakshana_orange.png"),
                }
                self.weighted_result_widget = QWidget(self)
                self.weighted_result_layout = QVBoxLayout(self.weighted_result_widget)

            def _card(self, _title):
                card = QWidget(); QVBoxLayout(card); return card

        core = AllocationCandidate(1, 1, "core", "Lakshana_orange", "Suit4", None, None, "orange", 60, 60,
            False, False, False, None, False, None, None, None, (OfficialStat("CritBase", 0.2, True),),
            (OfficialStat("AtkUp", 0.17499999701976776, True),))
        module = AllocationCandidate(2, 1, "module", "module", "Suit4", "shu2", 2, "purple", 60, 60,
            False, False, False, None, False, None, None, None, (), (OfficialStat("Atk", 20, False),))
        option = RoleAllocationOption(
            1055, 1, 80.0, (),
            (AllocationAssignment(core.uid, "core", "Lakshana_orange", "Suit4", None, (), None, 40.0, (), ()),
             AllocationAssignment(module.uid, "module", "module", "Suit4", "shu2", (), None, 40.0, (), ())),
            (("H_2", "H_2"),), (),
        )
        result = AllocationSolveResult(1, 1, 1, "test", 1, (), UnifiedAllocation("role_priority", 80.0, (option,), (), ()))
        role = MagicMock(
            character_id=1055,
            effective_property_weights=(("CritBase", 1.0), ("AtkUp", 0.7)),
        )
        context = MagicMock(
            candidates=(core, module),
            shapes=(OfficialShape("shu2", 2, (), "V_2", "Type-2"),),
            roles=(role,),
        )
        host = Host()
        pixmap = QPixmap(12, 12)
        pixmap.fill(QColor("red"))

        with patch.object(page.runtime, "TEMPLATE_DIR", Path("."), create=True), \
             patch.object(page.legacy_results, "_get_shape_pixmap", return_value=pixmap) as get_shape_pixmap:
            page.render_weighted_allocation_result(host, result, context)

        self.assertEqual(1, len(host.findChildren(page.PuzzleBoardWidget)))
        cards = host.findChildren(QWidget, "equipmentCard")
        self.assertEqual(2, len(cards))
        visible_text = " ".join(label.text() for label in host.findChildren(page.QLabel))
        self.assertIn("街头拳王", visible_text)
        self.assertIn("SHU2", visible_text)
        buttons = [button.text() for button in host.findChildren(page.QPushButton)]
        self.assertIn("空幕属性汇总", buttons)
        self.assertIn("装配", buttons)
        self.assertIn("替换", buttons)
        self.assertIn("优化", buttons)
        self.assertNotIn("角色属性汇总", buttons)
        self.assertIn("空幕:", visible_text)
        get_shape_pixmap.assert_called_once_with("V_2", 60, "Purple")
        self.assertIn("攻击力", visible_text)
        self.assertIn("17.5", visible_text)
        self.assertNotIn("17.499", visible_text)
        active = next(label for label in host.findChildren(page.QLabel) if "攻击力% <b>17.5</b>" in label.text())
        self.assertIn("#d2991d", active.styleSheet())
        image_labels = [label for label in host.findChildren(page.QLabel) if label.pixmap() and not label.pixmap().isNull()]
        self.assertGreaterEqual(len(image_labels), 2)

    def test_drive_main_stat_is_not_projected_as_the_blue_card_stat(self):
        candidate = AllocationCandidate(
            2, 1, "module", "module", "Suit4", "shu2", 2, "purple", 60, 60,
            False, False, False, None, False, None, None, None,
            (OfficialStat("AtkAdd", 20, False),),
            (OfficialStat("CritBase", 0.1, True),),
        )
        assignment = AllocationAssignment(
            candidate.uid, "module", "module", "Suit4", "shu2", (), None, 40.0, (), (),
        )
        host = MagicMock(
            _weighted_property_names={"AtkAdd": "攻击力", "CritBase": "暴击率%"},
            _weighted_item_names={"module": "纵向驱动"},
            _weighted_item_icons={},
        )

        source = page._legacy_equipment_source(host, assignment, {candidate.uid: candidate}, {"shu2": "V_2"})

        self.assertEqual("", source["main_stats"])
        self.assertIsNone(source["main_value"])
        self.assertEqual({"暴击率%": 10.0}, source["sub_stats"])

    def test_official_summary_reuses_old_shape_quality_and_role_bonus_rules(self):
        core = AllocationCandidate(
            1, 1, "core", "core", None, None, None, "orange", 20, 20,
            False, False, False, None, False, None, None, None,
            (OfficialStat("CritBase", 0.3, True),),
            (OfficialStat("AtkUp", 0.1, True),),
        )
        module = AllocationCandidate(
            2, 1, "module", "module", None, "Shu2", 2, "purple", 0, 20,
            False, False, False, None, False, None, None, None,
            (
                OfficialStat("AtkAdd", 8.0, False),
                OfficialStat("HPMaxAdd", 112.0, False),
            ),
            (OfficialStat("CritBase", 0.02, True),),
        )
        option = RoleAllocationOption(
            1055, 1, 0.0, (),
            (
                AllocationAssignment(core.uid, "core", "core", None, None, (), None, 0.0, (), ()),
                AllocationAssignment(module.uid, "module", "module", None, "Shu2", (), None, 0.0, (), ()),
            ),
            (), (),
        )
        host = SimpleNamespace(
            _weighted_property_names={
                "CritBase": "暴击率", "AtkUp": "攻击力%",
                "AtkAdd": "攻击力", "HPMaxAdd": "生命值",
            },
            _weighted_property_percent={"CritBase": True, "AtkUp": True},
        )
        role = SimpleNamespace(
            extra_shape_label="Type-2",
            extra_shape_buffs=(("CritBase", 6.0),),
        )

        rows = {
            property_id: (value, percent)
            for property_id, _label, value, percent in page._official_summary_rows(
                host, option, {core.uid: core, module.uid: module}, role,
            )
        }

        self.assertEqual((38.0, True), rows["CritBase"])
        self.assertEqual((10.0, True), rows["AtkUp"])
        self.assertEqual((33.6, False), rows["AtkAdd"])
        self.assertEqual((448.0, False), rows["HPMaxAdd"])

    def test_candidate_drive_card_can_show_replace_inside_its_header(self):
        class Host(AllocationResultsMixin, QWidget):
            pass

        host = Host()
        with patch.object(page.legacy_results, "_get_shape_pixmap", return_value=QPixmap()):
            card = page.legacy_results._equip_card(
                host, "SHU2", "", {}, "V_2", "nte-module-2-1", {},
                (40.0, "A"), "Purple", replacement_callback=lambda: None,
                replacement_text="替换", card_variant="inventory",
            )

        texts = [button.text() for button in card.findChildren(page.QPushButton)]
        self.assertEqual(["替换"], texts)
        self.assertNotIn("优化", texts)

    def test_replacement_empty_curtain_cards_resolve_official_item_images(self):
        catalog = MagicMock()
        icon_path = Path("assets/game_ui/equipment/core/Lakshana_orange.png")
        catalog.inventory_item_icon.return_value = icon_path
        item = inventory_page._sqlite_inventory_item_display(
            {
                "kind": "core", "item_id": "Lakshana_orange", "suit_id": "Suit4",
                "uid_slot": 1, "uid_serial": 2, "quality": "orange",
                "main_stats": {}, "sub_stats": {},
            },
            {"Suit4": "街头拳王"},
        )

        resolved = inventory_page._replacement_item_icon(catalog, "tape", item)

        self.assertEqual("Lakshana_orange", item["_item_id"])
        self.assertEqual(icon_path, resolved)
        catalog.inventory_item_icon.assert_called_once_with("core", "Lakshana_orange")

        drive = inventory_page._sqlite_inventory_item_display(
            {
                "kind": "module", "item_id": "cell3_style1_1_Orange",
                "uid_slot": 1, "uid_serial": 3, "quality": "orange",
                "geometry": "EquipmentGeometry_Hen3", "sub_stats": {},
            },
            {},
        )
        inventory_page._replacement_item_icon(catalog, "drive", drive)
        self.assertEqual("cell3_style1_1_Orange", drive["_item_id"])
        catalog.inventory_item_icon.assert_called_with(
            "module", "cell3_style1_1_Orange"
        )

    def test_equipment_actions_reuse_configured_and_automatic_implementations(self):
        selected = (MagicMock(character_id=1055), MagicMock(character_id=1051))
        preview = page.WeightedAllocationPreview(
            result=MagicMock(unified=MagicMock(selected=selected)),
            static_dataset=MagicMock(), account_id="default", user_database_path=Path("account.sqlite"),
            context=MagicMock(),
        )
        host = MagicMock(
            _weighted_allocation_preview=preview,
            _weighted_role_names={1055: "九原", 1051: "「零」"},
        )
        host._get_sync_settings.return_value = {"equipment_apply_method": "nte_core"}

        with patch.object(page.inventory_page, "_preview_fast_assemble_all_roles") as fast_all, \
             patch.object(page.inventory_page, "_preview_automatic_assemble_all_roles") as automatic_all, \
             patch.object(page.inventory_page, "_preview_nte_core_assemble_role") as fast_role, \
             patch.object(page.inventory_page, "_preview_automatic_assemble_role") as automatic_role:
            page._perform_weighted_equipment_action(host, mode="configured")
            page._perform_weighted_equipment_action(host, mode="configured", role_name="九原")
            page._perform_weighted_equipment_action(host, mode="automatic")
            host._get_sync_settings.return_value = {"equipment_apply_method": "gamepad"}
            page._perform_weighted_equipment_action(host, mode="configured", role_name="「零」")

        fast_all.assert_called_once_with(host, role_names=["九原", "「零」"])
        fast_role.assert_called_once_with(host, "九原")
        automatic_all.assert_called_once_with(host, role_names=["九原", "「零」"])
        automatic_role.assert_called_once_with(host, "「零」")

    def test_equipment_action_saves_the_current_preview_before_dispatch(self):
        preview = page.WeightedAllocationPreview(
            result=MagicMock(unified=MagicMock(selected=(MagicMock(character_id=1055),))),
            static_dataset=MagicMock(), account_id="default", user_database_path=Path("account.sqlite"),
            context=MagicMock(),
        )
        host = MagicMock(
            _weighted_allocation_preview=preview,
            _weighted_allocation_saved_preview=None,
        )

        with patch.object(page.runtime, "USER_DATABASE_PATH", Path("account.sqlite"), create=True), \
             patch.object(page, "start_weighted_allocation_save") as save, \
             patch.object(page, "_perform_weighted_equipment_action") as perform:
            page._request_weighted_equipment(host, mode="configured", role_name="九原")
            save.call_args.kwargs["after_save"]()

        save.assert_called_once()
        perform.assert_called_once_with(host, mode="configured", role_name="九原")

    def test_replacement_reuses_sqlite_dialog_with_frozen_v5_weights(self):
        assignment = MagicMock(kind="module", uid=(2, 1))
        role = MagicMock(
            effective_property_weights=(("CritBase", 1.0), ("AtkUp", 0.7)),
            effective_main_property_weights=(("CritDamageBase", 1.0),),
        )
        preview = page.WeightedAllocationPreview(
            result=MagicMock(), static_dataset=MagicMock(), account_id="default",
            user_database_path=Path("account.sqlite"), context=MagicMock(),
        )
        host = MagicMock(
            _weighted_allocation_preview=preview,
            _weighted_allocation_saved_preview=preview,
            _weighted_property_names={
                "CritBase": "暴击率%", "AtkUp": "攻击力%",
                "CritDamageBase": "暴击伤害%",
            },
        )

        with patch.object(page.runtime, "USER_DATABASE_PATH", Path("account.sqlite"), create=True), \
             patch.object(page.inventory_page, "_optimize_saved_equipment") as optimize:
            page._request_weighted_replacement(host, "九原", assignment, role)

        optimize.assert_called_once()
        args = optimize.call_args.args
        kwargs = optimize.call_args.kwargs
        self.assertEqual((host, "九原", "drive", "nte-module-2-1"), args)
        self.assertEqual({"暴击率%": 1.0, "攻击力%": 0.7}, kwargs["weights_override"])
        self.assertEqual({"暴击伤害%": 1.0}, kwargs["main_weights_override"])
        self.assertFalse(kwargs["rank_by_damage"])
        self.assertEqual("空幕", kwargs["core_term"])
        self.assertTrue(kwargs["exclude_used_by_others"])
        self.assertTrue(callable(kwargs["replacement_persister"]))

    def test_replacement_saves_current_preview_before_opening_dialog(self):
        assignment = MagicMock(kind="core", uid=(3, 4))
        role = MagicMock(effective_property_weights=(), effective_main_property_weights=())
        preview = page.WeightedAllocationPreview(
            result=MagicMock(), static_dataset=MagicMock(), account_id="default",
            user_database_path=Path("account.sqlite"), context=MagicMock(),
        )
        host = MagicMock(
            _weighted_allocation_preview=preview,
            _weighted_allocation_saved_preview=None,
            _weighted_property_names={},
        )

        with patch.object(page.runtime, "USER_DATABASE_PATH", Path("account.sqlite"), create=True), \
             patch.object(page, "start_weighted_allocation_save") as save, \
             patch.object(page.inventory_page, "_optimize_saved_equipment") as optimize:
            page._request_weighted_replacement(host, "「零」", assignment, role)
            save.call_args.kwargs["after_save"]()

        save.assert_called_once()
        optimize.assert_called_once()
        self.assertEqual((host, "「零」", "tape", "nte-core-3-4"), optimize.call_args.args)

    def test_replacement_refreshes_the_in_memory_saved_preview(self):
        old = AllocationCandidate(
            2, 1, "module", "old", "Suit4", "shu2", 2, "purple", 60, 60,
            False, False, False, None, False, None, None, None, (), (),
        )
        new = AllocationCandidate(
            3, 1, "module", "new", "Suit4", "shu2", 2, "purple", 60, 60,
            False, False, False, None, False, None, None, None, (), (),
        )
        option = RoleAllocationOption(
            1055, 1, 80.0, (),
            (AllocationAssignment(old.uid, "module", "old", "Suit4", "shu2", (), None, 40.0, (), ()),),
            (("H_2", "H_2"),), (),
        )
        result = AllocationSolveResult(
            1, 2, 3, "test", 1, (),
            UnifiedAllocation("role_priority", 80.0, (option,), (), ()),
        )
        context = MagicMock(candidates=(old, new))
        preview = page.WeightedAllocationPreview(
            result=result, static_dataset=MagicMock(), account_id="default",
            user_database_path=Path("account.sqlite"), context=context,
        )
        host = MagicMock(_weighted_allocation_preview=preview)

        with patch.object(page, "save_weighted_allocation_preview", return_value=(7,)) as save, \
             patch.object(page, "render_weighted_allocation_result") as render, \
             patch.object(page, "_set_weighted_equipment_actions_enabled") as enable:
            page._on_weighted_replacement_done(
                host, preview, old.uid,
                {"_uid_slot": 3, "_uid_serial": 1}, 55.0, 40.0,
            )

        updated = host._weighted_allocation_preview
        self.assertIs(updated, host._weighted_allocation_saved_preview)
        self.assertEqual(new.uid, updated.result.unified.selected[0].assignments[0].uid)
        self.assertEqual(95.0, updated.result.unified.selected[0].score)
        save.assert_called_once_with(updated)
        render.assert_called_once()
        enable.assert_called_once_with(host, True)

    def test_replacement_rejects_uid_already_used_by_another_selected_role(self):
        old = AllocationCandidate(
            2, 1, "module", "old", "Suit4", "shu2", 2, "purple", 60, 60,
            False, False, False, None, False, None, None, None, (), (),
        )
        claimed = AllocationCandidate(
            3, 1, "module", "claimed", "Suit4", "shu2", 2, "purple", 60, 60,
            False, False, False, None, False, None, None, None, (), (),
        )
        first = RoleAllocationOption(
            1055, 1, 40.0, (),
            (AllocationAssignment(
                old.uid, "module", "old", "Suit4", "shu2", (), None,
                40.0, (), (),
            ),),
            (("H_2", "H_2"),), (),
        )
        second = RoleAllocationOption(
            1003, 1, 55.0, (),
            (AllocationAssignment(
                claimed.uid, "module", "claimed", "Suit4", "shu2", (), None,
                55.0, (), (),
            ),),
            (("H_2", "H_2"),), (),
        )
        preview = page.WeightedAllocationPreview(
            result=AllocationSolveResult(
                1, 2, 3, "test", 1, (),
                UnifiedAllocation(
                    "role_priority", 95.0, (first, second), (), (),
                ),
            ),
            static_dataset=MagicMock(),
            account_id="default",
            user_database_path=Path("account.sqlite"),
            context=MagicMock(candidates=(old, claimed)),
        )

        with self.assertRaisesRegex(RuntimeError, "其他角色"):
            replace_weighted_allocation_assignment(
                preview, old_uid=old.uid, new_uid=claimed.uid, new_score=60.0
            )

    def test_legacy_quality_names_match_result_card_contract(self):
        self.assertEqual("Gold", page._legacy_quality("orange"))
        self.assertEqual("Purple", page._legacy_quality("purple"))
        self.assertEqual("Blue", page._legacy_quality("blue"))

    def test_equipment_stat_display_removes_double_tail_noise(self):
        self.assertEqual("17.5", page.legacy_results._format_equipment_stat_display(17.499999701976776))
        self.assertEqual("64", page.legacy_results._format_equipment_stat_display(64.0))

    def test_missing_workshop_role_uses_bundled_static_default(self):
        with page.StaticGameDataDao() as dao:
            recommendation = dao.get_character_recommended_weights(1075)
        self.assertEqual("default", recommendation["source_kind"])
        self.assertEqual(
            {
                "DamageUpGeneralBase": 0.75,
                "CritBase": 1.0,
                "CritDamageBase": 1.0,
                "AtkUp": 0.7,
            },
            recommendation["property_weights"],
        )

    def test_unassigned_result_explains_the_explicit_core_constraint(self):
        role = MagicMock(character_id=1075, target_suit_id="Suit5", core_main_property_id="CritBase")
        context = MagicMock(roles=(role,), candidates=())
        window = MagicMock(
            _weighted_role_names={1075: "卡厄斯"},
            _weighted_suit_names={"Suit5": "街头拳王"},
            _weighted_property_names={"CritBase": "暴击率"},
        )

        reason = page._unassigned_reason(window, context, (1075,))

        self.assertEqual("卡厄斯：缺少 街头拳王＋暴击率 主词条空幕", reason)

    def test_matched_drives_explain_that_only_the_recommended_core_is_missing(self):
        role = MagicMock(target_suit_id="Suit5", core_main_property_id="CritBase")
        window = MagicMock(
            _weighted_suit_names={"Suit5": "街头拳王"},
            _weighted_property_names={"CritBase": "暴击率"},
        )

        text = page._missing_core_text(window, role)

        self.assertEqual("空幕缺失：缺少 街头拳王＋暴击率 主词条空幕（驱动图纸已匹配）", text)

    def test_pinned_runner_still_calls_context_facade(self):
        request = WeightedAllocationRequest(Path(__file__), 1, 2, 3, 5)
        user, static, context = MagicMock(), MagicMock(), MagicMock(account_id="a", static_dataset=MagicMock())
        user.__enter__.return_value = user; static.__enter__.return_value = static
        roles_path = Path("config/roles.json")
        with patch("src.features.weighted_allocation.runner.UserDataDao", return_value=user), patch("src.features.weighted_allocation.runner.StaticGameDataDao", return_value=static), patch("src.features.weighted_allocation.runner._workshop_roles_path", return_value=roles_path), patch("src.features.weighted_allocation.runner.build_allocation_context", return_value=context) as build_context, patch("src.features.weighted_allocation.runner.solve_allocation_context", return_value="result"):
            preview = run_weighted_allocation(request)
        self.assertEqual("result", preview.result)
        self.assertEqual(roles_path, build_context.call_args.kwargs["workshop_roles_path"])

    def test_persistence_reader_requires_complete_matching_active_plans(self):
        dao = MagicMock()
        dao.__enter__.return_value = dao
        dao.list_optimization_profiles.return_value = [{
            "profile_id": 2,
            "name": "__weighted_allocation_role_priority__",
            "version": {
                "version_number": 16,
                "characters": [
                    {"character_id": 1051, "ordinal": 0},
                    {"character_id": 1055, "ordinal": 1},
                ],
            },
        }]
        payload = {
            "schema": "allocation-official-snapshot-v1",
            "source": "weighted_allocation",
            "profile_id": 2,
            "profile_version": 16,
            "solver_version": "allocation-context-v1",
            "static_dataset": {
                "schema_version": 12, "dataset_id": "dataset",
                "importer_version": 12, "built_at_utc": "now",
            },
        }
        dao.list_loadout_plans.return_value = [{
            "plan_id": 13, "character_id": 1051, "source_snapshot_id": 11,
            "score": 299.12, "is_active": True, "payload": payload,
            "assignments": [{
                "uid_slot": 1, "uid_serial": 2, "kind": "module",
                "target_row": 1, "target_column": 1,
            }],
        }]

        with patch("src.features.weighted_allocation.runner.UserDataDao", return_value=dao):
            incomplete = read_weighted_allocation_persistence(Path(__file__))
        self.assertIsNone(incomplete.restore_request)

        dao.list_loadout_plans.return_value.append({
            "plan_id": 14, "character_id": 1055, "source_snapshot_id": 11,
            "score": 280.0, "is_active": True, "payload": dict(payload),
            "assignments": [{
                "uid_slot": 3, "uid_serial": 4, "kind": "module",
                "target_row": 1, "target_column": 1,
            }],
        })
        with patch("src.features.weighted_allocation.runner.UserDataDao", return_value=dao):
            complete = read_weighted_allocation_persistence(Path(__file__))

        self.assertEqual(11, complete.restore_request.snapshot_id)
        self.assertEqual([13, 14], [plan.plan_id for plan in complete.saved_plans])

    def test_manual_replacement_can_be_rebuilt_from_saved_scores_and_coordinates(self):
        old = AllocationCandidate(
            2, 1, "module", "old", "Suit4", "Hen2", 2, "purple", 60, 60,
            False, False, False, None, False, None, None, None, (), (),
        )
        new = AllocationCandidate(
            3, 1, "module", "new", "Suit4", "Hen2", 2, "purple", 60, 60,
            False, False, False, None, False, None, None, None, (), (),
        )
        option = RoleAllocationOption(
            1055, 1, 40.0, (),
            (AllocationAssignment(
                old.uid, "module", "old", "Suit4", "Hen2", ((1, 1), (1, 2)),
                None, 40.0, (), (),
            ),),
            (("H_2", "H_2"),), (),
        )
        signature = WeightedSavedPlanSignature(
            14, 1055, 55.0, frozenset({new.uid}),
            (WeightedSavedAssignmentSignature(new.uid, "module", 1, 1, 55.0),),
        )
        context = MagicMock(
            candidates=(old, new),
            shapes=(OfficialShape(
                "EquipmentGeometry_Hen2", 2,
                (OfficialShapeCell(0, 0), OfficialShapeCell(0, 1)), "H_2", "Type-2",
            ),),
        )

        from src.features.weighted_allocation.runner import _restore_saved_option
        restored = _restore_saved_option(context, option, signature)

        self.assertEqual(55.0, restored.score)
        self.assertEqual(new.uid, restored.assignments[0].uid)
        self.assertEqual(((1, 1), (1, 2)), restored.assignments[0].board_cells)

    def test_saved_weighted_plan_keeps_per_item_scores_for_manual_restore(self):
        assignment = AllocationAssignment(
            (2, 1), "module", "module", "Suit4", "Hen2", ((1, 1), (1, 2)),
            None, 40.0, (), (),
        )
        option = RoleAllocationOption(
            1055, 1, 40.0, (), (assignment,), (("H_2", "H_2"),), (),
        )
        result = AllocationSolveResult(
            11, 2, 16, "allocation-context-v1", 1, (),
            UnifiedAllocation("role_priority", 40.0, (option,), (), ()),
        )
        dataset = StaticDatasetReference(10, "dataset", 10, "now")
        preview = page.WeightedAllocationPreview(
            result=result, static_dataset=dataset, account_id="default",
            user_database_path=Path(__file__), context=MagicMock(),
        )
        user, static, bridge = MagicMock(), MagicMock(), MagicMock()
        user.__enter__.return_value = user
        static.__enter__.return_value = static
        static.list_characters.return_value = [{"character_id": 1055, "name_zh": "九原"}]
        prepared = MagicMock()
        prepared.as_record.return_value = {"payload": "prepared"}
        bridge.prepare_role_plan.return_value = prepared
        user.replace_active_loadout_plans.return_value = (7,)

        with patch("src.features.weighted_allocation.runner.UserDataDao", return_value=user), \
             patch("src.features.weighted_allocation.runner.StaticGameDataDao", return_value=static), \
             patch("src.features.weighted_allocation.runner.SavedStateLoadoutBridge", return_value=bridge):
            plan_ids = save_weighted_allocation_preview(preview)

        self.assertEqual((7,), plan_ids)
        payload = bridge.prepare_role_plan.call_args.kwargs["payload"]
        self.assertEqual({"nte-module-2-1": 40.0}, payload["assignment_scores"])
        user.replace_active_loadout_plans.assert_called_once_with(
            [{"payload": "prepared"}]
        )

    def test_runner_can_skip_hidden_role_top_k_work(self):
        request = WeightedAllocationRequest(Path(__file__), 1, 2, 3, 1, False)
        user, static, context = MagicMock(), MagicMock(), MagicMock(account_id="a", static_dataset=MagicMock())
        user.__enter__.return_value = user; static.__enter__.return_value = static
        with patch("src.features.weighted_allocation.runner.UserDataDao", return_value=user), patch("src.features.weighted_allocation.runner.StaticGameDataDao", return_value=static), patch("src.features.weighted_allocation.runner.build_allocation_context", return_value=context), patch("src.features.weighted_allocation.runner.solve_allocation_context", return_value="result") as solve:
            run_weighted_allocation(request)
        solve.assert_called_once_with(context, top_k=1, include_role_top_k=False, allow_missing_core=True)

    def test_save_worker_is_retained_until_its_result_is_handled(self):
        host = MagicMock()
        host._weighted_allocation_preview = page.WeightedAllocationPreview(
            result=MagicMock(), static_dataset=MagicMock(), account_id="default", user_database_path=Path("account.sqlite"),
            context=MagicMock(),
        )
        worker = MagicMock()
        callbacks = {}
        worker.result_ready.connect.side_effect = lambda callback: callbacks.setdefault("result", callback)
        worker.error.connect.side_effect = lambda callback: callbacks.setdefault("error", callback)

        with patch.object(page.runtime, "USER_DATABASE_PATH", Path("account.sqlite"), create=True), \
             patch.object(page, "WorkerThread", return_value=worker):
            page.start_weighted_allocation_save(host)

        self.assertIs(host._weighted_allocation_save_worker, worker)
        self.assertFalse(host.weighted_save_button.setEnabled.call_args.args[0])
        callbacks["result"](())
        self.assertIsNone(host._weighted_allocation_save_worker)
        self.assertTrue(host.weighted_save_button.setEnabled.call_args.args[0])
