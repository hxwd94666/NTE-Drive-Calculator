# 统一把已保存的战斗环境配置转换成分析层输入与整场 Buff 区间。
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.domain.battle_report import (
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
    BattleTargetCondition,
)
from src.domain.battle_target import BattleSelectedTargetProfile


def _text(value: object, fallback: str = "") -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def _selected_target_profiles(value: object) -> tuple[BattleSelectedTargetProfile, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result = []
    for row in value:
        if not isinstance(row, Mapping):
            continue
        resistances = row.get("resistances")
        if not isinstance(resistances, Mapping):
            resistances = {}
        static_target_id = _text(row.get("static_target_id"))
        max_hp = row.get("max_hp")
        if not static_target_id or not isinstance(max_hp, (int, float)):
            continue
        result.append(BattleSelectedTargetProfile(
            static_target_id=static_target_id,
            selection_target_id=_text(
                row.get("selection_target_id"), static_target_id
            ),
            target_name=_text(row.get("target_name"), static_target_id),
            monster_class_path=_text(row.get("monster_class_path")),
            monster_count=max(1, int(row.get("monster_count") or 1)),
            max_hp=float(max_hp),
            monster_level=float(row.get("monster_level") or 1.0),
            defense_base=(
                float(row["defense_base"])
                if isinstance(row.get("defense_base"), (int, float))
                else None
            ),
            defense_up=float(row.get("defense_up") or 0.0),
            defense_add=float(row.get("defense_add") or 0.0),
            topple_limit=float(row.get("topple_limit") or 50.0),
            resistances=tuple(sorted(
                (str(key), float(number))
                for key, number in resistances.items()
                if isinstance(number, (int, float))
            )),
            profile_set=_text(row.get("profile_set")),
            pack_id=_text(row.get("pack_id")),
        ))
    return tuple(result)


def resolve_battle_target_condition(
    value: Mapping[str, Any] | BattleTargetCondition | None,
) -> BattleTargetCondition | None:
    if isinstance(value, BattleTargetCondition):
        return value
    if not value or not bool(value.get("confirmed", True)):
        return None
    resistances = value.get("resistances")
    if not isinstance(resistances, Mapping):
        return None
    return BattleTargetCondition(
        target_name=_text(value.get("target_name"), "用户确认目标"),
        enemy_level=float(value.get("enemy_level") or 90.0),
        scene=_text(value.get("scene"), "outer_realm"),
        defense_reduction=float(value.get("defense_reduction") or 0.0),
        vulnerability=float(value.get("vulnerability") or 0.0),
        resistances=tuple(sorted(
            (str(key), float(number))
            for key, number in resistances.items()
            if isinstance(number, (int, float))
        )),
        source_kind=_text(value.get("source_kind"), "user_confirmed"),
        enemy_defense_base=(
            float(value["enemy_defense_base"])
            if isinstance(value.get("enemy_defense_base"), (int, float))
            else None
        ),
        enemy_defense_up=float(value.get("enemy_defense_up") or 0.0),
        enemy_defense_add=float(value.get("enemy_defense_add") or 0.0),
        enemy_topple_limit=float(value.get("enemy_topple_limit") or 50.0),
        environment_kind=_text(value.get("environment_kind"), "manual"),
        environment_ref=_text(value.get("environment_ref")),
        environment_name=_text(value.get("environment_name")),
        selected_target_ids=tuple(
            str(item) for item in (value.get("selected_target_ids") or ())
        ),
        primary_target_id=_text(value.get("primary_target_id")),
        difficulty_id=(
            int(value["difficulty_id"])
            if isinstance(value.get("difficulty_id"), (int, float))
            else None
        ),
        feast_options=tuple(sorted(
            (str(key), str(option_id))
            for key, option_id in (value.get("feast_options") or {}).items()
        )),
        witch_buff_id=_text(value.get("witch_buff_id")),
        witch_buff_name_zh=_text(value.get("witch_buff_name_zh")),
        witch_buff_property_id=_text(value.get("witch_buff_property_id")),
        witch_buff_value=(
            float(value["witch_buff_value"])
            if isinstance(value.get("witch_buff_value"), (int, float))
            else None
        ),
        witch_buff_is_percent=bool(value.get("witch_buff_is_percent")),
        selected_target_profiles=_selected_target_profiles(
            value.get("selected_target_profiles")
        ),
    )


def display_battle_environment_name(condition: BattleTargetCondition) -> str:
    """Return the compact player-facing environment name for a saved condition."""

    saved_name = _text(condition.environment_name)
    if saved_name:
        return saved_name
    kind = _text(condition.environment_kind)
    if condition.environment_ref.startswith("clone|"):
        return "材料 / 养成副本"
    if kind == "outer_realm":
        parts = condition.environment_ref.split("|")
        if len(parts) >= 3 and parts[1].isdigit():
            half = (
                "上下半" if parts[2] == "mixed"
                else "上半" if "FirstHalf" in parts[2]
                else "下半"
            )
            return f"轨外之境第{int(parts[1])}层{half}"
        return "轨外之境"
    prefix = {
        "open_world": "大世界",
        "clone": "材料 / 养成副本",
        "feast": "争锋赏宴",
        "high_risk_commission": "高危委托",
    }.get(kind, "大世界" if condition.scene == "open_world" else "战斗环境")
    target_name = _text(condition.target_name)
    if not target_name or "/" in target_name or "\\" in target_name:
        return prefix
    return target_name if target_name.startswith(prefix) else f"{prefix} · {target_name}"


def battle_witch_buff_interval(
    condition: BattleTargetCondition | None,
    battle_end_us: int,
) -> BattleInferredBuffInterval | None:
    if (
        condition is None
        or not condition.witch_buff_id
        or not condition.witch_buff_property_id
        or condition.witch_buff_value is None
    ):
        return None
    return BattleInferredBuffInterval(
        interval_id=f"external:witch:{condition.witch_buff_id}",
        buff_asset_path=f"user-condition:{condition.witch_buff_id}",
        buff_name=condition.witch_buff_name_zh or condition.witch_buff_id,
        source_effect_definition_id=condition.witch_buff_id,
        source_kind="user_confirmed_external_buff",
        source_character_id=0,
        source_character_name="魔女赐福",
        target_scope="team",
        start_us=0,
        end_us=battle_end_us,
        stacks=1,
        duration_policy="battle_condition",
        state_confidence="高",
        value_confidence="高",
        inference_basis="用户为本场战报选择的魔女赐福；不会读取今日状态覆盖历史。",
        trigger_event_type="USER_CONFIRMED_BATTLE_CONDITION",
        evidence_action_ids=(),
        evidence_event_ids=(),
        modifiers=(BattleBuffModifierEvidence(
            property_id=condition.witch_buff_property_id,
            modifier_operation="EGameplayModOp::Additive",
            magnitude_kind="static_catalog",
            magnitude_value=condition.witch_buff_value,
            calculation_asset_path="",
            value_confidence="高",
        ),),
    )
