# 争锋赏宴的正式选择目录与敌方画像修正。
"""争锋赏宴的正式选择目录与敌方画像修正。"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from src.domain.localization.catalog import localized_text
from src.services.static_catalog.monsters.display import NAME_UNAVAILABLE
from src.services.static_catalog.monsters.models import (
    CatalogValue,
    FeastDifficultyChoice,
    FeastOptionChoice,
    FeastOptionGroup,
    FeastSetup,
    FeastTargetChoice,
)


def build_feast_setup(
    row: dict[str, Any],
    *,
    period_id: str,
    period_label: str,
    period_state: str,
    schedule_label: str,
    challenge_ordinal: int,
    condition_note: str = "",
    project_option: Callable[[str, dict[str, Any]], CatalogValue],
) -> FeastSetup:
    targets = tuple(FeastTargetChoice(
        monster_id=str(target["boss_monster_id"]), display_name=_name(target.get("name_zh")),
        difficulties=tuple(FeastDifficultyChoice(int(item["difficulty_id"]), _name(item.get("name_zh")),
                                                int(item["monster_level"]), float(item.get("score_rate") or 0))
                           for item in target.get("difficulties", ())),
    ) for target in row.get("targets", ()))
    # 全局难度只选一次；不同目标的等级不合并成一个默认等级。
    unique = {}
    for item in row.get("difficulties", ()):
        identity = int(item["difficulty_id"])
        if identity not in unique:
            unique[identity] = dict(item)
        elif unique[identity]["monster_level"] != item["monster_level"]:
            unique[identity]["monster_level"] = None
    difficulties = tuple(
        FeastDifficultyChoice(
            difficulty_id=int(item["difficulty_id"]),
            display_name=_name(item.get("name_zh")),
            monster_level=int(item["monster_level"]) if item.get("monster_level") is not None else None,
            score_rate=float(item.get("score_rate") or 0.0),
        )
        for item in unique.values()
    )
    grouped: dict[tuple[int, str], list[FeastOptionChoice]] = defaultdict(list)
    for option in row.get("options", ()):
        category = _name(option.get("category_name_zh"))
        value = project_option(category, option)
        display_category = value.display_label or category
        display_name = value.display_value or NAME_UNAVAILABLE
        category_prefix = display_category.removesuffix("提升")
        if display_name.startswith(f"{category_prefix} · "):
            display_name = display_name.removeprefix(f"{category_prefix} · ")
        grouped[(int(option["category_ordinal"]), display_category)].append(
            FeastOptionChoice(
                option_id=str(option["option_id"]),
                display_name=display_name,
            )
        )
    groups = tuple(
        FeastOptionGroup(str(ordinal), category, tuple(options))
        for (ordinal, category), options in sorted(grouped.items())
    )
    default = max(
        difficulties,
        key=lambda item: (item.score_rate, item.difficulty_id),
    )
    return FeastSetup(
        period_id=period_id,
        period_label=period_label,
        period_state=period_state,
        schedule_label=schedule_label,
        stage_id=str(row["stage_id"]),
        title=_name(row.get("name_zh")),
        boss_name=localized_text(None, 'auto.services.static_catalog.monsters.feast.build_feast_setup.fc6a2887f', '多目标（{count}）', count=f'{len(targets)}') if len(targets) > 1 else _name(
            next(
                (
                    item.get("boss_name_zh")
                    for item in row.get("difficulties", ())
                    if item.get("boss_name_zh")
                ),
                None,
            )
        ),
        boss_monster_id=str(row.get("boss_monster_id") or ""),
        challenge_ordinal=challenge_ordinal,
        default_difficulty_id=default.difficulty_id,
        difficulties=difficulties,
        option_groups=groups,
        condition_note="；".join(filter(None, (condition_note, feast_mechanism_note(row)))),
        targets=targets,
        mechanism_score_limit=row.get("mechanism_score_limit"),
    )


def selected_feast_options(
    available: list[dict[str, Any]], selected_ids: tuple[str, ...],
) -> tuple[dict[str, Any], ...]:
    requested = tuple(dict.fromkeys(str(value) for value in selected_ids if value))
    by_id = {str(option["option_id"]): option for option in available}
    unknown = tuple(value for value in requested if value not in by_id)
    if unknown:
        raise ValueError("争锋赏宴选择不属于当前活动期挑战")
    selected = tuple(by_id[value] for value in requested)
    categories = [int(option["category_ordinal"]) for option in selected]
    if len(categories) != len(set(categories)):
        raise ValueError("争锋赏宴每类条件只能选择一项")
    return selected


def feast_mechanism_note(row: dict[str, Any]) -> str:
    if not any(option.get("effect_kind") in {"mechanism_goal", "mechanism"} for option in row.get("options", ())):
        return ""
    cap = row.get("mechanism_score_limit")
    limit = localized_text(None, 'auto.services.static_catalog.monsters.feast.feast_mechanism_note.fa9ef0233', '来源声明挑战目标分值上限 {cap}；', cap=f'{cap}') if cap is not None else "挑战目标分值上限未知；"
    return limit + "选择仅记录场景条件，完成状态与结算得分未判定；条件效果不计入常驻画像。"


def apply_feast_options(
    profile: dict[str, Any] | None,
    selected: tuple[dict[str, Any], ...],
) -> dict[str, Any] | None:
    if profile is None:
        return None
    projected = dict(profile)
    projected["resistances"] = [
        dict(resistance) for resistance in profile.get("resistances", ())
    ]
    for option in selected:
        amount = float(option.get("add_value") or 0.0)
        effect_kind = str(option.get("effect_kind") or "")
        if effect_kind == "health_up":
            projected["health_up"] = float(projected.get("health_up") or 0.0) + amount
        elif effect_kind == "resistance_up" and option.get("damage_type"):
            damage_type = str(option["damage_type"])
            for resistance in projected["resistances"]:
                if str(resistance.get("damage_type")) == damage_type:
                    resistance["resistance_base"] = (
                        float(resistance.get("resistance_base") or 0.0) + amount
                    )
                    break
    return projected


def _name(value: object) -> str:
    text = str(value or "").strip()
    return text if text and "\ufffd" not in text else NAME_UNAVAILABLE
