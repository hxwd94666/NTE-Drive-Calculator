# 将正式静态目录加载为已冻结敌方画像的可重算环境候选。
"""Static encounter catalog loading kept separate from inference orchestration."""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any

from src.domain.localization.catalog import localized_text
from src.domain.battle_report.targets.encounter import BattleEncounterCandidate, BattleEncounterTargetPreset
from src.services.battle_report.targets.observation import observed_number
from src.services.battle_report.targets.feast_profiles import feast_target_profiles
from src.services.battle_report.environment.outer_realm_period import period_label_for_config
from src.services.battle_report.targets.candidate_graph import normalized_monster_key


_INFERRED_CLONE_CATEGORIES = {
    "经验及甲硬币",
    "异能升级材料",
    "弧盘突破材料",
    "空幕",
}


def _final_hp(row: Mapping[str, Any]) -> float | None:
    base = observed_number(row.get("health_base"))
    if base is None or base <= 0.0:
        return None
    return round(
        base * (1.0 + (observed_number(row.get("health_up")) or 0.0))
        + (observed_number(row.get("health_add")) or 0.0),
        3,
    )


def _numeric_suffix(value: object) -> int:
    match = re.search(r"(\d+)$", str(value or ""))
    return -1 if match is None else int(match.group(1))


def _resistances(value: object) -> tuple[tuple[str, float], ...]:
    if not isinstance(value, Mapping):
        return ()
    return tuple(sorted(
        (
            str(key),
            float(item.get("resistance_base") or 0.0)
            if isinstance(item, Mapping)
            else float(item or 0.0),
        )
        for key, item in value.items()
    ))


def _target_preset(
    row: Mapping[str, Any],
    *,
    target_id: object,
    target_name: object,
    monster_class_path: object,
    monster_count: object = 1,
) -> BattleEncounterTargetPreset | None:
    max_hp = _final_hp(row)
    if max_hp is None:
        return None
    return BattleEncounterTargetPreset(
        target_id=str(target_id or monster_class_path or "").strip(),
        target_name=str(
            target_name or target_id or monster_class_path or "未知目标"
        ).strip(),
        monster_class_path=str(monster_class_path or "").strip(),
        monster_count=max(1, int(monster_count or 1)),
        max_hp=max_hp,
        monster_level=float(row.get("monster_level") or 1.0),
        profile_set=str(row.get("profile_set") or ""),
        pack_id=str(row.get("pack_id") or ""),
        defense_base=observed_number(row.get("defense_base")),
        defense_up=observed_number(row.get("defense_up")) or 0.0,
        defense_add=observed_number(row.get("defense_add")) or 0.0,
        topple_limit=observed_number(row.get("topple_limit")) or 50.0,
        resistances=_resistances(row.get("resistances")),
    )


def world_boss_candidates(
    static_dao: Any,
    *,
    target_preset: Callable[..., Any],
    candidate_factory: Callable[..., Any],
) -> tuple[Any, ...]:
    result = []
    for row in static_dao.list_world_boss_target_fingerprint_rows():
        target = target_preset(
            row,
            target_id=row.get("target_id"),
            target_name=row.get("name_zh"),
            monster_class_path=row.get("monster_template_name"),
        )
        if target is None:
            continue
        level = int(row.get("monster_level") or 0)
        result.append(candidate_factory(
            environment_kind="open_world",
            environment_ref=f"world_boss|{target.target_id}|{level}",
            environment_name=localized_text(None, 'auto.services.battle_report.targets.encounter.catalog.world_boss_candidates.f357e3dd7', '异象追猎 · {target_name} · Lv.{level}', target_name=f'{target.target_name}', level=f'{level}'),
            scope_half="",
            outer_realm_floor=None,
            difficulty_id=None,
            feast_options=(),
            targets=(target,),
        ))
    return tuple(result)


class BattleEncounterCatalogService:
    """Load supported automatic candidates in reproducible formal catalog order."""

    @classmethod
    def load(cls, static_dao: Any) -> tuple[BattleEncounterCandidate, ...]:
        names = {
            normalized_monster_key(row.get("monster_manual_id")): str(
                row.get("name_zh") or ""
            )
            for row in static_dao.list_monster_display_names()
        }
        raw = (
            *cls._outer_candidates(static_dao, names),
            *cls._high_risk_candidates(static_dao),
            *cls._clone_candidates(static_dao),
            *cls._feast_candidates(static_dao),
            *cls._world_boss_candidates(static_dao),
        )
        ordered = tuple(
            replace(candidate, catalog_order=index)
            for index, candidate in enumerate(raw)
        )
        return tuple(cls._hydrate_candidate(static_dao, row) for row in ordered)

    @staticmethod
    def filter_hard_context(
        candidates: Sequence[BattleEncounterCandidate],
        *,
        combat_context_kind: str,
        floor: int | None,
        scope_half: str,
    ) -> tuple[BattleEncounterCandidate, ...]:
        context = str(combat_context_kind or "").strip().casefold()
        result = []
        for candidate in candidates:
            if context == "abyss" and candidate.environment_kind != "outer_realm":
                continue
            if context == "non_abyss" and candidate.environment_kind == "outer_realm":
                continue
            if (
                candidate.environment_kind == "outer_realm"
                and floor is not None
                and candidate.outer_realm_floor != floor
            ):
                continue
            if (
                candidate.environment_kind == "outer_realm"
                and scope_half
                and candidate.scope_half != scope_half
            ):
                continue
            result.append(candidate)
        return tuple(result)

    @staticmethod
    def _high_risk_candidates(static_dao: Any) -> tuple[BattleEncounterCandidate, ...]:
        groups: dict[tuple[str, int], list[Mapping[str, Any]]] = defaultdict(list)
        for row in static_dao.list_high_risk_commission_fingerprint_rows():
            groups[(
                str(row.get("commission_id") or ""),
                int(row.get("difficulty_id") or 0),
            )].append(row)
        result = []
        for commission_id, difficulty_id in sorted(groups):
            rows = sorted(
                groups[(commission_id, difficulty_id)],
                key=lambda row: int(row.get("member_ordinal") or 0),
            )
            targets = tuple(
                target
                for row in rows
                if (target := _target_preset(
                    row,
                    target_id=row.get("monster_template_name"),
                    target_name=row.get("name_zh"),
                    monster_class_path=row.get("monster_class_path"),
                    monster_count=row.get("monster_count"),
                )) is not None
            )
            if targets:
                result.append(BattleEncounterCandidate(
                    environment_kind="high_risk_commission",
                    environment_ref=(
                        f"adv_vision|{commission_id}|{difficulty_id}"
                    ),
                    environment_name=(
                        localized_text(None, 'auto.services.battle_report.targets.encounter.catalog.high_risk_candidates.f53e16c71', '高危委托 · {value} · 难度 {difficulty_id}', value=f"{rows[0].get('name_zh') or commission_id}", difficulty_id=f'{difficulty_id}')
                    ),
                    scope_half="",
                    outer_realm_floor=None,
                    difficulty_id=difficulty_id,
                    feast_options=(),
                    targets=targets,
                ))
        return tuple(result)

    @staticmethod
    def _outer_candidates(
        static_dao: Any,
        names: Mapping[str, str],
    ) -> tuple[BattleEncounterCandidate, ...]:
        configs = tuple(static_dao.list_outer_realm_configs()[:2])
        config_details = {
            str(row.get("level_config_id") or ""): row for row in configs
        }
        config_rank = {
            str(row.get("level_config_id") or ""): index
            for index, row in enumerate(configs)
        }
        groups: dict[tuple[str, int, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in static_dao.list_outer_realm_target_presets():
            config_id = str(row.get("level_config_id") or "")
            if config_id not in config_rank:
                continue
            groups[(
                config_id,
                int(row.get("level_id") or 0),
                str(row.get("fight_stage") or ""),
            )].append(row)
        result = []
        for config_id, floor, stage in sorted(
            groups,
            key=lambda key: (
                config_rank[key[0]],
                key[1],
                0 if "FirstHalf" in key[2] else 1,
                key[0],
                key[2],
            ),
        ):
            rows = sorted(
                groups[(config_id, floor, stage)],
                key=lambda row: (
                    str(row.get("monster_class_path") or "").casefold(),
                    str(row.get("monster_class_path") or ""),
                ),
            )
            targets = tuple(
                target
                for row in rows
                if (target := _target_preset(
                    row,
                    target_id=row.get("monster_class_path"),
                    target_name=(
                        row.get("monster_name_zh")
                        or names.get(normalized_monster_key(row.get("monster_class_path")))
                        or row.get("monster_class_path")
                    ),
                    monster_class_path=row.get("monster_class_path"),
                    monster_count=row.get("monster_count"),
                )) is not None
            )
            half = "upper" if "FirstHalf" in stage else "lower"
            if targets:
                result.append(BattleEncounterCandidate(
                    environment_kind="outer_realm",
                    environment_ref=f"{config_id}|{floor}|{stage}",
                    environment_name=(
                        localized_text(None, 'auto.services.battle_report.targets.encounter.catalog.outer_candidates.feb6c1325', '轨外之境 · {value} · 第{floor}层{value_2}', value=f'{period_label_for_config(config_details[config_id])}', floor=f'{floor}', value_2=f"{'上半' if half == 'upper' else '下半'}")
                    ),
                    scope_half=half,
                    outer_realm_floor=floor,
                    difficulty_id=None,
                    feast_options=(),
                    targets=targets,
                ))
        return tuple(result)

    @staticmethod
    def _clone_candidates(static_dao: Any) -> tuple[BattleEncounterCandidate, ...]:
        groups: dict[tuple[str, str, int], list[Mapping[str, Any]]] = defaultdict(list)
        for row in static_dao.list_clone_encounter_fingerprint_rows():
            if str(row.get("category_name_zh") or "") not in _INFERRED_CLONE_CATEGORIES:
                continue
            groups[(
                str(row.get("category_id") or ""),
                str(row.get("clone_id") or ""),
                int(row.get("difficulty_ordinal") or 0),
            )].append(row)
        result = []
        for category_id, clone_id, ordinal in sorted(groups):
            rows = sorted(
                groups[(category_id, clone_id, ordinal)],
                key=lambda row: (
                    str(row.get("target_id") or "").casefold(),
                    str(row.get("target_id") or ""),
                ),
            )
            targets = tuple(
                target
                for row in rows
                if (target := _target_preset(
                    row,
                    target_id=row.get("target_id"),
                    target_name=row.get("target_name"),
                    monster_class_path=row.get("monster_template_path"),
                    monster_count=row.get("monster_count"),
                )) is not None
            )
            if targets:
                result.append(BattleEncounterCandidate(
                    environment_kind="clone",
                    environment_ref=f"clone|{category_id}|{clone_id}|{ordinal}",
                    environment_name=(
                        localized_text(None, 'auto.services.battle_report.targets.encounter.catalog.clone_candidates.fab5699bc', '{value} · {value_2} · 难度 {value_3}', value=f"{rows[0].get('category_name_zh') or '副本'}", value_2=f"{rows[0].get('activity_name_zh') or clone_id}", value_3=f"{rows[0].get('difficulty_level') or ordinal + 1}")
                    ),
                    scope_half="",
                    outer_realm_floor=None,
                    difficulty_id=None,
                    feast_options=(),
                    targets=targets,
                ))
        return tuple(result)

    @staticmethod
    def _feast_candidates(static_dao: Any) -> tuple[BattleEncounterCandidate, ...]:
        result = []
        stages = sorted(
            static_dao.list_feast_stages(),
            key=lambda row: (
                -_numeric_suffix(row.get("stage_id")),
                str(row.get("stage_id") or ""),
            ),
        )
        for stage in stages:
            health_options = [("", "", 0.0)]
            default_options = []
            for category in sorted(
                stage.get("option_categories") or (),
                key=lambda row: int(row.get("category_ordinal") or 0),
            ):
                category_key = str(category.get("category_ordinal") or 0)
                options = tuple(category.get("options") or ())
                for option in options:
                    if option.get("effect_kind") == "health_up":
                        health_options.append((
                            category_key,
                            str(option.get("option_id") or ""),
                            float(option.get("add_value") or 0.0),
                        ))
                highest = max(
                    (
                        option
                        for option in options
                        if option.get("effect_kind") in {"attack_up", "resistance_up"}
                    ),
                    key=lambda option: (
                        float(option.get("add_value") or 0.0),
                        str(option.get("option_id") or ""),
                    ),
                    default=None,
                )
                if highest is not None:
                    default_options.append((category_key, str(highest["option_id"])))
            grouped = defaultdict(list)
            for row in stage.get("difficulties") or ():
                grouped[int(row["difficulty_id"])].append(row)
            for difficulty_id, rows in sorted(grouped.items()):
                difficulty = dict(rows[0])
                if len(rows) > 1 and "targets" not in difficulty:
                    if any(not row.get("profile_available", True) for row in rows):
                        continue
                    difficulty["targets"] = [{
                        "target_id": row["boss_monster_id"], "monster_class_path": row["boss_monster_id"],
                        "name_zh": row.get("boss_name_zh"), "monster_level": row["monster_level"],
                        "profile_set": row.get("profile_set"), "pack_id": row.get("pack_id"), "profile": row,
                    } for row in rows]
                for health_category, option_id, health_up in health_options:
                    selected_options = list(default_options)
                    if option_id:
                        selected_options.append((health_category, option_id))
                    selected_ids = {value for _, value in selected_options}
                    options = [option for category in stage.get("option_categories", ())
                               for option in category.get("options", ())
                               if option.get("option_id") in selected_ids]
                    members = feast_target_profiles(stage, difficulty, options)
                    targets = tuple(_target_preset(
                        {**member["profile"], "monster_level": member["monster_level"],
                         "profile_set": member.get("profile_set"), "pack_id": member.get("pack_id")},
                        target_id=member["target_id"], target_name=member["name_zh"],
                        monster_class_path=member["monster_class_path"],
                        monster_count=member.get("monster_count", 1),
                    ) for member in members)
                    if not targets or any(target is None for target in targets):
                        continue
                    option_suffix = (
                        "" if not option_id else localized_text(None, 'auto.services.battle_report.targets.encounter.catalog.feast_candidates.f131d101d', ' · 生命 +{value}%', value=f'{health_up * 100:.0f}')
                    )
                    result.append(BattleEncounterCandidate(
                        environment_kind="feast",
                        environment_ref=str(stage.get("stage_id") or ""),
                        environment_name=(
                            localized_text(None, 'auto.services.battle_report.targets.encounter.catalog.feast_candidates.fd875a0a9', '争锋赏宴 · {value} · {value_2}{option_suffix}', value=f"{stage.get('name_zh') or stage.get('stage_id')}", value_2=f"{members[0].get('name_zh') or difficulty_id}", option_suffix=f'{option_suffix}')
                        ),
                        scope_half="",
                        outer_realm_floor=None,
                        difficulty_id=difficulty_id,
                        feast_options=tuple(sorted(selected_options)),
                        targets=targets,
                    ))
        return tuple(result)

    @staticmethod
    def _world_boss_candidates(static_dao: Any) -> tuple[BattleEncounterCandidate, ...]:
        return tuple(sorted(
            world_boss_candidates(
                static_dao,
                target_preset=_target_preset,
                candidate_factory=BattleEncounterCandidate,
            ),
            key=lambda row: (
                row.environment_ref.casefold(),
                row.environment_ref,
            ),
        ))

    @staticmethod
    def _hydrate_candidate(
        static_dao: Any,
        candidate: BattleEncounterCandidate,
    ) -> BattleEncounterCandidate:
        cache: dict[tuple[str, str], Mapping[str, Any]] = {}
        targets = []
        for target in candidate.targets:
            key = (target.profile_set, target.pack_id)
            profile = cache.get(key)
            if profile is None and all(key):
                profile = static_dao.get_enemy_combat_profile(*key) or {}
                cache[key] = profile
            profile = profile or {}
            targets.append(replace(
                target,
                defense_base=(
                    observed_number(profile.get("defense_base"))
                    if profile.get("defense_base") is not None
                    else target.defense_base
                ),
                defense_up=(
                    observed_number(profile.get("defense_up"))
                    if profile.get("defense_up") is not None
                    else target.defense_up
                ) or 0.0,
                defense_add=(
                    observed_number(profile.get("defense_add"))
                    if profile.get("defense_add") is not None
                    else target.defense_add
                ) or 0.0,
                topple_limit=(
                    observed_number(profile.get("topple_limit"))
                    if profile.get("topple_limit") is not None
                    else target.topple_limit
                ) or 50.0,
                resistances=target.resistances or _resistances(profile.get("resistances")),
            ))
        return replace(candidate, targets=tuple(targets))
