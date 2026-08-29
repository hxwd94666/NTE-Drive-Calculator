# 仅依据显式静态语义解析 Buff 目标范围。
"""Resolve Buff target scopes only from explicit static semantics."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class BattleBuffTargetScopeService:
    @staticmethod
    def ability_input_kind(input_id: Any, ability_id: str) -> str:
        value = str(input_id or "")
        if "UltraSkill" in value:
            return "Q"
        if "GSkill" in value:
            return "G"
        if value.endswith("_Skill"):
            return "E"
        if "Melee" in value:
            return "A"
        if "QTE" in ability_id:
            return "QTE"
        if "PerfectEvade" in ability_id:
            return "PERFECT_EVADE"
        return "UNKNOWN"

    @staticmethod
    def for_trigger(
        trigger: Mapping[str, Any],
        source_definition: Mapping[str, Any] | None,
    ) -> str:
        event_type = str(trigger.get("event_type") or "").casefold()
        if "all_player" in event_type:
            return "team"
        description = str((source_definition or {}).get("description_zh") or "")
        if "全队角色获得" in description or "全队角色提升" in description:
            return "team"
        if bool(trigger.get("target_trigger")):
            return "target"
        if bool(trigger.get("by_self")):
            return "self"
        return "unknown"

    @staticmethod
    def for_skill_binding(binding: Mapping[str, Any]) -> str:
        if str(binding.get("binding_kind") or "").casefold() == "passive_buff":
            return "self"
        target_type = str(binding.get("target_type_asset_path") or "").casefold()
        if any(marker in target_type for marker in (
            "all_player", "allplayer", "teammate", "team", "ally", "friend",
        )):
            return "team"
        if any(marker in target_type for marker in ("self", "owner", "source")):
            return "self"
        if any(marker in target_type for marker in ("enemy", "monster", "hostile")):
            return "target"
        return "unknown"
