# 为特殊逐击保留纯 Python 数值基准，并构造不含账号状态的原生输入。
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import wraps
from math import isfinite
from struct import pack, unpack
from types import GeneratorType
from typing import Any

from src.services.damage_calculation_service import (
    DamageScene, EnemyDefenseProfileInput, calculate_defense_multiplier,
    calculate_enemy_defense, calculate_enemy_defense_from_profile,
    calculate_resistance_multiplier, calculate_ring_strength_multiplier,
    calculate_weave_strength_multiplier, calculate_enemy_topple_limit_multiplier,
)
from src.services.battle_report.replay.support import settle_replay_damage

SpecialNumeric = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class PreparedSpecialReplay:
    operation: str
    inputs: dict
    continuation: GeneratorType

    def render(self, numbers: dict):
        try:
            self.continuation.send(numbers)
        except StopIteration as completed:
            return completed.value
        raise ValueError("A special replay must have exactly one numeric settlement")


def numeric_replay(replay):
    """Suspend at the one arithmetic boundary, retaining prepared evidence once."""
    @wraps(replay)
    def wrapped(*args, prepare_numeric=False, numeric=python_special_numeric, **kwargs):
        result = replay(*args, **kwargs)
        if not isinstance(result, GeneratorType):
            return result
        try:
            operation, inputs = next(result)
        except StopIteration as completed:
            return completed.value
        prepared = PreparedSpecialReplay(operation, inputs, result)
        return prepared if prepare_numeric else prepared.render(numeric(operation, inputs))
    return wrapped


def mitigation_input(condition, level: float, values: Mapping[str, float],
                     projection, attribute: str, *, clamp_defense: bool) -> dict:
    property_names = {f"DamageResist{attribute.title()}Base",
                      f"DamageResist{attribute.title()}Add"}
    return {
        "character_level": level,
        "enemy_level": condition.enemy_level,
        "scene": condition.scene,
        "defense_base": condition.enemy_defense_base,
        "defense_up": condition.enemy_defense_up,
        "defense_add": condition.enemy_defense_add,
        "defense_penetration": float(values.get("DefIgnore", 0.0)),
        "clamp_defense": clamp_defense,
        "defense_reduction": condition.defense_reduction,
        "base_resistance": dict(condition.resistances).get(attribute, 0.20),
        "resistance_additions": [
            row.additive_value for row in projection.modifiers
            if row.target_scope == "target" and row.property_id in property_names
        ],
        "resistance_penetration": float(values.get(f"DamagePenetrate{attribute.title()}", 0.0)),
        "vulnerability": condition.vulnerability,
    }


def _mitigation(values: dict, *, defense_enabled: bool = True) -> dict:
    penetration = values["defense_penetration"]
    if values["clamp_defense"]:
        penetration = max(0.0, penetration)
    defense = 1.0
    if defense_enabled:
        if values["defense_base"] is None:
            enemy = calculate_enemy_defense(
                values["enemy_level"], penetration, values["defense_reduction"],
                DamageScene.OPEN_WORLD if values["scene"] == "open_world"
                else DamageScene.OUTER_REALM,
            )
        else:
            enemy = calculate_enemy_defense_from_profile(
                EnemyDefenseProfileInput(values["defense_base"], values["defense_up"],
                                         values["defense_add"]),
                penetration, values["defense_reduction"],
            )
        defense = calculate_defense_multiplier(values["character_level"], enemy)
    resistance = calculate_resistance_multiplier(
        values["base_resistance"] + sum(values["resistance_additions"])
        - values["resistance_penetration"]
    )
    return {"defense": defense, "resistance": resistance,
            "vulnerability": 1.0 + values["vulnerability"]}


def _settlement(observed: float, noncritical: float, critical: float | None = None,
                *, corrected_direct: bool = False) -> dict:
    def error(value):
        return None if observed <= 0.0 else (value - observed) / observed * 100.0
    is_critical = critical is not None and abs(error(critical) or 0.0) < abs(error(noncritical) or 0.0)
    selected = critical if is_critical else noncritical
    rate = 0.5 if critical is not None else 0.0
    expected = (noncritical * (1.0 - rate) + critical * rate
                if critical is not None else noncritical)
    signed = error(selected)
    return {
        "noncritical": noncritical, "critical": critical, "selected": selected,
        "critical_state": ("critical" if is_critical else "non_critical")
        if critical is not None else "not_applicable",
        "critical_rate": rate, "expected": expected, "signed_error": signed,
        "absolute_error": None if signed is None else abs(signed),
        "corrected_expected": (observed if corrected_direct else expected * observed / selected)
        if selected > 0.0 else None,
    }


def python_special_numeric(operation: str, values: dict) -> dict:
    """Offline diagnostic arithmetic; native errors never select this as a fallback."""
    if operation == "special_weave_v1":
        def number(name):
            value = values.get(name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
                raise ValueError("invalid_special_input")
            return value

        strength = max(0.0, number("ring_strength"))
        base, extension = number("base_ratio"), number("extra_ratio")
        source, observed = number("source_damage"), number("observed")
        # 当前正式模型逐区转单精度；基础比例与灵可扩展独立保留，最终系数再转一次。
        # 十进制等价式仅供展示，先算 (1+C)*K-1 会在整数边界错误少结算一点。
        def f32(value):
            try:
                result = unpack("<f", pack("<f", value))[0]
            except OverflowError as error:
                raise ValueError("non_finite_special_result") from error
            if not isfinite(result):
                raise ValueError("non_finite_special_result")
            return result

        # 只有字段缺失才按强度求 R；显式 R=0、F=0 都是输入事实，不能用 or 默认值覆盖。
        multiplier = f32(number("reaction_multiplier") if "reaction_multiplier" in values
                         else calculate_weave_strength_multiplier(strength))
        final = f32(number("final_multiplier"))
        ratio = f32(base) + f32(extension)
        addition = min(100.0, max(0.0, multiplier - 1.0))
        followup = f32((ratio * multiplier + addition) * final)
        raw = source * followup
        if not isfinite(raw):
            raise ValueError("non_finite_special_result")
        predicted = settle_replay_damage(raw)
        result = {**_settlement(observed, predicted, corrected_direct=True),
                "ring_strength": strength, "strength_multiplier": multiplier,
                "base_ratio": base, "extra_ratio": extension, "copy_ratio": ratio,
                "strength_addition": addition, "final_multiplier": final,
                "followup_multiplier": followup, "unrounded_damage": raw}
        if any(isinstance(value, float) and not isfinite(value) for value in result.values()):
            raise ValueError("non_finite_special_result")
        return result
    if operation in {"special_reaction_v1", "special_nova_v1"}:
        nova = operation == "special_nova_v1"
        result = _mitigation(values["mitigation"], defense_enabled=not nova)
        strength = max(0.0, values["ring_strength"])
        multiplier = calculate_ring_strength_multiplier(strength)
        scorch = values.get("scorch", False)
        stack = values["state_multiplier"] if scorch else 1.0
        final = max(1.0, values["dot_final_multiplier"]) if scorch else 1.0
        if nova:
            raw = values["level_multiplier"] * multiplier * result["resistance"] * result["vulnerability"]
        else:
            raw = (values["level_multiplier"] * stack * multiplier * result["defense"]
                   * result["resistance"] * result["vulnerability"] * final)
        crit_damage = max(0.0, values.get("crit_damage", 0.50))
        critical = settle_replay_damage(raw * (1.0 + crit_damage)) if scorch else None
        return {**result, **_settlement(values["observed"], settle_replay_damage(raw), critical,
                                      corrected_direct=nova),
                "ring_strength": strength, "ring_multiplier": multiplier,
                "stack_multiplier": stack, "dot_final_multiplier": final,
                "crit_damage": crit_damage}
    if operation == "special_topple_v1":
        limit = values["enemy_topple_limit"]
        target = 25.0 if values["feast"] and limit >= 70.0 else calculate_enemy_topple_limit_multiplier(limit)
        cells = []
        for cell in values["cells"]:
            strength = max(0.0, cell["strength_base"]) * (1.0 + cell["strength_up"]) + cell["strength_add"]
            if cell["level_multiplier"] < 0 or strength < 0 or limit < 0:
                raise ValueError("Invalid topple numeric input")
            mitigation = _mitigation(cell["mitigation"])
            strength_multiplier = 1.0 + strength / 300.0 + cell["damage_up"]
            damage = (cell["level_multiplier"] * strength_multiplier * target
                      * mitigation["defense"] * mitigation["resistance"])
            cells.append({**mitigation, "strength": strength, "damage": damage})
        predicted = settle_replay_damage(sum(cell["damage"] for cell in cells))
        return {**_settlement(values["observed"], predicted, corrected_direct=True),
                "target_multiplier": target, "cells": cells}
    raise ValueError("Unsupported special numeric operation")
