# 怪物与玩法资料库的 Qt 无关正式术语投影。
"""怪物与玩法资料库的 Qt 无关正式术语投影。"""

from __future__ import annotations

from typing import Any

from src.domain.localization.catalog import localized_text
from src.services.static_catalog.common.terminology import (
    StaticCatalogTerminologyService,
)


NAME_UNAVAILABLE = "名称暂未提供"


def display_catalog_scalar(value: object) -> str:
    """格式化标量只改变展示，不改变其正式身份。"""

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
    """以精确伤害抗性 key 查询公共术语，不按相似名称匹配。"""

    return _display_term(terminology_service, "damage_resistance", stable_key)


def display_fight_stage(
    terminology_service: StaticCatalogTerminologyService,
    stable_key: object,
) -> str:
    """以完整正式战斗阶段枚举查询公共显示术语。"""

    return _display_term(terminology_service, "outer_realm_fight_stage", stable_key)


def display_buff_option(
    terminology_service: StaticCatalogTerminologyService,
    option: dict[str, Any],
) -> str:
    """仅投影玩家可读数值，类别名称仍由正式本地化记录决定。"""

    score = display_catalog_scalar(option.get("score"))
    if option.get("effect_kind") == "mechanism_goal":
        return localized_text(None, 'auto.services.static_catalog.monsters.display.display_buff_option.fa90978fd', '{value} · 目标分值 {score}（未判定完成）', value=f"{option.get('description_zh') or '目标说明暂未提供'}", score=f'{score}')
    if str(option.get("effect_kind") or "") == "time_limit":
        seconds = display_catalog_scalar(option.get("limit_seconds"))
        return localized_text(None, 'auto.services.static_catalog.monsters.display.display_buff_option.f1eb67b88', '限时 {seconds} 秒 · 额外得分 {score}', seconds=f'{seconds}', score=f'{score}')
    amount = display_catalog_scalar(option.get("add_value"))
    damage_type = str(option.get("damage_type") or "").strip()
    prefix = (
        f"{display_damage_type(terminology_service, damage_type)} · "
        if damage_type
        else ""
    )
    return localized_text(None, 'auto.services.static_catalog.monsters.display.display_buff_option.fd5900904', '{prefix}数值 {amount} · 额外得分 {score}', prefix=f'{prefix}', amount=f'{amount}', score=f'{score}')
