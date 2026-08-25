# 以完整怪物数量和初始最大生命多重集反向识别环境与目标身份。
"""Infer one encounter and its target identities from immutable hit evidence."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.domain.battle_report import BattleAnalysisSnapshot, BattleTargetCondition
from src.services.battle_encounter_candidate_support import world_boss_candidates
from src.services.battle_mixed_outer_realm_inference_service import (
    infer_mixed_outer_realm,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


_HALF_STAGE = {
    "upper": "EAbyssFightStage::FirstHalf",
    "lower": "EAbyssFightStage::SecondHalf",
}
_MONSTER_KEY = re.compile(r"(?i)(boss|mon)_0*(\d+)")
_INFERRED_CLONE_CATEGORIES = {
    "经验及甲硬币",
    "异能升级材料",
    "弧盘突破材料",
    "空幕",
}
INFERRED_ENCOUNTER_SOURCE_KIND = "inferred_unique_encounter_hp_multiset"


def _float(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _hp(value: object) -> float | None:
    number = _float(value)
    return None if number is None or number <= 0.0 else round(number, 3)


def _final_hp(row: Mapping[str, Any]) -> float | None:
    base = _float(row.get("health_base"))
    if base is None or base <= 0.0:
        return None
    return round(
        base * (1.0 + (_float(row.get("health_up")) or 0.0))
        + (_float(row.get("health_add")) or 0.0),
        3,
    )


def _monster_key(value: object) -> str:
    match = _MONSTER_KEY.search(str(value or ""))
    return "" if match is None else f"{match.group(1).lower()}_{int(match.group(2))}"


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


@dataclass(frozen=True, slots=True)
class BattleEncounterTargetPreset:
    target_id: str
    target_name: str
    monster_class_path: str
    monster_count: int
    max_hp: float
    monster_level: float
    profile_set: str
    pack_id: str
    defense_base: float | None
    defense_up: float
    defense_add: float
    topple_limit: float
    resistances: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True, slots=True)
class BattleInferredTargetIdentity:
    scope_half: str
    captured_target_id: str
    target_name: str
    inferred_monster_id: str
    initial_max_hp: float


@dataclass(frozen=True, slots=True)
class BattleInferredEncounter:
    environment_kind: str
    environment_ref: str
    environment_name: str
    source_kind: str
    confidence: str
    inference_basis: str
    scope_half: str
    outer_realm_floor: int | None
    difficulty_id: int | None
    feast_options: tuple[tuple[str, str], ...]
    targets: tuple[BattleEncounterTargetPreset, ...]
    identities: tuple[BattleInferredTargetIdentity, ...]
    target_condition: BattleTargetCondition | None
    ambiguous: bool = False
    ambiguity_alternatives: tuple[str, ...] = ()
    target_conditions_by_half: tuple[tuple[str, BattleTargetCondition], ...] = ()


@dataclass(frozen=True, slots=True)
class _EncounterCandidate:
    environment_kind: str
    environment_ref: str
    environment_name: str
    scope_half: str
    outer_realm_floor: int | None
    difficulty_id: int | None
    feast_options: tuple[tuple[str, str], ...]
    targets: tuple[BattleEncounterTargetPreset, ...]


@dataclass(frozen=True, slots=True)
class _ObservedTarget:
    scope_half: str
    target_id: str
    monster_id: str
    initial_max_hp: float
    first_time_us: int


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
        target_name=str(target_name or target_id or monster_class_path or "未知目标").strip(),
        monster_class_path=str(monster_class_path or "").strip(),
        monster_count=max(1, int(monster_count or 1)),
        max_hp=max_hp,
        monster_level=float(row.get("monster_level") or 1.0),
        profile_set=str(row.get("profile_set") or ""),
        pack_id=str(row.get("pack_id") or ""),
        defense_base=_float(row.get("defense_base")),
        defense_up=_float(row.get("defense_up")) or 0.0,
        defense_add=_float(row.get("defense_add")) or 0.0,
        topple_limit=_float(row.get("topple_limit")) or 50.0,
        resistances=_resistances(row.get("resistances")),
    )


def _candidate_signature(candidate: _EncounterCandidate) -> tuple[float, ...]:
    return tuple(sorted(
        target.max_hp
        for target in candidate.targets
        for _ in range(target.monster_count)
    ))


def _shared_profile(
    targets: Sequence[BattleEncounterTargetPreset],
) -> tuple | None:
    signatures = {
        (
            target.monster_level,
            target.defense_base,
            target.defense_up,
            target.defense_add,
            target.topple_limit,
            target.resistances,
        )
        for target in targets
    }
    if len(signatures) != 1:
        return None
    signature = next(iter(signatures))
    return None if signature[1] is None else signature


class BattleInferredTargetConditionService:
    """Match complete target/HP multisets across every catalogued environment."""

    @classmethod
    def infer(
        cls,
        *,
        static_database_path: Path | None,
        combat_context_kind: str,
        floor: int | None,
        evidence: Mapping[str, Any] | None,
        range_start_us: int | None,
        range_end_us: int | None,
    ) -> BattleInferredEncounter | None:
        if static_database_path is None:
            return None
        mixed = infer_mixed_outer_realm(
            static_database_path=static_database_path,
            combat_context_kind=combat_context_kind, floor=floor, evidence=evidence,
            range_start_us=range_start_us, range_end_us=range_end_us,
            infer_half=cls.infer,
        )
        if mixed is not None:
            return mixed
        observed = cls._observed_targets(
            evidence,
            range_start_us=range_start_us,
            range_end_us=range_end_us,
        )
        if not observed:
            return None
        observed_halves = {target.scope_half for target in observed if target.scope_half}
        if len(observed_halves) > 1:
            return None
        observed_signature = tuple(sorted(target.initial_max_hp for target in observed))
        try:
            with StaticGameDataDao(static_database_path) as static_dao:
                candidates = cls._load_candidates(static_dao)
                candidates = cls._filter_candidates(
                    candidates,
                    combat_context_kind=combat_context_kind,
                    floor=floor,
                    scope_half=next(iter(observed_halves), ""),
                )
                matches = tuple(
                    candidate
                    for candidate in candidates
                    if _candidate_signature(candidate) == observed_signature
                    and cls._candidate_matches_identity(candidate, observed)
                )
                resolved = cls._resolve_match(matches)
                if resolved is None:
                    return None
                candidate, ambiguity_alternatives = resolved
                candidate = cls._hydrate_candidate(static_dao, candidate)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            return None

        identities = cls._assign_identities(observed, candidate)
        condition = cls._condition(candidate)
        signature_text = ", ".join(f"{value:,.0f}" for value in observed_signature)
        ambiguous = bool(ambiguity_alternatives)
        return BattleInferredEncounter(
            environment_kind=candidate.environment_kind,
            environment_ref=candidate.environment_ref,
            environment_name=candidate.environment_name,
            source_kind=INFERRED_ENCOUNTER_SOURCE_KIND,
            confidence="中" if ambiguous else "高",
            inference_basis=(
                (
                    f"当前遭遇的 {len(observed)} 个目标实例及初始最大生命多重集"
                    f" [{signature_text}] 同时符合黑之书与无首铁驭；"
                    "按产品默认选择黑之书，目标身份仍有歧义，请按实际战斗确认。"
                )
                if ambiguous
                else (
                    f"当前遭遇的 {len(observed)} 个目标实例及初始最大生命多重集"
                    f" [{signature_text}] 在静态环境目录中只有一个完整配置符合。"
                )
            ),
            scope_half=candidate.scope_half,
            outer_realm_floor=candidate.outer_realm_floor,
            difficulty_id=candidate.difficulty_id,
            feast_options=candidate.feast_options,
            targets=candidate.targets,
            identities=identities,
            target_condition=condition,
            ambiguous=ambiguous,
            ambiguity_alternatives=ambiguity_alternatives,
            target_conditions_by_half=(
                () if condition is None else ((candidate.scope_half, condition),)
            ),
        )

    @staticmethod
    def project_evidence(
        evidence: dict[str, Any] | None,
        inferred: BattleInferredEncounter | None,
    ) -> None:
        """Project derived names in memory without changing persisted axis rows."""

        if evidence is None or inferred is None:
            return
        identities = {
            (row.scope_half, row.captured_target_id): row
            for row in inferred.identities
        }
        for hit in evidence.get("hits") or ():
            scope_half = str(hit.get("abyss_half") or "").strip().casefold()
            target_id = str(hit.get("target_id") or "").strip()
            identity = identities.get((scope_half, target_id))
            if identity is None:
                identity = identities.get(("", target_id))
            if identity is None:
                continue
            if identity.target_name:
                hit["target_name"] = identity.target_name
            if (
                identity.inferred_monster_id
                and not str(hit.get("target_monster_id") or "").strip()
            ):
                hit["target_monster_id"] = identity.inferred_monster_id
            hit["target_identity_source"] = inferred.source_kind
            hit["target_identity_confidence"] = inferred.confidence
            hit["target_environment_ref"] = inferred.environment_ref

    @staticmethod
    def apply(
        analysis: BattleAnalysisSnapshot,
        inferred: BattleInferredEncounter | None,
    ) -> BattleAnalysisSnapshot:
        if inferred is None:
            return analysis
        return replace(
            analysis,
            target_condition=(
                analysis.target_condition
                if analysis.target_condition is not None
                else inferred.target_condition
            ),
            detected_environment_kind=inferred.environment_kind,
            detected_environment_ref=inferred.environment_ref,
            detected_environment_name=inferred.environment_name,
            detected_environment_difficulty_id=inferred.difficulty_id,
            detected_environment_options=inferred.feast_options,
            detected_outer_realm_floor=inferred.outer_realm_floor,
            target_identity_inference_source=inferred.source_kind,
            target_identity_inference_confidence=inferred.confidence,
            target_identity_inference_basis=inferred.inference_basis,
            target_identity_inference_ambiguous=inferred.ambiguous,
            target_identity_inference_alternatives=inferred.ambiguity_alternatives,
            target_conditions_by_half=inferred.target_conditions_by_half,
        )

    @staticmethod
    def _resolve_match(
        matches: Sequence[_EncounterCandidate],
    ) -> tuple[_EncounterCandidate, tuple[str, ...]] | None:
        if len(matches) == 1:
            return matches[0], ()
        if len(matches) != 2:
            return None
        by_name = {
            candidate.targets[0].target_name: candidate
            for candidate in matches
            if candidate.environment_kind == "open_world"
            and len(candidate.targets) == 1
        }
        if set(by_name) != {"黑之书", "无首铁驭"}:
            return None
        return by_name["黑之书"], (by_name["无首铁驭"].environment_name,)

    @staticmethod
    def _observed_targets(
        evidence: Mapping[str, Any] | None,
        *,
        range_start_us: int | None,
        range_end_us: int | None,
    ) -> tuple[_ObservedTarget, ...]:
        outgoing = tuple(
            row
            for row in (evidence or {}).get("hits") or ()
            if str(row.get("direction") or "").casefold() == "outgoing"
            and str(row.get("target_id") or "").strip()
            and _hp(row.get("target_max_hp")) is not None
        )
        if not outgoing:
            return ()
        scoped = tuple(
            row
            for row in outgoing
            if (range_start_us is None or int(row.get("relative_time_us") or 0) >= range_start_us)
            and (range_end_us is None or int(row.get("relative_time_us") or 0) < range_end_us)
        )
        selected_halves = {
            str(row.get("abyss_half") or "").strip().casefold()
            for row in scoped
            if str(row.get("abyss_half") or "").strip().casefold() in _HALF_STAGE
        }
        if len(selected_halves) > 1:
            return ()
        if selected_halves:
            selected_half = next(iter(selected_halves))
            outgoing = tuple(
                row
                for row in outgoing
                if str(row.get("abyss_half") or "").strip().casefold() == selected_half
            )
        elif scoped:
            outgoing = scoped

        states: dict[tuple[str, str], tuple[float, int, str]] = {}
        for row in outgoing:
            half = str(row.get("abyss_half") or "").strip().casefold()
            target_id = str(row.get("target_id") or "").strip()
            max_hp = _hp(row.get("target_max_hp"))
            if max_hp is None:
                continue
            time_us = int(row.get("relative_time_us") or 0)
            monster_id = str(row.get("target_monster_id") or "").strip()
            previous = states.get((half, target_id))
            states[(half, target_id)] = (
                max(max_hp, previous[0]) if previous else max_hp,
                min(time_us, previous[1]) if previous else time_us,
                (previous[2] if previous and previous[2] else monster_id),
            )
        return tuple(sorted(
            (
                _ObservedTarget(half, target_id, values[2], values[0], values[1])
                for (half, target_id), values in states.items()
            ),
            key=lambda row: (row.first_time_us, row.target_id),
        ))

    @staticmethod
    def _candidate_matches_identity(
        candidate: _EncounterCandidate,
        observed: Sequence[_ObservedTarget],
    ) -> bool:
        for row in observed:
            observed_key = _monster_key(row.monster_id)
            if not observed_key:
                continue
            matching_hp = tuple(
                target
                for target in candidate.targets
                if target.max_hp == row.initial_max_hp
            )
            if not any(
                observed_key in {
                    _monster_key(target.target_id),
                    _monster_key(target.monster_class_path),
                }
                for target in matching_hp
            ):
                return False
        return True

    @classmethod
    def _load_candidates(cls, static_dao) -> tuple[_EncounterCandidate, ...]:
        names = {
            _monster_key(row.get("monster_manual_id")): str(row.get("name_zh") or "")
            for row in static_dao.list_monster_display_names()
        }
        return (
            *cls._outer_candidates(static_dao, names),
            *cls._clone_candidates(static_dao),
            *cls._feast_candidates(static_dao),
            *cls._world_boss_candidates(static_dao),
        )

    @staticmethod
    def _outer_candidates(static_dao, names) -> tuple[_EncounterCandidate, ...]:
        latest_configs = {
            str(row.get("level_config_id") or "")
            for row in static_dao.list_outer_realm_configs()[:2]
        }
        groups: dict[tuple[str, int, str], list[Mapping[str, Any]]] = defaultdict(list)
        for row in static_dao.list_outer_realm_target_presets():
            if str(row.get("level_config_id") or "") not in latest_configs:
                continue
            groups[(
                str(row.get("level_config_id") or ""),
                int(row.get("level_id") or 0),
                str(row.get("fight_stage") or ""),
            )].append(row)
        result = []
        for (config_id, floor, stage), rows in groups.items():
            targets = tuple(
                target
                for row in rows
                if (target := _target_preset(
                    row,
                    target_id=row.get("monster_class_path"),
                    target_name=(
                        row.get("monster_name_zh")
                        or names.get(_monster_key(row.get("monster_class_path")))
                        or row.get("monster_class_path")
                    ),
                    monster_class_path=row.get("monster_class_path"),
                    monster_count=row.get("monster_count"),
                )) is not None
            )
            half = "upper" if "FirstHalf" in stage else "lower"
            if targets:
                result.append(_EncounterCandidate(
                    environment_kind="outer_realm",
                    environment_ref=f"{config_id}|{floor}|{stage}",
                    environment_name=f"轨外之境第{floor}层{'上半' if half == 'upper' else '下半'}",
                    scope_half=half,
                    outer_realm_floor=floor,
                    difficulty_id=None,
                    feast_options=(),
                    targets=targets,
                ))
        return tuple(result)

    @staticmethod
    def _clone_candidates(static_dao) -> tuple[_EncounterCandidate, ...]:
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
        for (category_id, clone_id, ordinal), rows in groups.items():
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
                result.append(_EncounterCandidate(
                    environment_kind="clone",
                    environment_ref=f"clone|{category_id}|{clone_id}|{ordinal}",
                    environment_name=(
                        f"{rows[0].get('category_name_zh') or '副本'} · "
                        f"{rows[0].get('activity_name_zh') or clone_id} · "
                        f"难度 {rows[0].get('difficulty_level') or ordinal + 1}"
                    ),
                    scope_half="",
                    outer_realm_floor=None,
                    difficulty_id=None,
                    feast_options=(),
                    targets=targets,
                ))
        return tuple(result)

    @staticmethod
    def _feast_candidates(static_dao) -> tuple[_EncounterCandidate, ...]:
        result = []
        stages = sorted(
            static_dao.list_feast_stages(),
            key=lambda row: _numeric_suffix(row.get("stage_id")),
            reverse=True,
        )[:2]
        for stage in stages:
            health_options = [("", "", 0.0)]
            default_options = []
            resistance_additions: dict[str, float] = {}
            for category in stage.get("option_categories") or ():
                category_key = str(category.get("category_ordinal") or 0)
                options = tuple(category.get("options") or ())
                for option in category.get("options") or ():
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
                    key=lambda option: float(option.get("add_value") or 0.0),
                    default=None,
                )
                if highest is not None:
                    default_options.append((category_key, str(highest["option_id"])))
                    if highest.get("effect_kind") == "resistance_up":
                        damage_type = str(highest.get("damage_type") or "")
                        resistance_additions[damage_type] = float(
                            highest.get("add_value") or 0.0
                        )
            for difficulty in stage.get("difficulties") or ():
                for health_category, option_id, health_up in health_options:
                    row = dict(difficulty)
                    row["health_up"] = float(row.get("health_up") or 0.0) + health_up
                    base_resistances = {
                        key: (
                            float(value.get("resistance_base") or 0.0)
                            if isinstance(value, Mapping)
                            else float(value or 0.0)
                        )
                        for key, value in (row.get("resistances") or {}).items()
                    }
                    for damage_type, addition in resistance_additions.items():
                        base_resistances[damage_type] = (
                            base_resistances.get(damage_type, 0.0) + addition
                        )
                    row["resistances"] = base_resistances
                    target = _target_preset(
                        row,
                        target_id=stage.get("boss_monster_id"),
                        target_name=difficulty.get("boss_name_zh"),
                        monster_class_path=stage.get("boss_monster_id"),
                    )
                    if target is None:
                        continue
                    difficulty_id = int(difficulty.get("difficulty_id") or 0)
                    option_suffix = (
                        "" if not option_id else f" · 生命 +{health_up * 100:.0f}%"
                    )
                    selected_options = list(default_options)
                    if option_id:
                        selected_options.append((health_category, option_id))
                    result.append(_EncounterCandidate(
                        environment_kind="feast",
                        environment_ref=str(stage.get("stage_id") or ""),
                        environment_name=(
                            f"争锋赏宴 · {stage.get('name_zh') or stage.get('stage_id')}"
                            f" · {difficulty.get('name_zh') or difficulty_id}{option_suffix}"
                        ),
                        scope_half="",
                        outer_realm_floor=None,
                        difficulty_id=difficulty_id,
                        feast_options=tuple(sorted(selected_options)),
                        targets=(target,),
                    ))
        return tuple(result)

    @staticmethod
    def _world_boss_candidates(static_dao) -> tuple[_EncounterCandidate, ...]:
        return world_boss_candidates(
            static_dao,
            target_preset=_target_preset,
            candidate_factory=_EncounterCandidate,
        )

    @staticmethod
    def _filter_candidates(
        candidates: Sequence[_EncounterCandidate],
        *,
        combat_context_kind: str,
        floor: int | None,
        scope_half: str,
    ) -> tuple[_EncounterCandidate, ...]:
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
    def _hydrate_candidate(static_dao, candidate: _EncounterCandidate) -> _EncounterCandidate:
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
                defense_base=_float(profile.get("defense_base")) or target.defense_base,
                defense_up=_float(profile.get("defense_up")) or target.defense_up,
                defense_add=_float(profile.get("defense_add")) or target.defense_add,
                topple_limit=_float(profile.get("topple_limit")) or target.topple_limit,
                resistances=target.resistances or _resistances(profile.get("resistances")),
            ))
        return replace(candidate, targets=tuple(targets))

    @staticmethod
    def _assign_identities(
        observed: Sequence[_ObservedTarget],
        candidate: _EncounterCandidate,
    ) -> tuple[BattleInferredTargetIdentity, ...]:
        observed_by_hp: dict[float, list[_ObservedTarget]] = defaultdict(list)
        candidates_by_hp: dict[float, list[BattleEncounterTargetPreset]] = defaultdict(list)
        for row in observed:
            observed_by_hp[row.initial_max_hp].append(row)
        for target in candidate.targets:
            candidates_by_hp[target.max_hp].extend([target] * target.monster_count)

        resolved: list[tuple[_ObservedTarget, str, str]] = []
        for max_hp, observed_rows in observed_by_hp.items():
            candidate_rows = candidates_by_hp[max_hp]
            names = {row.target_name for row in candidate_rows}
            if len(names) != 1:
                continue
            name = next(iter(names))
            monster_ids = {row.target_id for row in candidate_rows}
            monster_id = next(iter(monster_ids)) if len(monster_ids) == 1 else ""
            for row in sorted(observed_rows, key=lambda item: (item.first_time_us, item.target_id)):
                resolved.append((row, name, monster_id))

        name_counts = Counter(name for _, name, _ in resolved)
        name_ordinals: Counter[str] = Counter()
        identities = []
        for row, name, monster_id in sorted(
            resolved,
            key=lambda item: (item[0].first_time_us, item[0].target_id),
        ):
            name_ordinals[name] += 1
            display_name = (
                f"{name} {name_ordinals[name]}" if name_counts[name] > 1 else name
            )
            identities.append(BattleInferredTargetIdentity(
                scope_half=row.scope_half,
                captured_target_id=row.target_id,
                target_name=display_name,
                inferred_monster_id=monster_id,
                initial_max_hp=row.initial_max_hp,
            ))
        return tuple(identities)

    @staticmethod
    def _condition(candidate: _EncounterCandidate) -> BattleTargetCondition | None:
        profile = _shared_profile(candidate.targets)
        if profile is None:
            return None
        level, defense_base, defense_up, defense_add, topple_limit, resistances = profile
        static_ids = tuple(dict.fromkeys(
            target.target_id for target in candidate.targets if target.target_id
        ))
        condition_kind = (
            "open_world" if candidate.environment_kind == "clone"
            else candidate.environment_kind
        )
        return BattleTargetCondition(
            target_name=candidate.environment_name,
            enemy_level=float(level or 1.0),
            scene=(
                "open_world"
                if candidate.environment_kind in {"open_world", "clone"}
                else "outer_realm"
            ),
            defense_reduction=0.0,
            vulnerability=0.0,
            resistances=resistances,
            source_kind=INFERRED_ENCOUNTER_SOURCE_KIND,
            enemy_defense_base=float(defense_base),
            enemy_defense_up=float(defense_up),
            enemy_defense_add=float(defense_add),
            enemy_topple_limit=float(topple_limit),
            environment_kind=condition_kind,
            environment_ref=candidate.environment_ref,
            selected_target_ids=static_ids,
            primary_target_id=static_ids[0] if len(static_ids) == 1 else "",
            difficulty_id=candidate.difficulty_id,
            feast_options=candidate.feast_options,
        )
