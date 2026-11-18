# 构建统一时间轴点击和悬停共享的证据详情。
"""Qt-free tooltip projection for timeline selections."""

from __future__ import annotations

from collections.abc import Callable

from src.domain.localization.catalog import localized_text
from src.domain.battle_report.timeline.events import (
    BattleAnalysisHit, BattleInferredAction, BattleInferredInput, BattleTimelineDamageGroup,
)
from src.features.battle_report.timeline.layout import (
    TimelineSelection,
    format_damage,
    format_time,
)
from src.services.official_role.skill_names import (
    preferred_battle_damage_name,
    render_battle_event_type,
)


def build_timeline_tooltip(
    selected: TimelineSelection,
    *,
    projected_time: Callable[[int], int],
    hit_heading: str = "正式逐击", text_catalog=None,
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
            localized_text(text_catalog, 'auto.features.battle_report.timeline.tooltip.build_timeline_tooltip.f08cf0324', '\n来源技能：{skill_name}', skill_name=f'{hit.skill_name}')
            if hit.skill_name not in {"", damage_name, "未识别技能"}
            else ""
        )
        return (
            localized_text(text_catalog, 'auto.features.battle_report.timeline.tooltip.build_timeline_tooltip.f85cc4628', '{hit_heading} · {value} · {character_name}\n{damage_name}{source}\n逐击 ID：{event_id}\n{value_2} · {target_name} · {value_3}', hit_heading=f'{hit_heading}', value=f'{format_time(projected_time(hit.relative_time_us))}', character_name=f'{hit.character_name}', damage_name=f'{damage_name}', source=f'{source}', event_id=f'{hit.event_id}', value_2=f'{render_battle_event_type(hit.classification, hit.attack_type, hit.damage_attribute)}', target_name=f'{hit.target_name}', value_3=f'{format_damage(hit.damage)}')
        )
    if selected.kind == "damage_group":
        group = selected.payload
        assert isinstance(group, BattleTimelineDamageGroup)
        source = (
            localized_text(text_catalog, 'auto.features.battle_report.timeline.tooltip.build_timeline_tooltip.f8d129b24', '\n来源技能：{source_skill_name}', source_skill_name=f'{group.source_skill_name}')
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
            localized_text(text_catalog, 'auto.features.battle_report.timeline.tooltip.build_timeline_tooltip.f01d8e0dd', '{title} · {character_name} · {channel_label}\n{damage_name}{source}\n{value}—{value_2} · {hits} 次 · {value_3}{details}\n{footer}', title=f'{title}', character_name=f'{group.character_name}', channel_label=f'{group.channel_label}', damage_name=f'{group.damage_name}', source=f'{source}', value=f'{format_time(projected_time(group.start_us))}', value_2=f'{format_time(projected_time(group.end_us))}', hits=f'{group.hits}', value_3=f'{format_damage(group.damage)}', details=f'{details}', footer=f'{footer}')
        )
    if selected.kind == "action":
        action = selected.payload
        assert isinstance(action, BattleInferredAction)
        return (
            localized_text(text_catalog, 'auto.features.battle_report.timeline.tooltip.build_timeline_tooltip.f98841b15', '推算动作 · {character_name} · 身份置信度{identity_confidence} / 时间置信度{timing_confidence}\n{input_sequence} · {action_name}\n{value}—{value_2} · {hits} 击 · {value_3}\n{inference_basis}', character_name=f'{action.character_name}', identity_confidence=f'{action.identity_confidence}', timing_confidence=f'{action.timing_confidence}', input_sequence=f'{action.input_sequence}', action_name=f'{action.action_name}', value=f'{format_time(projected_time(action.start_us))}', value_2=f'{format_time(projected_time(action.end_us))}', hits=f'{action.hits}', value_3=f'{format_damage(action.damage)}', inference_basis=f'{action.inference_basis}')
        )
    item = selected.payload
    assert isinstance(item, BattleInferredInput)
    if item.is_character_switch:
        return (
            localized_text(text_catalog, 'auto.features.battle_report.timeline.tooltip.build_timeline_tooltip.fc3b10624', '角色切换 · {character_name}\n{value} · 时间置信度{timing_confidence}\n头像表示切换结果；当前没有真实键盘槽位证据。', character_name=f'{item.character_name}', value=f'{format_time(projected_time(item.start_us))}', timing_confidence=f'{item.timing_confidence}')
        )
    return (
        localized_text(text_catalog, 'auto.features.battle_report.timeline.tooltip.build_timeline_tooltip.f5196df9c', '推算输入 · {character_name} · {display_text}\n{value}—{value_2} · 时间置信度{timing_confidence}\n', character_name=f'{item.character_name}', display_text=f'{item.display_text}', value=f'{format_time(projected_time(item.start_us))}', value_2=f'{format_time(projected_time(item.end_us))}', timing_confidence=f'{item.timing_confidence}')
        + (
            "静态长按程序表明伤害可发生在按住期间；松手边界仍是推算。"
            if item.hold_damage_mode == "during_hold"
            else "静态长按程序表明伤害发生在松手或达到阈值后的输出段。"
            if item.hold_damage_mode == "after_hold"
            else "当前没有真实键鼠事件；不能据此判断重复点击次数。"
        )
    )
