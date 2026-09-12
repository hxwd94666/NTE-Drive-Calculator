# 验证原生效果证据经过页面解码后独立展示，不混入规则推断。
from dataclasses import asdict, replace
import json
import unittest
from types import SimpleNamespace

from src.domain.battle_native_evidence import BattleHitFieldEvidence
from src.domain.battle_report import BattleAnalysisHit, BattleHitReplayResult
from src.integrations.native_battle_page_wire import decode
from src.services.battle_native_evidence_rendering import render_native_evidence
from tests.test_battle_hit_buff_detail import _hit
from tests.native_execution_fixture import execution_evidence


def native_hit(attacker=None, victim=None):
    raw = asdict(_hit())
    raw['native_evidence'] = {'payload_json': json.dumps({
        'providerId': 'fixture-provider', 'captureId': 'fixture-capture', 'hitId': '1',
        'rawHit': {'captureStage': 'hit_callback', 'effectsStage': 'settlement_snapshot',
                   'unixUs': '1000000', 'hitObservedUnixUs': '900000',
                   'settlementObservedUnixUs': '1100000',
                   'attackerEffects': attacker, 'victimEffects': victim,
                   'futureField': {'mustRemain': 42}},
    })}
    return decode(raw, BattleAnalysisHit)


class NativeHitEvidenceTests(unittest.TestCase):
    def test_execution_inputs_are_diagnostics_without_promoting_incomplete_fields(self):
        original = native_hit().native_evidence
        payload = json.loads(original.payload_json)
        payload["rawHit"]["executionEvidence"] = execution_evidence()
        evidence = replace(original, payload_json=json.dumps(payload))
        text = render_native_evidence(evidence)
        for expected in ("执行 ID：9007199254740993", "18446744073709551615",
                         "1788800000000010", "1788800000000090", "size_limit",
                         "2143289344", "invalid_value", "outputModifiers", "not_observed"):
            self.assertIn(expected, text)
        self.assertIn("不等于该项实际被公式采用", text)
        self.assertIn("原生暴击：未知", text)
        self.assertEqual(payload, json.loads(evidence.payload_json))

    def test_execution_missing_future_and_invalid_values_do_not_mean_empty_inputs(self):
        original = native_hit().native_evidence
        self.assertIn("本击没有已关联的执行输入", render_native_evidence(original))
        payload = json.loads(original.payload_json)
        for value, expected in (({"schemaVersion": 2}, "版本尚不支持"),
                                ({"schemaVersion": True}, "版本尚不支持"),
                                ([], "格式无效"),
                                ({"schemaVersion": 1, "before": {"x": float("nan")}}, "格式无效")):
            payload["rawHit"]["executionEvidence"] = value
            self.assertIn(expected, render_native_evidence(replace(original, payload_json=json.dumps(payload))))

    def test_log_uses_core_field_evidence_before_fit_even_without_replay(self):
        from PySide6.QtWidgets import QApplication
        from src.features.battle_report.analysis_view import BattleLongAnalysisView
        app = QApplication.instance() or QApplication([])
        view = BattleLongAnalysisView()
        evidence = BattleHitFieldEvidence(
            critical_state="non_critical", critical_source="native_execution",
            critical_confidence="高", damage_attribute_source="native_execution",
            damage_attribute_confidence="高", notes=("独立字段证据",),
        )
        hit = replace(_hit(), field_evidence=evidence)
        try:
            view._analysis = SimpleNamespace(hits=(hit,), hit_replays=(), time_stop_intervals=())
            view._render_log()
            self.assertIn("未暴击", view.log_table.item(0, 8).text())
            self.assertIn("观测", view.log_table.item(0, 8).text())
            self.assertIn("native_execution", view.log_table.item(0, 8).toolTip())
            self.assertIn("独立字段证据", view.log_table.item(0, 4).toolTip())
            replay = BattleHitReplayResult(
                event_id=hit.event_id, observed_damage=1000, non_critical_damage=500,
                critical_damage=1000, selected_damage=1000, selected_error_percent=0,
                critical_state="critical", confidence="中", factors=(),
            )
            view._analysis.hit_replays = (replay,)
            view._render_log()
            self.assertIn("未暴击（执行观测", view.log_table.item(0, 8).text())
            view._analysis.hits = (replace(hit, field_evidence=replace(
                evidence, critical_state="unknown", critical_source="unknown",
                critical_confidence="未知")),)
            view._render_log()
            self.assertEqual("暴击（推断，中）", view.log_table.item(0, 8).text())
            app.processEvents()
        finally:
            view.close()

    def test_field_evidence_wire_keeps_unknown_and_not_applicable_distinct(self):
        from src.integrations.nte_analysis_core import NativeAnalysisError
        raw = asdict(_hit())
        raw["field_evidence"] = {
            "critical_state": "not_applicable", "critical_source": "static_rule",
            "critical_confidence": "高", "damage_attribute_source": "unknown",
            "damage_attribute_confidence": "未知", "notes": [],
        }
        hit = decode(raw, BattleAnalysisHit)
        self.assertEqual(hit.field_evidence.critical_state, "not_applicable")
        raw["field_evidence"]["critical_state"] = "unknown"
        self.assertEqual(decode(raw, BattleAnalysisHit).field_evidence.critical_state, "unknown")
        raw["field_evidence"]["critical_state"] = "certain"
        with self.assertRaises(NativeAnalysisError):
            decode(raw, BattleAnalysisHit)

    def test_independent_metadata_and_health_phases_keep_exact_values(self):
        original = native_hit().native_evidence
        payload = json.loads(original.payload_json)
        payload["rawHit"].update({"metadataKnown": False, "criticalKnown": True,
            "critical": False, "criticalSource": "native_execution", "damageTypeKnown": False,
            "damageType": 0, "damage": 5309, "victimHp": 629219.8125,
            "victimMaxHp": 1250000.0, "victimHpStage": "server_settlement_payload",
            "victimMaxHpStage": "before_settlement_dispatch"})
        evidence = replace(original, payload_json=json.dumps(payload))
        text = render_native_evidence(evidence)
        self.assertIn("原生暴击：否", text)
        self.assertIn("伤害类型原始枚举：未知", text)
        self.assertIn("629219.8125", text)
        self.assertIn("server_settlement_payload", text)
        self.assertIn("before_settlement_dispatch", text)
        self.assertNotIn("时停未采集", text)
        payload["rawHit"].update({"displayType": 24, "criticalKnown": False})
        weave = render_native_evidence(replace(original, payload_json=json.dumps(payload)))
        self.assertIn("原生暴击：未知", weave)
        self.assertIn("正式显示类型：24", weave)
        self.assertEqual(json.loads(evidence.payload_json)["rawHit"]["damage"], 5309)

    def test_effect_content_version_is_not_the_hit_read_time(self):
        original = native_hit({"effects": [], "complete": True, "ended": False,
                               "observedUs": "100"}).native_evidence
        payload = json.loads(original.payload_json)
        payload["rawHit"]["attackerEffectsObservedUnixUs"] = "900"
        text = render_native_evidence(replace(original, payload_json=json.dumps(payload)))
        self.assertIn("快照内容版本时间（Unix 微秒）：100", text)
        self.assertIn("本击来源侧 / 目标侧效果读取时间（Unix 微秒）：900 / 未知", text)

    def test_old_missing_and_sampled_empty_are_different(self):
        self.assertIn('未采集原生', render_native_evidence(_hit().native_evidence))
        unknown = render_native_evidence(native_hit().native_evidence)
        self.assertIn('未取得快照', unknown)
        self.assertNotIn('有效空列表', unknown)
        empty = {'effects': [], 'complete': True, 'ended': False, 'status': 'ok'}
        text = render_native_evidence(native_hit(empty).native_evidence)
        self.assertIn('已采集有效空列表', text)
        self.assertIn('全场来源覆盖未知', text)
        partial = render_native_evidence(native_hit({**empty, 'complete': False}).native_evidence)
        self.assertNotIn('有效空列表', partial)

    def test_effect_ownership_suppression_and_phase_are_preserved(self):
        snapshot = {'effects': [{'name': '采样效果', 'key': '/Game/Fixture/GE_Buff',
                    'instanceKey': 'effect:7', 'stacks': 3, 'inhibited': True,
                    'source': 'fixture-source'}], 'complete': True, 'ended': False}
        hit = native_hit(None, snapshot)
        text = render_native_evidence(hit.native_evidence)
        self.assertIn('效果采样阶段：settlement_snapshot', text)
        self.assertIn('层数：3', text)
        self.assertIn('已抑制：是', text)
        self.assertGreater(text.index('采样效果'), text.index('受击目标侧效果'))
        self.assertIn('不能', render_native_evidence(native_hit({'effects': None}).native_evidence))
        self.assertEqual(json.loads(hit.native_evidence.payload_json)['rawHit']['futureField'],
                         {'mustRemain': 42})
        saved = json.loads(hit.native_evidence.payload_json)['rawHit']
        self.assertEqual(saved['hitObservedUnixUs'], '900000')
        self.assertEqual(saved['settlementObservedUnixUs'], '1100000')
        self.assertEqual(saved['unixUs'], '1000000')
        self.assertEqual(saved['victimEffects'], snapshot)
        self.assertIsNone(saved['attackerEffects'])
        # Formula context changes never reassign the observed actor or modify the source payload.
        self.assertEqual(replace(hit, character_id=1001).native_evidence, hit.native_evidence)

    def test_dialog_shows_native_and_inferred_sections_without_recalculating(self):
        from unittest.mock import patch
        from PySide6.QtWidgets import QApplication
        from src.features.battle_report.hit_buff_dialog import BattleHitBuffDialog
        app = QApplication.instance() or QApplication([])
        dialog = BattleHitBuffDialog()
        try:
            with patch('src.services.battle_buff_attribute_projection_service.'
                       'BattleBuffAttributeProjectionService.project_hit',
                       side_effect=AssertionError('native evidence entered inference')):
                dialog.show_for_hit(native_hit({'effects': [], 'complete': True, 'ended': False}), ())
            app.processEvents()
            text = dialog.detail.toPlainText()
            self.assertIn('已采集有效空列表', text)
            self.assertIn('规则推断与公式重放', text)
            self.assertNotIn('futureField', text)
        finally:
            dialog.close()


if __name__ == '__main__':
    unittest.main()
