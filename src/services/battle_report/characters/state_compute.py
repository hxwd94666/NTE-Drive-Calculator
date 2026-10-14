# 调度角色状态纯计算并恢复法帝娅生命上限区间的不可变证据。
from __future__ import annotations
from collections.abc import Callable
from math import isfinite
from typing import Any
from src.domain.battle_report.buffs.models import BattleBuffModifierEvidence, BattleInferredBuffInterval
from src.domain.battle_report.analysis.backend_contracts import BattleComputeBackend


def compute_character_state(
    kind: str, inputs: dict[str, Any], *,
    backend: BattleComputeBackend | None, checkpoint: Callable[[], None] | None,
) -> dict[str, Any] | None:
    if not isinstance(backend, BattleComputeBackend) or not backend.supports_battle_compute:
        return None
    # 请求 checkpoint 同时守取消边界；原生结果按固定 contract 校验后才交付。
    if checkpoint:
        checkpoint()
    rows = backend.compute_batch("character_state_v1", ({**inputs, "kind": kind},),
                                 checkpoint=checkpoint)
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise ValueError("invalid_character_state_result")
    return rows[0]


def restore_fadia_intervals(result: dict[str, Any]) -> tuple[BattleInferredBuffInterval, ...]:
    rows = result.get("intervals")
    if not isinstance(rows, list):
        raise ValueError("invalid_character_state_result")
    restored = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_character_state_result")
        start, end, ordinal, event, half, value, observed = (
            row.get("start_us"), row.get("end_us"), row.get("ordinal"),
            row.get("event_id"), row.get("half"), row.get("stack_hp"), row.get("observed"),
        )
        if (
            type(start) is not int or type(end) is not int or start >= end
            or type(ordinal) is not int or ordinal <= 0
            or not isinstance(event, str) or not isinstance(half, str)
            or type(value) not in (int, float) or not isfinite(value) or value <= 0
            or type(observed) is not bool
        ):
            raise ValueError("invalid_character_state_result")
        restored.append(BattleInferredBuffInterval(
            interval_id=f"fadia:dark-star-hp:{ordinal}:{event}",
            buff_asset_path="confirmed:character_passive:1039:dark-star-hp",
            buff_name="法帝娅被动：生命上限汲取",
            source_effect_definition_id="character_passive:1039:GA_Fadia_Passive_1",
            source_kind="confirmed_character_text_and_formal_hit",
            source_character_id=1039, source_character_name="法帝娅", target_scope="team",
            start_us=start, end_us=end, stacks=1, duration_policy="until_battle_end",
            state_confidence="高", value_confidence="高" if observed else "中",
            inference_basis=(
                "正式 Buff_Reaction_4_new 逐击表明黯星已结算；"
                "正式 HTExtractAttributeGEComp 以来源当时 MAXHP 的 10%"
                "给全队加生命上限，最多 5 次；"
                + (f"按{half}半场独立累计并在换半时清空；" if half else "")
                + ("本次目标最大生命正式下降值按 200% 反推来源当时 MAXHP。"
                   if observed else "缺少本次目标下降值，使用冻结 PanelHP、三觉与前序本机制层数递推。")
            ),
            trigger_event_type="FORMAL_DARK_STAR_SETTLED", evidence_action_ids=(),
            evidence_event_ids=(event,),
            modifiers=(BattleBuffModifierEvidence(
                property_id="HPMaxAdd", modifier_operation="EGameplayModOp::Additive",
                magnitude_kind="derived:fadia_source_current_max_hp*0.10", magnitude_value=value,
                calculation_asset_path="", value_confidence="高" if observed else "中",
            ),),
            stacking_type="AggregateBySource", stack_limit_count=5,
        ))
    return tuple(restored)


def state_ranges(result: dict[str, Any], name: str) -> tuple[tuple[int, int], ...]:
    rows = result.get(name)
    if not isinstance(rows, list) or not all(
        isinstance(row, list) and len(row) == 2 and all(type(value) is int for value in row)
        for row in rows
    ):
        raise ValueError("invalid_character_state_result")
    return tuple((row[0], row[1]) for row in rows)


def state_ids(result: dict[str, Any], name: str) -> tuple[str, ...]:
    values = result.get(name)
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError("invalid_character_state_result")
    return tuple(values)
