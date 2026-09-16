# 验证逐击分块详情读取既有实测和计算结果，不触发页面公式重算。
import json
import unittest
from dataclasses import replace
from unittest.mock import patch
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QTableWidget

from src.domain.battle_native_evidence import BattleNativeHitEvidence
from src.domain.battle_report import BattleHitReplayTerm, BattleHitReplayFactor
from src.features.battle_report.hit_inspection_widget import HitInspectionWidget, contribution_source, term_source
from tests.test_battle_hit_buff_detail import _hit


class ContributionSourceTests(unittest.TestCase):
    def test_observed_state_is_distinct_from_measured_application(self):
        evidence = SimpleNamespace(status="applied", observed_stacks=1)
        inferred = SimpleNamespace(status="applied", observed_stacks=None)
        self.assertEqual(contribution_source(False, [evidence]), "状态有证据／贡献模型")
        self.assertEqual(contribution_source(False, [evidence, inferred]), "部分状态有证据／贡献模型")
        self.assertEqual(contribution_source(False, [inferred]), "模型推断贡献")
        self.assertEqual(contribution_source(True, [evidence]), "采用实测写入值")

    def test_participant_formula_uses_the_same_buff_evidence_label(self):
        decision = SimpleNamespace(interval_id="suite-power", status="applied", observed_stacks=1)
        term = SimpleNamespace(source_group="buff", term_id="buff:DamagePenetrateChaos:suite-power")
        self.assertEqual(term_source(term, SimpleNamespace(decisions=[decision])), "状态有证据／贡献模型")


class HitInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_measured_attributes_and_applied_buffs_are_separate_from_presence(self):
        raw = {"attackerObjectIndex":1,"attackerAttributes":{"id":1,"actorIndex":1,"actorSerial":2,
            "stage":"hit_callback","values":{"attack":{"value":123.125,"status":"ok","source":"game"}}},
            "attackerEffects":{"effects":[{"name":"不能展示的存在效果"}]},
            "executionEvidence":{"identity":{"sourceAsc":[3,4]},"applicationEvidence":{
                "coverage":"custom_application_modifier_native_path","status":"ok","allBuffsComplete":False,
                "items":[{"source":"native_application","recipientAsc":{"objectIndex":3,"objectSerial":4},
                    "buff":{"className":"Buff_Applied_C"},"property":"AtkAdd","value":37,"inclusion":"unknown"}]}}}
        hit=replace(_hit(),native_evidence=BattleNativeHitEvidence(json.dumps({"rawHit":raw})))
        view=HitInspectionWidget()
        try:
            with patch('src.services.battle_buff_attribute_projection_service.BattleBuffAttributeProjectionService.project_hit',
                       side_effect=AssertionError('UI must not compute')):
                view.set_hit(hit)
                view.participant("attacker")
                table=view._popup.findChild(QTableWidget)
                self.assertEqual(table.item(0,1).text(),"123.125")
                self.assertEqual(table.item(0,2).text(),"实测")
                view.contributions()
                table=view._popup.findChild(QTableWidget)
                self.assertEqual(table.rowCount(),1)
                self.assertEqual(table.item(0,0).text(),"原生属性写入 · 额外攻击力")
                self.assertIn("原始效果标识：Buff_Applied_C", table.item(0,6).text())
                self.assertEqual(table.item(0,4).text(),"实测写入")
                self.assertIn("包含关系待定",table.item(0,6).text())
                self.assertIn("公式读取尚未观测",table.item(0,6).text())
                view.participant("formula")
                self.assertEqual(view._popup.findChild(QTableWidget).rowCount(),0)
        finally:
            if view._popup is not None: view._popup.close()
            view.close()

    def test_old_report_does_not_fabricate_measured_attributes(self):
        view=HitInspectionWidget()
        try:
            view.set_hit(_hit())
            view.participant("attacker")
            self.assertEqual(view._popup.findChild(QTableWidget).rowCount(),0)
        finally:
            if view._popup is not None: view._popup.close()
            view.close()

    def test_participants_follow_term_owner_across_mixed_factors(self):
        def term(key, prop, group):
            return BattleHitReplayTerm(key, prop, prop, 80.0, group, "", False, "来源")
        factors = (
            BattleHitReplayFactor("defense", "防御", 0.5, "", terms=(
                term("character:level", "CharacterLevel", "character"),
                term("attacker:DefIgnore", "DefIgnore", "resolved"),
                term("target:native:defense", "DefBase", "native_panel"))),
            BattleHitReplayFactor("resistance", "抗性", 1.0, "", terms=(
                term("buff:DamagePenetrateChaos:a", "DamagePenetrateChaos", "buff"),
                term("target:native:resistance", "Resistance:chaos", "native_panel"))),
            BattleHitReplayFactor("vulnerability", "易伤", 1.0, "", terms=(
                term("target:vulnerability", "Vulnerability", "target"),)),
        )
        replay = SimpleNamespace(selected_damage=1.0, formula_type="直伤", critical_state="non_critical",
                                 factors=factors, formula_panel_character_id=None)
        view = HitInspectionWidget()
        try:
            view.set_hit(_hit(), replay)
            with patch.object(view, "table") as show:
                view.participant("victim")
                names = [r[0] for r in show.call_args.args[2]]
                self.assertEqual(names, ["公式输入 · DefBase", "公式输入 · Resistance:chaos", "公式输入 · Vulnerability"])
                view.participant("attacker")
                rows = show.call_args.args[2]
                self.assertEqual([r[0] for r in rows], ["公式输入 · CharacterLevel", "公式输入 · DefIgnore", "公式输入 · DamagePenetrateChaos"])
                self.assertEqual(rows[0][2], "冻结角色配置")
        finally:
            view.close()

    def test_formula_storage_requires_read_path_and_matching_execution(self):
        term = {"returned":True,"identityStable":True,"attackReadPathObserved":True,
                "component":{"objectIndex":11,"objectSerial":12},
                "usedAttack":{"status":"ok","value":2127.125},"result":{"status":"ok","value":100}}
        raw = {"executionId":"19","executionEvidence":{"executionId":"19",
               "applicationEvidence":{"baseTerms":[term]}}}
        view = HitInspectionWidget()
        try:
            for observed in (True,False):
                term["attackReadPathObserved"] = observed
                hit = replace(_hit(),native_evidence=BattleNativeHitEvidence(json.dumps({"rawHit":raw})))
                view.set_hit(hit)
                view.formula_observations()
                table = view._popup.findChild(QTableWidget)
                self.assertEqual(table.item(0,2).text(),"2127.125" if observed else "未取得")
                self.assertEqual(table.item(0,3).text(),"客户端实测" if observed else "缺少读取证据")
        finally:
            if view._popup is not None: view._popup.close()
            view.close()

    def test_model_breakdown_is_not_presented_as_measured_buff_application(self):
        view = HitInspectionWidget()
        term = BattleHitReplayTerm("native:critDamage", "CritDamageBase", "暴伤", 1.74,
                                   "native_panel", "逐击属性", True, "游戏返回")
        factor = BattleHitReplayFactor("critical", "暴击", 2.74, "", terms=(term,))
        replay = SimpleNamespace(selected_damage=1.0, formula_type="直伤", critical_state="critical",
                                 factors=(factor,), formula_panel_character_id=None)
        def modifier(property_id):
            return SimpleNamespace(interval_ids=("model:1",), buff_names=("模型 Buff",),
                                   target_scope="self", property_id=property_id, additive_value=0.6, confidence="低")
        projection = SimpleNamespace(modifiers=(modifier("CritDamageBase"), modifier("HPMaxUp")), decisions=())
        try:
            view.set_hit(_hit(), replay, projection)
            with patch.object(view, "table") as show:
                view.contributions()
                rows = show.call_args.args[2]
                self.assertEqual(rows[0][4], "模型推断贡献")
                self.assertIn("实测面板已覆盖", rows[0][5])
                self.assertIn("当前公式未单独使用", rows[1][5])
        finally:
            view.close()
