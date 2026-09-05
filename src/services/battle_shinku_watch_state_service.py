# 按真红主动命中与切人弱锚估算凝视层数，保留无法恢复的状态缺口。
"""Forward Watch estimates from frozen timing, never from observed damage."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit, BattleAnalysisSnapshot, BattleSkillDamageEvidence,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE, projected_range_duration_us,
)


_WATCH = "ge_player_shinku_watch_damage"
_WATCH_EX = "ge_player_shinku_watchex_damage"
_WATCH_DAMAGE_IDS = frozenset({_WATCH, _WATCH_EX})
_PAIRED_SETTLEMENT_WINDOW_US = 150_000
_SAME_SETTLEMENT_US = 1_000
_ENTRY_DELAY_US = 2_000_000
_STACK_PERIOD_US = 1_000_000


@dataclass(frozen=True, slots=True)
class _WatchEstimate:
    stacks: int
    basis: str


def _selected_effects(character: Mapping[str, Any]) -> frozenset[str]:
    profile = character.get("profile") or {}
    if profile.get("awakening_selection_initialized"):
        return frozenset(str(value) for value in (
            profile.get("selected_awaken_effect_ids") or ()
        ))
    level = int(profile.get("awakening_level") or character.get("awakening_level") or 0)
    return frozenset(f"Effect{index}" for index in range(1, level + 1))


def _is_active_shinku_hit(hit: BattleAnalysisHit) -> bool:
    effect = hit.gameplay_effect_id.casefold()
    return bool(
        hit.character_id == 1076 and hit.direction == "outgoing"
        and hit.classification == "direct" and not hit.is_follow_up
        and effect.startswith("ge_player_shinku_")
        and effect not in _WATCH_DAMAGE_IDS
        and not any(token in effect for token in ("reaction", "qte", "passive"))
    )


def _unknown(reason: str) -> _WatchEstimate:
    return _WatchEstimate(0, "威慑凝视层数未解析：" + reason + (
        "；保留正式每层技能曲线，不以默认一层、觉醒上限或实测伤害反推层数。"
    ))


def _estimate_one(
    hit: BattleAnalysisHit,
    *,
    analysis: BattleAnalysisSnapshot,
    context_hits: Sequence[BattleAnalysisHit],
    selected: frozenset[str],
) -> _WatchEstimate:
    if not getattr(analysis, "axis_complete", False):
        return _unknown("逐击轴不完整，无法建立连续的增长与重置弱锚")
    # The triggering attack and Watch can share one server settlement.
    earlier = tuple(
        row for row in context_hits
        if row.relative_time_us < hit.relative_time_us - _SAME_SETTLEMENT_US
        and row.scope_half == hit.scope_half
        and (
            _is_active_shinku_hit(row)
            or (
                row.direction == "outgoing"
                and row.gameplay_effect_id.casefold() in _WATCH_DAMAGE_IDS
                and row.relative_time_us
                < hit.relative_time_us - _PAIRED_SETTLEMENT_WINDOW_US
            )
        )
    )
    if not earlier:
        return _unknown("本击之前没有真红主动命中或凝视消费锚，开场前积层未知")
    anchor = max(earlier, key=lambda row: (row.relative_time_us, row.sequence))
    retains_off_field = "Effect3" in selected
    if not retains_off_field:
        # Only inferred foreground E/Q actions are weak switch evidence.
        # Teammate damage, QTE and autonomous A hits are not switch facts.
        switched_out = any(
            action.character_id != 1076 and action.input_kind in {"E", "Q"}
            and anchor.relative_time_us < action.start_us < hit.relative_time_us
            and any(
                row.event_id in action.evidence_event_ids
                and row.scope_half == hit.scope_half
                and row.direction == "outgoing" and row.classification == "direct"
                and not row.is_follow_up
                for row in context_hits
            )
            for action in getattr(analysis, "inferred_actions", ())
        )
        if switched_out:
            return _unknown(
                "第3项觉醒未启用，最近真红命中后出现队友前台技能切人弱锚；"
                "切出应清层，但切回后的无攻击停留起点未采集"
            )
    active_us = projected_range_duration_us(
        anchor.relative_time_us, hit.relative_time_us,
        intervals=getattr(analysis, "time_stop_intervals", ()),
        mode=ACTIVE_TIME_MODE,
    )
    if active_us < _ENTRY_DELAY_US:
        return _unknown("最近主动命中后扣停表空窗不足两秒，计时弱模型无法解释本击")
    # Explicit weak convention: first stack at the 2 s entry boundary, then
    # each complete second. Real first-tick phase is not in this capture.
    stacks = 1 + (active_us - _ENTRY_DELAY_US) // _STACK_PERIOD_US
    trigger_evade = any(
        _is_active_shinku_hit(row) and row.target_id == hit.target_id
        and row.scope_half == hit.scope_half
        and abs(row.relative_time_us - hit.relative_time_us) <= _SAME_SETTLEMENT_US
        and "perfectevade" in row.gameplay_effect_id.casefold()
        for row in context_hits
    )
    if trigger_evade:
        stacks += 1
    cap = 16 if "Effect4" in selected else 8
    stacks = min(cap, stacks)
    return _WatchEstimate(stacks, (
        f"威慑凝视弱推断：以前次真红主动命中/凝视消费 {anchor.event_id} "
        f"为重置锚，按本场停表投影扣除后的空窗 {active_us / 1_000_000:.3f} 秒；"
        "假设空窗内无未命中主动攻击、无额外未记录极限闪避，"
        "并假设满两秒进入时获得首层、随后每满一秒增加一层。"
        + ("本次同批极限反击提供一次额外闪避层的弱锚。" if trigger_evade else "")
        + ("第3项觉醒启用，切人不清层并允许后台增长；" if retains_off_field else
           "第3项觉醒未启用，未发现打断本段的前台切人弱锚；")
        + f"按冻结觉醒取上限 {cap} 层，估算 {stacks} 层。"
        "这不是 Core 实测层数；未命中攻击、切换和首层相位仍可能使估算偏移。"
    ))


def apply_shinku_watch_state_boundary(
    evidence: Sequence[BattleSkillDamageEvidence],
    *,
    analysis: BattleAnalysisSnapshot,
    character: Mapping[str, Any] | None,
) -> tuple[BattleSkillDamageEvidence, ...]:
    """Use only a bounded weak estimate; keep missing anchors unresolved."""
    context_hits = tuple(getattr(analysis, "timeline_hits", ()) or analysis.hits)
    hits = {row.event_id: row for row in context_hits}
    selected = _selected_effects(character or {})
    estimates: dict[str, _WatchEstimate] = {}
    for row in evidence:
        if row.damage_id.casefold() not in _WATCH_DAMAGE_IDS:
            continue
        hit = hits.get(row.event_id)
        if hit is None or character is None:
            estimates[row.event_id] = _unknown("缺少冻结角色或本击原始身份")
            continue
        if row.damage_id.casefold() == _WATCH_EX:
            paired = tuple(
                other for other in context_hits
                if other.gameplay_effect_id.casefold() == _WATCH
                and other.target_id == hit.target_id and other.scope_half == hit.scope_half
                and abs(other.relative_time_us - hit.relative_time_us)
                <= _PAIRED_SETTLEMENT_WINDOW_US
            )
            if len(paired) != 1:
                estimates[row.event_id] = _unknown(
                    "追加凝视在同目标有界时间窗内没有唯一主凝视配对"
                )
                continue
            hit = paired[0]
        estimates[row.event_id] = _estimate_one(
            hit, analysis=analysis, context_hits=context_hits, selected=selected,
        )
    return tuple(
        replace(
            row, state_multiplier=float(estimates[row.event_id].stacks),
            state_multiplier_label=(
                "威慑凝视结算层数（弱推断）" if estimates[row.event_id].stacks else
                "威慑凝视结算层数（未解析）"
            ),
            state_multiplier_basis=estimates[row.event_id].basis,
            state_confidence="低" if estimates[row.event_id].stacks else "未解析",
        )
        if row.event_id in estimates else row
        for row in evidence
    )


__all__ = ["apply_shinku_watch_state_boundary"]
