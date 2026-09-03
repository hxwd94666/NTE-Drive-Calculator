# 验证公共养成计算器的真实静态数据、响应式卡片和身份回投。
from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QScrollArea,
    QTableView,
    QTableWidget,
)

from src.features.static_catalog.progression_calculator import (
    build_progression_calculator_dialog,
)
from src.services.progression_stamina_service import ProgressionStaminaService
from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "game_static.sqlite3"


class StaticCatalogProgressionCalculatorUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.dao = StaticGameDataDao(DATABASE)
        self.terminology = StaticCatalogTerminologyService(self.dao)
        self.outcomes = []
        self.dialog = build_progression_calculator_dialog(
            service=ProgressionStaminaService(official_stage_source=self.dao),
            terminology_service=self.terminology,
        )

    def tearDown(self) -> None:
        self.dialog.dispose()
        self.dao.close()

    def test_real_material_request_renders_cards_and_exact_stamina_at_narrow_width(
        self,
    ) -> None:
        self.dialog.open_request(
            {
                "kind": "skill",
                "character_id": 1036,
                "skill_id": "skill-audit",
                "requirements": ({
                    "item_id": "EquipmentUpMaterial_lv3",
                    "required_quantity": 10,
                    "known_quantity": 10,
                },),
                "requirement_status": "complete",
                "requirement_gaps": (),
            },
            on_result=self.outcomes.append,
        )
        self.dialog.hunter_level.setValue(55)
        for card in self.dialog._material_cards.values():
            card.owned.setValue(2)
        self.dialog.resize(500, 720)
        self.dialog.show()
        self.app.processEvents()
        self.dialog.calculate_button.click()
        deadline = time.monotonic() + 3.0
        while not self.dialog.calculate_button.isEnabled() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertTrue(self.dialog.calculate_button.isEnabled())

        visible_text = "\n".join(
            label.text()
            for label in self.dialog.findChildren(QLabel)
            if label.isVisibleTo(self.dialog)
        )
        self.assertIn("超屑井主", visible_text)
        self.assertEqual("鉴别 7（原生）", self.dialog.identification_level.currentText())
        self.assertIn("最低活力  160", visible_text)
        self.assertNotIn("EquipmentUpMaterial_lv3", visible_text)
        self.assertFalse(self.dialog.findChildren(QTableWidget))
        self.assertFalse(self.dialog.findChildren(QTableView))
        self.assertTrue(all(
            scroll.horizontalScrollBar().maximum() == 0
            for scroll in self.dialog.findChildren(QScrollArea)
        ))
        self.assertEqual(1, len(self.outcomes))
        self.assertEqual("1036", self.outcomes[0].owner_id)
        self.assertEqual("skill-audit", self.outcomes[0].skill_id)


if __name__ == "__main__":
    unittest.main()
