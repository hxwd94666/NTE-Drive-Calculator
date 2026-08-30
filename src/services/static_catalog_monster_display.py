# 怪物与玩法资料库的 Qt 无关正式术语投影。
"""Player-facing monster terms derived from stable release enum identities."""

from __future__ import annotations

from typing import Any

from src.services.static_catalog_terminology_service import (
    StaticCatalogTerminologyService,
)


NAME_UNAVAILABLE = "名称暂未提供"


def display_catalog_scalar(value: object) -> str:
    """Format a scalar without changing its stable identity."""

    if value is None or value == "":
        return "不可用"
    if isinstance(value, float):
        return f"{value:g}"
    if isinstance(value, bool):
        return "是" if value else "否"
    return str(value)


def _display_term(
    terminology_service: StaticCatalogTerminologyService,
    entity_kind: str,
    stable_key: object,
) -> str:
    identity = str(stable_key or "").strip()
    if not identity:
        return NAME_UNAVAILABLE
    term = terminology_service.resolve(entity_kind, identity)
    return (
        str(term.display_name)
        if term.name_available and term.display_name
        else NAME_UNAVAILABLE
    )


def display_damage_type(
    terminology_service: StaticCatalogTerminologyService,
    stable_key: object,
) -> str:
    """Resolve one exact damage-resistance stable key from the central catalog."""

    return _display_term(terminology_service, "damage_resistance", stable_key)


def display_fight_stage(
    terminology_service: StaticCatalogTerminologyService,
    stable_key: object,
) -> str:
    """Resolve one full outer-realm fight-stage enum from central UI terms."""

    return _display_term(terminology_service, "outer_realm_fight_stage", stable_key)


def display_buff_option(
    terminology_service: StaticCatalogTerminologyService,
    option: dict[str, Any],
) -> str:
    """Project player numbers while the formal localized category owns its name."""

    score = display_catalog_scalar(option.get("score"))
    if str(option.get("effect_kind") or "") == "time_limit":
        seconds = display_catalog_scalar(option.get("limit_seconds"))
        return f"限时 {seconds} 秒 · 额外得分 {score}"
    amount = display_catalog_scalar(option.get("add_value"))
    damage_type = str(option.get("damage_type") or "").strip()
    prefix = (
        f"{display_damage_type(terminology_service, damage_type)} · "
        if damage_type
        else ""
    )
    return f"{prefix}数值 {amount} · 额外得分 {score}"
