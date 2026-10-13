# 将冻结同频证据送入原生推断，并恢复各角色归属与中文依据。
from __future__ import annotations

from src.services.battle_report.native.axis_compute import serialize_animation_candidate

_BASIS = {
    "core_pair": "队友 QTE 后紧邻灵可 UltraSkillLTE_AOE，且二者位于同一 "
                 "nte-core 战报记录的时停区间；该区间仅作中置信上下文辅助，"
                 "QTE→LTE 配对才是触发推论主体。",
    "fallback_pair": "队友 QTE 后紧邻灵可 UltraSkillLTE_AOE；二者同时落在低置信 "
                     "Q 动作回退区间。回退区间不能单独证明触发，只与独立配对共同形成低置信推论。",
    "pair": "队友 QTE 后在 150ms 内紧邻同目标灵可 UltraSkillLTE_AOE；"
            "当前没有唯一且覆盖完整配对的可用时停区间，因此只记录低置信"
            "同频响应配对，不据此证明由灵可 Q 或 E 触发。",
    "type6_skill": "完整有序的灵可 E 四段逐击后，在唯一静态动画响应窗口内"
                    "出现首个同目标队友 QTE，且没有跨越时停或其他已认领 QTE"
                    "；唯一 type6 选人区间覆盖或紧邻该 QTE，但不能单独证明同频触发。",
    "legacy_skill": "完整有序的灵可 E 四段逐击后，在唯一静态动画响应窗口内"
                     "出现首个同目标队友 QTE，且没有跨越时停或其他已认领 QTE"
                     "；旧战报无类型证据，采用窗口内最早的唯一合法 QTE 回退。",
}


def infer_native_linko_coattack(
    backend, hits, actions, *, time_stop_projection, animation_candidates, type6_evidence,
    allow_legacy_e_fallback, character_elements, checkpoint=None,
):
    from src.services.battle_report.characters.linko_inference import _QteAction, _inference

    # 只传冻结来源证据；类型 6、配对与低置信回退仍由正式推断负责。
    payload = {
        "hits": [{
            "event_id": hit.event_id, "sequence": hit.sequence,
            "relative_time_us": hit.relative_time_us, "character_id": hit.character_id,
            "direction": hit.direction, "is_follow_up": hit.is_follow_up,
            "ability_id": hit.ability_id, "gameplay_effect_id": hit.gameplay_effect_id,
            "target_id": hit.target_id,
        } for hit in hits],
        "actions": [{
            "action_id": action.action_id, "character_id": action.character_id,
            "input_kind": action.input_kind, "start_us": action.start_us,
            "evidence_event_ids": action.evidence_event_ids,
        } for action in actions],
        "time_stop_projection": {
            "intervals": time_stop_projection.intervals,
            "source_kind": time_stop_projection.source_kind,
            "confidence": time_stop_projection.confidence,
            "non_type6_intervals": time_stop_projection.non_type6_intervals,
        },
        "animation_candidates": [serialize_animation_candidate(row) for row in animation_candidates],
        "type6_evidence": [{
            "event_id": row.event_id, "relative_time_us": row.relative_time_us,
            "end_relative_time_us": row.end_relative_time_us, "target_id": row.target_id,
        } for row in type6_evidence],
        "allow_legacy_e_fallback": allow_legacy_e_fallback,
    }
    response = backend.compute_batch("linko_coattack_v1", (payload,), checkpoint=checkpoint)
    if len(response) != 1:
        raise ValueError("Native Linko response count mismatch")
    groups = []
    for ordinal, row in enumerate(response[0]["inferences"]):
        if checkpoint is not None and ordinal % 64 == 0:
            checkpoint()
        action_index = row["qte_action"]
        indices = row["hit_indices"]
        if type(action_index) is not int or not 0 <= action_index < len(actions):
            raise ValueError("Native Linko action index mismatch")
        if not indices or any(type(index) is not int or not 0 <= index < len(hits) for index in indices):
            raise ValueError("Native Linko hit index mismatch")
        qte = _QteAction(actions[action_index], tuple(hits[index] for index in indices))
        groups.append(_inference(
            qte, trigger_kind=row["trigger_kind"], confidence=row["confidence"],
            basis=_BASIS[row["basis_kind"]], evidence_event_ids=row["evidence_event_ids"],
            trigger_action_id=row["trigger_action_id"], raw_gap_us=row["raw_gap_us"],
            active_gap_us=row["active_gap_us"], time_stop_source_kind=row["time_stop_source_kind"],
            time_stop_confidence=row["time_stop_confidence"], character_elements=character_elements or {},
            selection_pause_start_us=row["selection_pause_start_us"],
            selection_pause_end_us=row["selection_pause_end_us"],
        ))
    results = []
    for group, index in response[0]["order"]:
        if type(group) is not int or not 0 <= group < len(groups):
            raise ValueError("Native Linko group index mismatch")
        if type(index) is not int or not 0 <= index < len(groups[group]):
            raise ValueError("Native Linko result index mismatch")
        results.append(groups[group][index])
    return tuple(results)
