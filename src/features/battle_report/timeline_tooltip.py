# 构建统一时间轴点击和悬停共享的证据详情。
"""Qt-free tooltip projection for timeline selections."""

from __future__ import annotations

from collections.abc import Callable

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleInferredAction,
    BattleInferredInput,
    BattleTimelineDamageGroup,
)
from src.features.battle_report.timeline_layout import (
    TimelineSelection,
    format_damage,
    format_time,
)
from src.services.skill_name_rendering_service import (
    preferred_battle_damage_name,
    render_battle_event_type,
)


def build_timeline_tooltip(
    selected: TimelineSelection,
    *,
    projected_time: Callable[[int], int],
    hit_heading: str = "正式逐击",
) -> str:
    if selected.kind == "hit":
        hit = selected.payload
        assert isinstance(hit, BattleAnalysisHit)
        damage_name = preferred_battle_damage_name(
            hit.damage_name,
            hit.skill_name,
            hit.ability_id,
        )
        source = (
            f"\n来源技能：{hit.skill_name}"
            if hit.skill_name not in {"", damage_name, "未识别技能"}
            else ""
        )
        return (
            f"{hit_heading} · {format_time(projected_time(hit.relative_time_us))} · "
            f"{hit.character_name}\n{damage_name}{source}\n"
            f"逐击 ID：{hit.event_id}\n"
            f"{render_battle_event_type(hit.classification, hit.attack_type, hit.damage_attribute)}"
            f" · {hit.target_name} · {format_damage(hit.damage)}"
        )
    if selected.kind == "damage_group":
        group = selected.payload
        assert isinstance(group, BattleTimelineDamageGroup)
        source = (
            f"\n来源技能：{group.source_skill_name}"
            if group.source_skill_name
            not in {"", group.damage_name, "未识别技能"}
            else ""
        )
        vital_group = group.channel_key in {
            "max_hp_reduction",
            "max_hp_reduction_estimated",
        }
        title = (
            "生命上限描述估算"
            if group.channel_key == "max_hp_reduction_estimated"
            else "生命上限派生结算"
            if vital_group
            else "技能伤害组"
        )
        details = "" if not group.detail_lines else "\n" + "\n".join(group.detail_lines)
        footer = (
            "观测到的最大生命下降与机制归因分开保存；该值不改写正式逐击。"
            if group.channel_key == "max_hp_reduction"
            else "技能描述弱证据；默认不计入正式有效伤害。"
            if group.channel_key == "max_hp_reduction_estimated"
            else "横条粗细按整组伤害缩放；圆点大小按单击伤害缩放。"
        )
        return (
            f"{title} · {group.character_name} · {group.channel_label}\n"
            f"{group.damage_name}{source}\n"
            f"{format_time(projected_time(group.start_us))}—"
            f"{format_time(projected_time(group.end_us))} · "
            f"{group.hits} 次 · {format_damage(group.damage)}{details}\n"
            f"{footer}"
        )
    if selected.kind == "action":
        action = selected.payload
        assert isinstance(action, BattleInferredAction)
        return (
            f"推算动作 · {action.character_name} · 身份置信度{action.identity_confidence} / "
            f"时间置信度{action.timing_confidence}\n"
            f"{action.input_sequence} · {action.action_name}\n"
            f"{format_time(projected_time(action.start_us))}—"
            f"{format_time(projected_time(action.end_us))} · "
            f"{action.hits} 击 · {format_damage(action.damage)}\n{action.inference_basis}"
        )
    item = selected.payload
    assert isinstance(item, BattleInferredInput)
    if item.is_character_switch:
        return (
            f"推算 QTE 切换 · {item.character_name}\n"
            f"{format_time(projected_time(item.start_us))} · "
            f"时间置信度{item.timing_confidence}\n"
            "头像表示切换结果；当前没有真实键盘槽位证据。"
        )
    return (
        f"推算输入 · {item.character_name} · {item.display_text}\n"
        f"{format_time(projected_time(item.start_us))}—"
        f"{format_time(projected_time(item.end_us))} · 时间置信度{item.timing_confidence}\n"
        + (
            "静态长按程序表明伤害可发生在按住期间；松手边界仍是推算。"
            if item.hold_damage_mode == "during_hold"
            else "静态长按程序表明伤害发生在松手或达到阈值后的输出段。"
            if item.hold_damage_mode == "after_hold"
            else "当前没有真实键鼠事件；不能据此判断重复点击次数。"
        )
    )
