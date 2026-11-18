# 区分公式暴击分支与原生观测标记，避免把飘字标记当作伤害倍率证据。
from src.domain.localization.catalog import localized_text
from src.domain.battle_report.timeline.events import BattleAnalysisHit
from src.domain.battle_report.replay.models import BattleHitReplayResult


def critical_explanation(hit: BattleAnalysisHit, replay: BattleHitReplayResult) -> str:
    states = {"critical": "是", "non_critical": "否", "not_applicable": "不适用", "unknown": "未知"}
    evidence = hit.field_evidence
    if replay.critical_policy == "disabled" and replay.critical_state == "not_applicable":
        label = "公式暴击：不适用（当前公式不使用暴击倍率）"
        if evidence is not None and evidence.critical_source == "native_prediction":
            label += localized_text(None, 'auto.services.battle_report.replay.critical_explanation.critical_explanation.f9f763ce0', '；原生预测/飘字暴击标记：{value}', value=f"{states.get(evidence.critical_state, '未知')}")
        return label
    if evidence is not None and (evidence.critical_source.startswith("native_") or evidence.critical_source == "server_settlement"):
        source = {"native_prediction": "原生预测/飘字", "native_execution": "原生执行", "server_settlement": "原生结算"}.get(evidence.critical_source, "原生观测")
        return localized_text(None, 'auto.services.battle_report.replay.critical_explanation.critical_explanation.f8bfd918b', '{source}暴击：{value}（字段置信度{critical_confidence}）', source=f'{source}', value=f"{states.get(evidence.critical_state, '未知')}", critical_confidence=f'{evidence.critical_confidence}')
    return localized_text(None, 'auto.services.battle_report.replay.critical_explanation.critical_explanation.f65380b4e', '推断暴击：{value}（置信度{confidence}）', value=f'{states.get(replay.critical_state, replay.critical_state)}', confidence=f'{replay.confidence}')
