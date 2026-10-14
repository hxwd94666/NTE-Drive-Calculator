# 验证逐击分块详情读取既有实测和计算结果，不触发页面公式重算。
import json
import unittest
from dataclasses import replace
from unittest.mock import patch
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QTableWidget

from src.domain.battle_report.timeline.events import BattleNativeHitEvidence
from src.domain.battle_report.replay.models import BattleHitReplayTerm, BattleHitReplayFactor
from src.features.battle_report.hits.inspection_widget import HitInspectionWidget
from tests.battle_report.buffs.test_hit_explanation import _hit


class HitInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.view = HitInspectionWidget()

    def tearDown(self):
        if self.view._popup is not None:
            self.view._popup.close()
        self.view.close()

    def test_measured_attributes_and_applied_buffs_are_separate_from_presence(self):
        raw = {"attackerObjectIndex":1,"attackerAttributes":{"id":1,"actorIndex":1,"actorSerial":2,
            "stage":"hit_callback","values":{"attack":{"value":123.125,"status":"ok","source":"game"}}},
            "attackerEffects":{"effects":[{"name":"不能展示的存在效果"}]},
            "executionEvidence":{"identity":{"sourceAsc":[3,4]},"applicationEvidence":{
                "coverage":"custom_application_modifier_native_path","status":"ok","allBuffsComplete":False,
                "items":[{"source":"native_application","recipientAsc":{"objectIndex":3,"objectSerial":4},
                    "buff":{"className":"Buff_Applied_C"},"property":"AtkAdd","value":37,"inclusion":"unknown"}]}}}
        hit=replace(_hit(),native_evidence=BattleNativeHitEvidence(json.dumps({"rawHit":raw})))
        view = self.view
        with patch('src.services.battle_report.buffs.attribute_projection.BattleBuffAttributeProjectionService.project_hit',
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
            # 切到缺证据的旧报告后，不沿用上一击的实测属性，也不后台补算。
            view.set_hit(_hit())
            view.participant("attacker")
            self.assertEqual(view._popup.findChild(QTableWidget).rowCount(),0)

    def test_participants_follow_term_owner_across_mixed_factors(self):
        def term(key, prop, group):
            return BattleHitReplayTerm(key, prop, prop, 80.0, group, "", False, "来源")
        factors = (
            BattleHitReplayFactor("defense", "防御", 0.5, "", terms=(
                term("character:level", "CharacterLevel", "character"),
                term("attacker:DefIgnore", "DefIgnore", "resolved"),
                term("target:native:defense", "DefBase", "native_panel"))),
            BattleHitReplayFactor("resistance", "抗性", 1.0, "", terms=(
                term("buff:DamagePenetrateChaos:suite-power", "DamagePenetrateChaos", "buff"),
                term("target:native:resistance", "Resistance:chaos", "native_panel"))),
            BattleHitReplayFactor("vulnerability", "易伤", 1.0, "", terms=(
                term("target:vulnerability", "Vulnerability", "target"),)),
        )
        replay = SimpleNamespace(selected_damage=1.0, formula_type="直伤", critical_state="non_critical",
                                 factors=factors, formula_panel_character_id=None)
        view = self.view
        decision = SimpleNamespace(interval_id="suite-power", status="applied", observed_stacks=1)
        view.set_hit(_hit(), replay, SimpleNamespace(decisions=(decision,)))
        view.participant("victim")
        table = view._popup.findChild(QTableWidget)
        self.assertEqual([table.item(row, 0).text() for row in range(table.rowCount())],
                         ["公式输入 · DefBase", "公式输入 · Resistance:chaos", "公式输入 · Vulnerability"])
        view.participant("attacker")
        table = view._popup.findChild(QTableWidget)
        self.assertEqual([table.item(row, 0).text() for row in range(table.rowCount())],
                         ["公式输入 · CharacterLevel", "公式输入 · DefIgnore", "公式输入 · DamagePenetrateChaos"])
        self.assertEqual(table.item(0, 2).text(), "冻结角色配置")
        self.assertEqual(table.item(2, 2).text(), "状态有证据／贡献模型")

    def test_formula_storage_requires_read_path_and_matching_execution(self):
        term = {"returned":True,"identityStable":True,"attackReadPathObserved":True,
                "component":{"objectIndex":11,"objectSerial":12},
                "usedAttack":{"status":"ok","value":2127.125},"result":{"status":"ok","value":100}}
        raw = {"executionId":"19","executionEvidence":{"executionId":"19",
               "applicationEvidence":{"baseTerms":[term]}}}
        view = self.view
        # 读取路径与同一次执行的绑定缺一不可；其它实例的值不能冒充本击实测。
        for observed, execution_id in ((True, "19"), (False, "19"), (True, "20")):
            with self.subTest(observed=observed, execution_id=execution_id):
                term["attackReadPathObserved"] = observed
                raw["executionEvidence"]["executionId"] = execution_id
                hit = replace(_hit(),native_evidence=BattleNativeHitEvidence(json.dumps({"rawHit":raw})))
                view.set_hit(hit)
                view.formula_observations()
                table = view._popup.findChild(QTableWidget)
                known = observed and execution_id == raw["executionId"]
                self.assertEqual(table.item(0,2).text(),"2127.125" if known else "未取得")
                self.assertEqual(table.item(0,3).text(),"客户端实测" if known else "缺少读取证据")

    def test_model_breakdown_is_not_presented_as_measured_buff_application(self):
        view = self.view
        term = BattleHitReplayTerm("native:critDamage", "CritDamageBase", "暴伤", 1.74,
                                   "native_panel", "逐击属性", True, "游戏返回")
        factor = BattleHitReplayFactor("critical", "暴击", 2.74, "", terms=(term,))
        replay = SimpleNamespace(selected_damage=1.0, formula_type="直伤", critical_state="critical",
                                 factors=(factor,), formula_panel_character_id=None)
        def modifier(property_id, interval_ids):
            return SimpleNamespace(interval_ids=interval_ids, buff_names=("模型 Buff",),
                                   target_scope="self", property_id=property_id, additive_value=0.6, confidence="低")
        observed = SimpleNamespace(interval_id="model:1", status="applied", observed_stacks=1, reasons=())
        inferred = SimpleNamespace(interval_id="model:2", status="applied", observed_stacks=None, reasons=())
        native_applied = SimpleNamespace(interval_id="native-applied:1", status="applied", observed_stacks=1, reasons=())
        cases = (
            ((observed,), ("model:1",), "状态有证据／贡献模型"),
            ((observed, inferred), ("model:1", "model:2"), "部分状态有证据／贡献模型"),
            ((inferred,), ("model:2",), "模型推断贡献"),
            ((native_applied,), ("native-applied:1",), "采用实测写入值"),
        )
        for decisions, interval_ids, source in cases:
            with self.subTest(source=source):
                projection = SimpleNamespace(
                    modifiers=(modifier("CritDamageBase", interval_ids), modifier("HPMaxUp", interval_ids)),
                    decisions=decisions,
                )
                view.set_hit(_hit(), replay, projection)
                view.contributions()
                table = view._popup.findChild(QTableWidget)
                self.assertEqual(table.item(0, 4).text(), source)
                self.assertIn("实测面板已覆盖", table.item(0, 5).text())
                self.assertIn("当前公式未单独使用", table.item(1, 5).text())
