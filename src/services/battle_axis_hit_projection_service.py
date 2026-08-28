# 将持久化逐击行投影为统一主伤害和追击事件。
"""Project raw battle-axis mappings into immutable analysis hits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.battle_report import BattleAnalysisHit
from src.services.battle_target_vital_analysis_service import (
    resolve_battle_target_identity,
)


_REACTION_MARKERS = (
    "创生", "黯星", "浊燃", "浸染", "盈蓄", "失谐", "延滞", "倾陷",
    "reaction", "topple",
)
_WEAVE_MARKERS = ("覆纹", "weave")
_TOPPLE_MARKERS = ("倾陷", "topple", "tenacity")
_MECHANIC_MARKERS = ("ge_boss_05_hitbullet", "敌方飞弹反射")


def _text(value: Any, fallback: str = "") -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def _classification(
    *values: Any,
    ability_name: Any = None,
    gameplay_effect_name: Any = None,
    gameplay_tags: Sequence[str] = (),
    follow_up: bool = False,
) -> str:
    ability = _text(ability_name).casefold()
    effect = _text(gameplay_effect_name).casefold()
    normalized_values = tuple(_text(value) for value in values)
    joined = " ".join(
        (ability, effect, *(value.casefold() for value in normalized_values))
    )
    if any(marker.casefold() in joined for marker in _WEAVE_MARKERS):
        return "weave"
    if any(marker.casefold() in joined for marker in _TOPPLE_MARKERS):
        return "topple"
    if any(marker.casefold() in joined for marker in _MECHANIC_MARKERS):
        return "mechanic"
    if follow_up and any(
        marker.casefold() in " ".join(value.casefold() for value in normalized_values)
        for marker in _REACTION_MARKERS
    ):
        return "reaction"
    if effect.startswith(("buff_reaction_", "ge_actorreaction_")):
        return "reaction"
    normalized_tags = {str(value).casefold() for value in gameplay_tags}
    if "state.damage.dot" in normalized_tags:
        return "dot"
    if "state.damage.attachment" in normalized_tags:
        return "attachment"
    qte_direct = (
        "qte" in ability
        or "qte" in effect
        or any(value.startswith("环合·") for value in normalized_values)
    ) and not ("reaction" in effect and "qte" not in effect)
    if qte_direct:
        return "direct_follow_up" if follow_up else "direct"
    if any(marker.casefold() in joined for marker in _REACTION_MARKERS):
        return "reaction"
    return "direct_follow_up" if follow_up else "direct"


def project_battle_axis_hits(
    rows: Sequence[Mapping[str, Any]],
    *,
    origin_us: int | None = None,
) -> tuple[BattleAnalysisHit, ...]:
    """Split each persisted row without mutating its Core evidence."""

    events: list[BattleAnalysisHit] = []
    for row in rows:
        sequence = int(row.get("sequence_order") or row.get("sequence_text") or 0)
        relative_time_us = int(row.get("relative_time_us") or 0)
        character_id = row.get("character_id")
        raw_character_id = None if character_id is None else int(character_id)
        character_known = bool(
            row.get(
                "character_known",
                raw_character_id is not None and raw_character_id > 0,
            )
        )
        normalized_character_id = (
            raw_character_id
            if character_known and raw_character_id is not None and raw_character_id > 0
            else None
        )
        target_id, target_name = resolve_battle_target_identity(row)
        common = {
            "sequence": sequence,
            "relative_time_us": relative_time_us,
            "character_id": normalized_character_id,
            "character_name": _text(row.get("character_name"), "未知角色"),
            "target_id": target_id,
            "target_name": target_name,
            "direction": _text(row.get("direction"), "unknown"),
            "scope_half": _text(row.get("abyss_half")).casefold(),
            "target_hp_before": row.get("target_hp_before"),
            "target_hp_after": row.get("target_hp_after"),
            "target_max_hp": row.get("target_max_hp"),
            "ability_id": _text(row.get("ability_name")),
            "gameplay_effect_id": _text(row.get("gameplay_effect_name")),
        }
        primary_damage = max(0.0, float(row.get("damage") or 0.0))
        raw_overkill = row.get("overkill_damage")
        overkill_damage = (
            None
            if raw_overkill is None
            else min(primary_damage, max(0.0, float(raw_overkill)))
        )
        overlap_correction = min(
            primary_damage - (overkill_damage or 0.0),
            max(0.0, float(row.get("_calc_damage_overlap_correction") or 0.0)),
        )
        derived_correction_kind = _text(
            row.get("_calc_damage_correction_kind")
        )
        if primary_damage > 0:
            damage_name = _text(
                row.get("damage_display_name"),
                _text(
                    row.get("damage_name"),
                    _text(
                        row.get("gameplay_effect_name"),
                        _text(
                            row.get("damage_component"),
                            _text(row.get("attack_type"), "未识别伤害"),
                        ),
                    ),
                ),
            )
            component = _text(row.get("damage_component"), "unknown")
            attack_type = _text(row.get("attack_type"), "unknown")
            events.append(
                BattleAnalysisHit(
                    event_id=f"{sequence}:primary",
                    skill_name=_text(
                        row.get("ability_display_name"),
                        _text(row.get("ability_name"), damage_name),
                    ),
                    damage_name=damage_name,
                    damage_component=component,
                    attack_type=attack_type,
                    damage_attribute=_text(row.get("damage_attribute"), "unknown"),
                    damage=(
                        primary_damage
                        - (overkill_damage or 0.0)
                        - overlap_correction
                    ),
                    is_follow_up=False,
                    raw_damage=(
                        primary_damage
                        if overkill_damage is not None or overlap_correction > 0.0
                        else None
                    ),
                    overkill_damage=overkill_damage,
                    damage_correction_kind=(
                        derived_correction_kind
                        or (
                            "nte_core_overkill_v3"
                            if overkill_damage is not None
                            else ""
                        )
                    ),
                    damage_correction_confidence=(
                        _text(row.get("_calc_damage_correction_confidence"))
                        or ("高" if overkill_damage is not None else "")
                    ),
                    damage_correction_basis=(
                        _text(row.get("_calc_damage_correction_basis"))
                        or (
                            "nte-core v3 权威 overkill_damage；仅从主伤害扣除，"
                            "追击不扣。"
                            if overkill_damage is not None
                            else ""
                        )
                    ),
                    damage_overlap_correction=overlap_correction,
                    classification=_classification(
                        damage_name,
                        component,
                        attack_type,
                        ability_name=row.get("ability_name"),
                        gameplay_effect_name=row.get("gameplay_effect_name"),
                        gameplay_tags=tuple(row.get("formal_gameplay_tags") or ()),
                    ),
                    **common,
                )
            )
        follow_up_damage = max(0.0, float(row.get("follow_up_damage") or 0.0))
        if follow_up_damage <= 0.0:
            continue
        follow_up_relative_us = relative_time_us
        follow_up_timestamp_us = row.get("follow_up_timestamp_unix_us")
        if follow_up_timestamp_us is None:
            follow_up_timestamp = row.get("follow_up_timestamp_unix")
            if isinstance(follow_up_timestamp, (int, float)):
                follow_up_timestamp_us = round(
                    float(follow_up_timestamp) * 1_000_000
                )
        if origin_us is not None and isinstance(
            follow_up_timestamp_us,
            (int, float),
        ):
            follow_up_relative_us = max(
                0,
                int(follow_up_timestamp_us) - origin_us,
            )
        labels = tuple(row.get("follow_up_labels") or ())
        damage_name = _text(
            row.get("follow_up_damage_display_name"),
            _text(
                row.get("follow_up_damage_name"),
                _text(labels[0] if labels else None, "追加攻击"),
            ),
        )
        component = _text(row.get("follow_up_damage_component"), "follow_up")
        attack_type = _text(row.get("follow_up_attack_type"), "follow_up")
        events.append(
            BattleAnalysisHit(
                event_id=f"{sequence}:follow_up",
                skill_name=_text(
                    row.get("ability_display_name"),
                    _text(row.get("ability_name"), damage_name),
                ),
                damage_name=damage_name,
                damage_component=component,
                attack_type=attack_type,
                damage_attribute=_text(
                    row.get("follow_up_damage_attribute"),
                    _text(row.get("damage_attribute"), "unknown"),
                ),
                damage=follow_up_damage,
                is_follow_up=True,
                overkill_damage=None,
                classification=_classification(
                    damage_name,
                    component,
                    attack_type,
                    *labels,
                    ability_name=row.get("ability_name"),
                    gameplay_effect_name=row.get("gameplay_effect_name"),
                    gameplay_tags=tuple(row.get("formal_gameplay_tags") or ()),
                    follow_up=True,
                ),
                **{
                    **common,
                    "relative_time_us": follow_up_relative_us,
                },
            )
        )
    return tuple(sorted(
        events,
        key=lambda item: (
            item.relative_time_us,
            item.sequence,
            item.is_follow_up,
        ),
    ))
