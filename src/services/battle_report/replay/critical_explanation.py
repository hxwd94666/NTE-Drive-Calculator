# 区分公式暴击分支与原生观测标记，避免把飘字标记当作伤害倍率证据。
from src.domain.battle_report import BattleAnalysisHit, BattleHitReplayResult


def critical_explanation(hit: BattleAnalysisHit, replay: BattleHitReplayResult) -> str:
    states = {"critical": "是", "non_critical": "否", "not_applicable": "不适用", "unknown": "未知"}
    evidence = hit.field_evidence
    if replay.critical_policy == "disabled" and replay.critical_state == "not_applicable":
        label = "公式暴击：不适用（当前公式不使用暴击倍率）"
        if evidence is not None and evidence.critical_source == "native_prediction":
            label += f"；原生预测/飘字暴击标记：{states.get(evidence.critical_state, '未知')}"
        return label
    if evidence is not None and (evidence.critical_source.startswith("native_") or evidence.critical_source == "server_settlement"):
        source = {"native_prediction": "原生预测/飘字", "native_execution": "原生执行", "server_settlement": "原生结算"}.get(evidence.critical_source, "原生观测")
        return f"{source}暴击：{states.get(evidence.critical_state, '未知')}（字段置信度{evidence.critical_confidence}）"
    return f"推断暴击：{states.get(replay.critical_state, replay.critical_state)}（置信度{replay.confidence}）"
