# 验证战报边际页只持有可丢弃候选，不暴露持久化或角色页同步入口。
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from src.domain.battle_counterfactual_quantification import (
    BattleDamageQuantification,
    BattleQuantificationGap,
)
from src.domain.battle_report import BattleCharacterBaseline, BattleCharacterStat
from src.features.battle_report.marginal_page import BattleMarginalPage
from src.features.battle_report.marginal_result_table_view import (
    render_attribute_results,
    render_buff_benefit_results,
)


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
                    "battle_stat_overrides": {"CritBase": 0.9},
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
        self.assertTrue(
            all(
                "battle_stat_overrides" not in profile
                for profile in analysis_requests[-1][2]
            )
        )
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
        self.assertNotIn("清除手工属性", button_texts)

    def test_page_renders_read_only_unit_benefits(self) -> None:
        page = BattleMarginalPage()
        baseline = BattleCharacterBaseline(
            character_id=1001,
            character_name="测试角色",
            source="frozen-test",
            stats=(BattleCharacterStat("CritBase", "暴击率", 0.5, True),),
        )
        result = SimpleNamespace(
            property_id="CritBase",
            label="暴击率",
            unit=0.01,
            is_percent=True,
            baseline_damage=1000.0,
            known_projection_damage=1010.0,
            quantified_role_gain_percent=1.0,
            quantified_team_gain_percent=0.4,
            full_role_gain_percent=1.0,
            full_team_gain_percent=0.4,
            damage_share_percent=40.0,
            quantification=BattleDamageQuantification.from_buckets(
                status="complete",
                fully_quantified_damage=800.0,
                proven_unchanged_damage=200.0,
                quantified_increment=10.0,
            ),
            assumption="按逐击 character 暴击策略重放。",
        )
        page._analysis = SimpleNamespace(
            baselines=(baseline,), roles=(), build_counterfactual=None,
            buff_counterfactuals=(),
        )
        page.character_combo.blockSignals(True)
        page.character_combo.addItem("测试角色", 1001)
        page.character_combo.blockSignals(False)
        with patch(
            "src.features.battle_report.marginal_page."
            "BattleMarginalCalculationService.default_units",
            return_value={"CritBase": 0.01},
        ), patch(
            "src.features.battle_report.marginal_page."
            "BattleMarginalCalculationService.calculate",
            return_value=(result,),
        ):
            page._render_selected_role()

        self.assertIsNone(page.attribute_table.cellWidget(0, 1))
        self.assertEqual("暴击率 +1.00%", page.attribute_table.item(0, 0).text())
        self.assertEqual("完整", page.attribute_table.item(0, 1).text())
        self.assertEqual("+1.00%", page.attribute_table.item(0, 2).text())
        self.assertEqual("+0.40%", page.attribute_table.item(0, 3).text())
        self.assertEqual("80.0%", page.attribute_table.item(0, 4).text())
        self.assertEqual("40.0%", page.attribute_table.item(0, 5).text())
        self.assertEqual("+10", page.attribute_table.item(0, 6).text())

    def test_page_renders_team_buff_gain_by_beneficiary(self) -> None:
        page = BattleMarginalPage()
        page.character_combo.addItem("角色1", 1)
        quantification = BattleDamageQuantification.from_buckets(
            status="complete",
            fully_quantified_damage=3_550.0,
            quantified_increment=550.0,
        )
        buff = SimpleNamespace(
            source_character_id=1,
            source_character_name="角色1",
            buff_name="团队增伤",
            target_scope="team",
            affected_hits=2,
            without_quantified_effect_damage=3000.0,
            quantified_damage_gain=550.0,
            quantified_gain_percent=18.333,
            damage_gain=550.0,
            gain_percent=18.333,
            without_buff_damage=3000.0,
            quantification=quantification,
            confidence="高",
            explanation="完整移除反事实。",
            beneficiaries=(
                SimpleNamespace(
                    character_id=1,
                    character_name="角色1",
                    damage_gain=150.0,
                    recipient_gain_percent=15.0,
                    team_contribution_percent=5.0,
                    quantified_damage_gain=150.0,
                    quantified_recipient_gain_percent=15.0,
                    quantified_team_contribution_percent=5.0,
                    quantification=quantification,
                ),
                SimpleNamespace(
                    character_id=2,
                    character_name="角色2",
                    damage_gain=400.0,
                    recipient_gain_percent=20.0,
                    team_contribution_percent=13.333,
                    quantified_damage_gain=400.0,
                    quantified_recipient_gain_percent=20.0,
                    quantified_team_contribution_percent=13.333,
                    quantification=quantification,
                ),
            ),
            quantified_unattributed_damage_gain=0.0,
            unattributed_damage_gain=0.0,
        )
        other_source = SimpleNamespace(
            source_character_id=2,
            source_character_name="角色2",
            buff_name="其他角色 Buff",
            target_scope="team",
            affected_hits=2,
            without_quantified_effect_damage=3000.0,
            quantified_damage_gain=100.0,
            quantified_gain_percent=3.0,
            damage_gain=100.0,
            gain_percent=3.0,
            without_buff_damage=3000.0,
            quantification=quantification,
            confidence="高",
            explanation="完整移除反事实。",
            beneficiaries=buff.beneficiaries,
            quantified_unattributed_damage_gain=0.0,
            unattributed_damage_gain=0.0,
        )

        page._render_buff_benefits((buff, other_source))

        self.assertEqual(2, page.buff_benefit_table.rowCount())
        self.assertEqual("角色1", page.buff_benefit_table.item(0, 0).text())
        self.assertEqual("团队增伤", page.buff_benefit_table.item(0, 1).text())
        self.assertEqual("角色2", page.buff_benefit_table.item(1, 2).text())
        self.assertEqual("+400", page.buff_benefit_table.item(1, 3).text())
        self.assertEqual("+20.00%", page.buff_benefit_table.item(1, 4).text())
        self.assertEqual("+13.33%", page.buff_benefit_table.item(1, 5).text())
        self.assertEqual(
            "+550（+18.33%）",
            page.buff_benefit_table.item(1, 6).text(),
        )
        self.assertEqual("100.0%", page.buff_benefit_table.item(1, 7).text())

    def test_partial_team_uses_quantified_contribution_for_complete_beneficiary(
        self,
    ) -> None:
        page = BattleMarginalPage()
        gap = BattleQuantificationGap(
            code="target_profile_missing",
            dimension_id="target_resistance",
            dependency_scope="target_sensitive",
            property_ids=("DamageResistChaosBase",),
            explanation="缺少冻结目标抗性画像。",
        )
        team_quantification = BattleDamageQuantification.from_buckets(
            status="partial",
            fully_quantified_damage=1_150.0,
            unavailable_damage=1_000.0,
            quantified_increment=150.0,
            gaps=(gap,),
        )
        beneficiary_quantification = BattleDamageQuantification.from_buckets(
            status="complete",
            fully_quantified_damage=1_150.0,
            quantified_increment=150.0,
        )
        result = SimpleNamespace(
            source_character_id=1,
            source_character_name="角色1",
            buff_name="混合团队 Buff",
            target_scope="team",
            affected_hits=2,
            without_quantified_effect_damage=2_000.0,
            quantified_damage_gain=150.0,
            quantified_gain_percent=7.5,
            damage_gain=None,
            gain_percent=None,
            without_buff_damage=None,
            quantification=team_quantification,
            confidence="低",
            explanation="仅量化角色侧变化。",
            beneficiaries=(SimpleNamespace(
                character_id=1,
                character_name="角色1",
                damage_gain=150.0,
                recipient_gain_percent=15.0,
                team_contribution_percent=None,
                quantified_damage_gain=150.0,
                quantified_recipient_gain_percent=15.0,
                quantified_team_contribution_percent=7.5,
                quantification=beneficiary_quantification,
            ),),
            quantified_unattributed_damage_gain=0.0,
            unattributed_damage_gain=None,
        )

        render_buff_benefit_results(
            page.buff_benefit_table,
            (result,),
            source_character_id=1,
        )

        self.assertEqual(
            "已量化 +7.50%",
            page.buff_benefit_table.item(0, 5).text(),
        )

    def test_unavailable_known_beneficiary_is_not_labeled_unattributed(self) -> None:
        page = BattleMarginalPage()
        gap = BattleQuantificationGap(
            code="formula_family_unsupported",
            dimension_id="buff_projection",
            dependency_scope="mechanic_specific",
            property_ids=(),
            explanation="联动变化暂不可量化。",
        )
        quantification = BattleDamageQuantification.from_buckets(
            status="unavailable",
            unavailable_damage=990.0,
            quantified_increment=None,
            gaps=(gap,),
        )
        result = SimpleNamespace(
            source_character_id=1,
            source_character_name="角色1",
            buff_name="联动 Buff",
            target_scope="team",
            affected_hits=1,
            without_quantified_effect_damage=None,
            quantified_damage_gain=None,
            quantified_gain_percent=None,
            damage_gain=None,
            gain_percent=None,
            without_buff_damage=None,
            quantification=quantification,
            confidence="低",
            explanation="收益未量化，但来源角色已知。",
            beneficiaries=(SimpleNamespace(
                character_id=7,
                character_name="角色7",
                damage_gain=None,
                recipient_gain_percent=None,
                team_contribution_percent=None,
                quantified_damage_gain=None,
                quantified_recipient_gain_percent=None,
                quantified_team_contribution_percent=None,
                quantification=quantification,
            ),),
            quantified_unattributed_damage_gain=None,
            unattributed_damage_gain=None,
        )

        render_buff_benefit_results(
            page.buff_benefit_table,
            (result,),
            source_character_id=1,
        )

        self.assertEqual(1, page.buff_benefit_table.rowCount())
        self.assertEqual("角色7", page.buff_benefit_table.item(0, 2).text())
        self.assertNotEqual(
            "无法归因",
            page.buff_benefit_table.item(0, 2).text(),
        )

    def test_page_renders_unavailable_attribute_as_dash_not_zero(self) -> None:
        page = BattleMarginalPage()
        gap = BattleQuantificationGap(
            code="target_profile_missing",
            dimension_id="target_defense",
            dependency_scope="target_sensitive",
            property_ids=("DefIgnore",),
            explanation="缺少冻结目标防御画像。",
        )
        result = SimpleNamespace(
            property_id="DefIgnore",
            label="防御忽略",
            unit=0.01,
            is_percent=True,
            baseline_damage=1000.0,
            known_projection_damage=None,
            quantified_role_gain_percent=None,
            quantified_team_gain_percent=None,
            full_role_gain_percent=None,
            full_team_gain_percent=None,
            damage_share_percent=40.0,
            quantification=BattleDamageQuantification.from_buckets(
                status="unavailable",
                unavailable_damage=1000.0,
                quantified_increment=None,
                gaps=(gap,),
            ),
            assumption="缺少冻结目标防御画像，未知不记为零。",
        )

        render_attribute_results(page.attribute_table, (result,))

        self.assertEqual("未量化", page.attribute_table.item(0, 1).text())
        self.assertEqual("—", page.attribute_table.item(0, 2).text())
        self.assertEqual("—", page.attribute_table.item(0, 3).text())
        self.assertNotIn("0.00%", page.attribute_table.item(0, 2).text())


if __name__ == "__main__":
    unittest.main()
