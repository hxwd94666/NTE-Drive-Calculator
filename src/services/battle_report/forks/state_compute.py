# 调度弧盘专属状态批量计算并恢复原有区间证据和展示文本。
from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from src.services.battle_report.native.state_payload import HIT_FIELDS, ACTION_FIELDS, RULE_FIELDS, EVENT_FIELDS, state_rows
from src.domain.battle_report import BattleInferredBuffInterval
from src.domain.native_analysis import BattleComputeBackend


_BASIS = {
    "damage_stack": (
        "按逐击正向重放：咒属性直伤、DOT 与浊燃在伤害结算后叠层；"
        "0.3 秒冷却和 15 秒持续时间均使用扣时停时钟。"
    ),
    "wush_main": (
        "Q 开始即建立 E/Q 增伤，触发它的本次 Q 也享受；"
        "持续时间按扣时停时钟推进，窗口内再次 Q 不刷新。"
    ),
    "wush_stack": (
        "E 在技能实际结束后增加一层；本次 E 只读取"
        "释放前层数，最多两层。"
    ),
    "gold_record": (
        "装备者每次独立 QTE 开始事件只增加一层 Q 暴击伤害；"
        "QTE 的逐击数量不复制叠层。"
    ),
    "gold_record_final": (
        "装备者每次独立 QTE 开始事件只增加一层 Q 暴击伤害；"
        "QTE 的逐击数量不复制叠层；从该次 QTE 开始时生效。"
    ),
    "door": "按统一来源侧治疗事件刷新；满血或零有效治疗仍可触发。",
    "bit_base": (
        "由直接 A/E/Q/QTE 动作切换来源重建前后台；装备者进出前台"
        "时对应状态与层数立即重置。"
    ),
    "bit_stack": (
        "每个被冷却接受的伤害时点只叠一层；同一时点多目标不"
        "重复计层，新层从该击结算后一微秒开始，切换状态清空。"
    ),
    "bitter": (
        "静态触发为受击计算前；区间从本次受击时点开始，故触发"
        "它的本次攻击已读取防御提升，来源侧 20 秒冷却。"
    ),
    "blast": (
        "Q 开始即获得攻击提升，触发它的本次 Q 也享受；"
        "重复触发只刷新而不叠加。"
    ),
    "butterfly": (
        "Q 开始立即把附着物增伤替换到强化档，触发 Q 期间的附着物"
        "伤害享受；重复 Q 只刷新六秒窗口。"
    ),
    "after_e": (
        "E 技能实际结束后一微秒生效，触发它的本次 E 不享受；"
        "同名效果不可叠加，重复 E 只刷新持续时间。"
    ),
    "gold_wool": (
        "Q 开始立即生效且本次 Q 享受；E 实际结束后一微秒生效，"
        "本次 E 不享受；任一触发都只刷新同一二十秒窗口。"
    ),
    "knight": (
        "消费逐击公式重放判定的暴击证据；新层在触发击后一微秒生效，"
        "来源侧 0.3 秒冷却，最多十层且刷新整组十秒持续时间。"
    ),
    "lunar": (
        "Q 开始立即获得光属性增伤与无视防御，本次 Q 享受；"
        "效果不可叠加，重复 Q 刷新二十秒持续时间。"
    ),
    "motor": (
        "进入前台时从零层开始，完整经过一秒才获得首层；"
        "按战报原始时间继续周期叠层，时停不暂停，最多五层；"
        "离开前台立即清空，重新入场重新计时。"
    ),
    "nest": (
        "只消费 Q 动作绑定的真实逐击及其 target_id；标记从"
        "触发击后一微秒生效，同目标刷新二十秒，多目标独立。"
    ),
}


# 只派发已支持的冻结描述；checkpoint 保留取消，完整返回后才恢复区间。
def compute_fork_intervals(
    rules: Sequence[Any],
    *,
    actions: Sequence[Any],
    hits: Sequence[Any],
    battle_end_us: int,
    time_stop_intervals: Sequence[tuple[int | None, int | None]],
    treatment_events: Sequence[Any],
    critical_events: Sequence[Any],
    backend: BattleComputeBackend | None,
    checkpoint: Callable[[], None] | None,
) -> tuple[BattleInferredBuffInterval, ...] | None:
    if not isinstance(backend, BattleComputeBackend) or not backend.supports_battle_compute:
        return None
    if checkpoint:
        checkpoint()
    inputs = {
        "rules": state_rows(rules, RULE_FIELDS),
        "actions": state_rows(actions, ACTION_FIELDS),
        "hits": state_rows(hits, HIT_FIELDS),
        "battle_end_us": battle_end_us,
        "time_stop_intervals": list(time_stop_intervals),
        "treatment_events": state_rows(treatment_events, EVENT_FIELDS),
        "critical_events": state_rows(critical_events, EVENT_FIELDS),
    }
    responses = backend.compute_batch("fork_state_v1", (inputs,), checkpoint=checkpoint)
    if len(responses) != 1 or not isinstance(responses[0].get("intervals"), list):
        raise ValueError("invalid_fork_state_result")
    results = []
    for row in responses[0]["intervals"]:
        if checkpoint:
            checkpoint()
        results.append(_restore(rules, row))
    return tuple(results)


def _restore(rules: Sequence[Any], row: Any) -> BattleInferredBuffInterval:
    if not isinstance(row, dict):
        raise ValueError("invalid_fork_state_result")
    index, start, end, stacks = (
        row.get("rule_index"), row.get("start_us"), row.get("end_us"), row.get("stacks"),
    )
    kind, suffix, basis, scope = (
        row.get("kind"), row.get("suffix"), row.get("basis_key"), row.get("target_scope"),
    )
    actions, events = row.get("action_ids"), row.get("event_ids")
    if (
        type(index) is not int or not 0 <= index < len(rules)
        or type(start) is not int or type(end) is not int or end <= start
        or type(stacks) is not int or stacks <= 0
        or kind not in ("fork", "fork-state", "fork-trigger", "fork-periodic", "damage-stack")
        or not isinstance(suffix, str) or not isinstance(basis, str) or basis not in _BASIS
        or (scope is not None and not isinstance(scope, str))
        or not isinstance(actions, list) or not isinstance(events, list)
        or not all(isinstance(item, str) for item in (*actions, *events))
    ):
        raise ValueError("invalid_fork_state_result")
    rule = rules[index]
    return BattleInferredBuffInterval(
        interval_id=f"buff:{kind}:{suffix}:{rule.rule_id}",
        buff_asset_path=rule.target_asset_path,
        buff_name=rule.target_name,
        source_effect_definition_id=rule.source_effect_definition_id,
        source_kind=rule.source_kind,
        source_character_id=rule.source_character_id,
        source_character_name=rule.source_character_name,
        target_scope=scope or rule.target_scope,
        start_us=start, end_us=end, stacks=stacks,
        duration_policy=rule.duration_policy,
        state_confidence="中", value_confidence="高",
        inference_basis=_BASIS[basis],
        trigger_event_type=rule.event_type,
        evidence_action_ids=tuple(actions), evidence_event_ids=tuple(events),
        modifiers=rule.modifiers, stacking_type=rule.stacking_type,
        stack_limit_count=rule.stack_limit_count,
    )
