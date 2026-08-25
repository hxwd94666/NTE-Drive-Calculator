# 验证战报边际页只持有可丢弃候选，不暴露持久化或角色页同步入口。
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from src.features.battle_report.marginal_page import BattleMarginalPage


class BattleMarginalCandidateUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_role_switch_replays_selected_half_with_all_candidate_profiles(self) -> None:
        created_options: list[dict] = []
        analysis_requests: list[tuple[int, object, object]] = []

        class FakeEditor(QWidget):
            def __init__(self, detail, *_args, **kwargs) -> None:
                super().__init__()
                self.detail = detail
                created_options.append(kwargs)

            def profile(self):
                return dict(self.detail["profile"])

            def selected_equipment_context(self):
                return "candidate", self.detail["equipment_contexts"]["candidate"]

        def detail(character_id: int) -> dict:
            return {
                "character": {
                    "character_id": character_id,
                    "name_zh": f"角色{character_id}",
                },
                "profile": {
                    "character_id": character_id,
                    "character_level": 80,
                    "selected_awaken_effect_ids": [],
                    "skill_levels": {"melee": 10},
                },
                "analysis_detail_scope": (
                    "first" if character_id == 1001 else "second"
                ),
                "selected_equipment_context_key": "candidate",
                "equipment_contexts": {
                    "candidate": {"items": [], "source_title": "本场原始"}
                },
            }

        with patch(
            "src.features.battle_report.marginal_page.OfficialRoleProfileEditor",
            FakeEditor,
        ):
            page = BattleMarginalPage()
            page.analysis_requested.connect(
                lambda character_id, scope, profiles: analysis_requests.append(
                    (character_id, scope, profiles)
                )
            )
            page.set_editor_data({
                "details": [detail(1001), detail(1002)],
                "marginal_equipment_editable": True,
                "inferred_character_facts": [
                    SimpleNamespace(fact_id="lacrimosa-effect5")
                ],
            })
            page.character_combo.setCurrentIndex(1)

        self.assertEqual(
            [(1001, "first"), (1002, "second")],
            [(character_id, scope) for character_id, scope, _ in analysis_requests],
        )
        self.assertEqual(2, len(analysis_requests[-1][2]))
        self.assertTrue(created_options[0]["allow_equipment_replacement"])
        self.assertFalse(created_options[0]["show_equipment_context_selector"])
        self.assertFalse(page.use_inferred_facts.isHidden())
        self.assertEqual((), page.disabled_inferred_fact_ids())
        page.use_inferred_facts.setChecked(False)
        self.assertEqual(
            ("lacrimosa-effect5",),
            page.disabled_inferred_fact_ids(),
        )

    def test_page_has_restore_but_no_persistence_or_role_sync_actions(self) -> None:
        page = BattleMarginalPage()
        button_texts = {button.text() for button in page.findChildren(QPushButton)}

        self.assertIn("恢复已保存状态", button_texts)
        self.assertNotIn("从角色页同步", button_texts)
        self.assertNotIn("同步养成到角色页", button_texts)
        self.assertNotIn("保存修改副本", button_texts)


if __name__ == "__main__":
    unittest.main()
