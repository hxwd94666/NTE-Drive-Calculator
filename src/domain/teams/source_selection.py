# 仅为未绑定成员解析唯一已保存槽位；读取不落盘，明确绑定失效时不换用其他槽位。
from dataclasses import replace

from src.domain.teams.overview import SourceMode


def with_single_slot_defaults(config, slots):
    """slots 已限定本队成员；空槽仍计入候选数，避免多槽时擅自替用户选择。"""
    if config.mode != SourceMode.CALC:
        return config
    by_character = {}
    for slot in slots:
        if not slot.get("is_archived"):
            by_character.setdefault(slot["character_id"], []).append(slot)
    bindings = dict(config.bindings)
    for key, own in by_character.items():
        if key not in bindings and len(own) == 1 and own[0].get("current_plan_id") is not None:
            bindings[key] = own[0]["slot_id"]
    return replace(config, bindings=tuple(sorted(bindings.items())))
