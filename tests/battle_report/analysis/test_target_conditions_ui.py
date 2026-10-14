# 验证战报环境目标的正式选择、还原与百分比保存单位。
import unittest
from PySide6.QtWidgets import QApplication
from src.domain.battle_report.targets.profiles import BattleTargetCondition
from src.features.battle_report.analysis.target_conditions import BattleTargetConditionSelector
from src.features.battle_report.analysis.target_vitals import BattleTargetVitalPanel
from src.services.battle_report.targets.catalog import BattleTargetCatalogService
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


class BattleTargetConditionUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_feast_selector_applies_difficulty_options_and_witch_buff(self) -> None:
        with StaticGameDataDao("data/game_static.sqlite3") as dao:
            catalog = BattleTargetCatalogService.load(dao)
        selector = BattleTargetConditionSelector()
        selector.set_catalog(catalog)
        selector.environment_combo.setCurrentIndex(
            selector.environment_combo.findData("feast")
        )
        selector.feast_stage_combo.setCurrentIndex(
            selector.feast_stage_combo.count() - 1
        )
        selector.feast_difficulty_combo.setCurrentIndex(3)
        for combo in selector._feast_option_combos.values():
            combo.setCurrentIndex(combo.count() - 1)
        selector.witch_combo.setCurrentIndex(4)

        preset = selector.current_preset()

        self.assertEqual("DiyBossStage8", preset["environment_ref"])
        self.assertEqual(4, preset["difficulty_id"])
        self.assertEqual(1050.0, preset["enemy_defense_base"])
        self.assertEqual(0.5, preset["resistances"]["psyche"])
        self.assertEqual("DamageUpGeneralBase", preset["witch_buff_property_id"])

        restored = BattleTargetCondition(
            target_name=preset["target_name"],
            enemy_level=preset["enemy_level"],
            scene=preset["scene"],
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=tuple(sorted(preset["resistances"].items())),
            enemy_defense_base=preset["enemy_defense_base"],
            enemy_defense_up=preset["enemy_defense_up"],
            enemy_defense_add=preset["enemy_defense_add"],
            enemy_topple_limit=preset["enemy_topple_limit"],
            environment_kind=preset["environment_kind"],
            environment_ref=preset["environment_ref"],
            selected_target_ids=tuple(preset["selected_target_ids"]),
            primary_target_id=preset["primary_target_id"],
            difficulty_id=preset["difficulty_id"],
            feast_options=tuple(sorted(preset["feast_options"].items())),
            witch_buff_id=preset["witch_buff_id"],
            witch_buff_name_zh=preset["witch_buff_name_zh"],
            witch_buff_property_id=preset["witch_buff_property_id"],
            witch_buff_value=preset["witch_buff_value"],
            witch_buff_is_percent=preset["witch_buff_is_percent"],
        )
        restored_selector = BattleTargetConditionSelector()
        restored_selector.set_catalog(catalog)
        restored_selector.render(restored)
        restored_preset = restored_selector.current_preset()

        self.assertEqual(preset["feast_options"], restored_preset["feast_options"])
        self.assertEqual(0.5, restored_preset["resistances"]["psyche"])

        panel = BattleTargetVitalPanel()
        panel.set_catalog(catalog)
        panel.condition_selector.environment_combo.setCurrentIndex(
            panel.condition_selector.environment_combo.findData("feast")
        )
        panel.condition_selector.feast_stage_combo.setCurrentIndex(
            panel.condition_selector.feast_stage_combo.count() - 1
        )
        panel.condition_selector.feast_difficulty_combo.setCurrentIndex(3)
        panel.defense_reduction_spin.setValue(12.5)
        emitted = []
        panel.condition_save_requested.connect(emitted.append)
        panel.save_condition_button.click()

        self.assertEqual("feast", emitted[0]["environment_kind"])
        self.assertEqual(4, emitted[0]["difficulty_id"])
        self.assertEqual(0.125, emitted[0]["defense_reduction"])
        self.assertEqual(
            ["boss_05_BP_DiyBoss"],
            list(emitted[0]["selected_target_ids"]),
        )

    def test_clone_selector_uses_official_category_activity_and_spawn_members(self) -> None:
        with StaticGameDataDao("data/game_static.sqlite3") as dao:
            catalog = BattleTargetCatalogService.load(dao)
        selector = BattleTargetConditionSelector()
        selector.set_catalog(catalog)
        selector.environment_combo.setCurrentIndex(
            selector.environment_combo.findData("clone")
        )
        selector.clone_category_combo.setCurrentIndex(
            next(
                index
                for index in range(selector.clone_category_combo.count())
                if selector.clone_category_combo.itemText(index) == "异能升级材料"
            )
        )
        selector.clone_activity_combo.setCurrentIndex(
            next(
                index
                for index in range(selector.clone_activity_combo.count())
                if selector.clone_activity_combo.itemText(index) == "小心鸽子"
            )
        )

        preset = selector.current_preset()

        self.assertEqual("open_world", preset["environment_kind"])
        self.assertTrue(preset["environment_ref"].startswith("clone|"))
        self.assertIsNone(preset["difficulty_id"])
        self.assertTrue(preset["selected_target_ids"])
        self.assertTrue(preset["target_name"])

    def test_target_condition_editor_emits_final_percent_values(self) -> None:
        panel = BattleTargetVitalPanel()
        emitted = []
        panel.condition_save_requested.connect(emitted.append)
        panel.target_name_edit.setText("墨菲斯托")
        panel.enemy_level_spin.setValue(90)
        panel.enemy_defense_base_spin.setValue(1050)
        panel.resistance_spins["cosmos"].setValue(50)
        panel.resistance_spins["psyche"].setValue(50)
        panel.resistance_spins["lakshana"].setValue(50)

        panel.save_condition_button.click()

        self.assertEqual(1, len(emitted))
        self.assertEqual("墨菲斯托", emitted[0]["target_name"])
        self.assertEqual(1050.0, emitted[0]["enemy_defense_base"])
        self.assertEqual(0.5, emitted[0]["resistances"]["cosmos"])
        self.assertEqual(0.5, emitted[0]["resistances"]["psyche"])
        self.assertEqual(0.5, emitted[0]["resistances"]["lakshana"])
