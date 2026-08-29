# 序列化并恢复自动目标推断派生快照。
"""Serialize and restore automatic target inference as a derived snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict
from typing import Any

from src.domain.battle_encounter import (
    BattleEncounterCandidate,
    BattleEncounterCandidateMatch,
    BattleEncounterTargetPreset,
)
from src.domain.battle_report import BattleTargetCondition
from src.domain.battle_target import BattleSelectedTargetProfile
from src.services.battle_inferred_target_condition_service import (
    INFERRED_ENCOUNTER_ALGORITHM_VERSION,
    BattleInferredEncounter,
    BattleInferredTargetConditionService,
    BattleInferredTargetIdentity,
)
from src.observability import OperationContext
from src.observability.operation import log_event, safe_exception
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


INFERRED_TARGET_SNAPSHOT_SCHEMA_VERSION = 1


def _pairs(value: object) -> tuple[tuple[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        (str(item[0]), item[1])
        for item in value
        if isinstance(item, (list, tuple)) and len(item) == 2
    )


def _target(value: Mapping[str, Any]) -> BattleEncounterTargetPreset:
    return BattleEncounterTargetPreset(
        target_id=str(value.get("target_id") or ""),
        target_name=str(value.get("target_name") or ""),
        monster_class_path=str(value.get("monster_class_path") or ""),
        monster_count=int(value.get("monster_count") or 1),
        max_hp=float(value.get("max_hp") or 0.0),
        monster_level=float(value.get("monster_level") or 1.0),
        profile_set=str(value.get("profile_set") or ""),
        pack_id=str(value.get("pack_id") or ""),
        defense_base=(
            None
            if value.get("defense_base") is None
            else float(value["defense_base"])
        ),
        defense_up=float(value.get("defense_up") or 0.0),
        defense_add=float(value.get("defense_add") or 0.0),
        topple_limit=float(value.get("topple_limit") or 0.0),
        resistances=tuple(
            (str(key), float(number))
            for key, number in _pairs(value.get("resistances"))
        ),
    )


def _candidate(value: Mapping[str, Any]) -> BattleEncounterCandidate:
    return BattleEncounterCandidate(
        environment_kind=str(value.get("environment_kind") or ""),
        environment_ref=str(value.get("environment_ref") or ""),
        environment_name=str(value.get("environment_name") or ""),
        scope_half=str(value.get("scope_half") or ""),
        outer_realm_floor=(
            None
            if value.get("outer_realm_floor") is None
            else int(value["outer_realm_floor"])
        ),
        difficulty_id=(
            None
            if value.get("difficulty_id") is None
            else int(value["difficulty_id"])
        ),
        feast_options=tuple(
            (str(key), str(item))
            for key, item in _pairs(value.get("feast_options"))
        ),
        targets=tuple(
            _target(item)
            for item in value.get("targets") or ()
            if isinstance(item, Mapping)
        ),
        catalog_order=int(value.get("catalog_order") or 0),
    )


def _match(value: Mapping[str, Any]) -> BattleEncounterCandidateMatch:
    candidate = value.get("candidate")
    if not isinstance(candidate, Mapping):
        raise ValueError("目标推断候选缺少环境")
    return BattleEncounterCandidateMatch(
        candidate=_candidate(candidate),
        possible_target_indexes=tuple(
            tuple(int(index) for index in indexes)
            for indexes in value.get("possible_target_indexes") or ()
        ),
        hard_identity_matches=int(value.get("hard_identity_matches") or 0),
        unobserved_slot_count=int(value.get("unobserved_slot_count") or 0),
    )


def _profile(value: Mapping[str, Any]) -> BattleSelectedTargetProfile:
    return BattleSelectedTargetProfile(
        static_target_id=str(value.get("static_target_id") or ""),
        selection_target_id=str(value.get("selection_target_id") or ""),
        target_name=str(value.get("target_name") or ""),
        monster_class_path=str(value.get("monster_class_path") or ""),
        monster_count=int(value.get("monster_count") or 1),
        max_hp=float(value.get("max_hp") or 0.0),
        monster_level=float(value.get("monster_level") or 1.0),
        defense_base=(
            None
            if value.get("defense_base") is None
            else float(value["defense_base"])
        ),
        defense_up=float(value.get("defense_up") or 0.0),
        defense_add=float(value.get("defense_add") or 0.0),
        topple_limit=float(value.get("topple_limit") or 0.0),
        resistances=tuple(
            (str(key), float(number))
            for key, number in _pairs(value.get("resistances"))
        ),
        profile_set=str(value.get("profile_set") or ""),
        pack_id=str(value.get("pack_id") or ""),
    )


def _condition(value: object) -> BattleTargetCondition | None:
    if not isinstance(value, Mapping):
        return None
    return BattleTargetCondition(
        target_name=str(value.get("target_name") or ""),
        enemy_level=float(value.get("enemy_level") or 1.0),
        scene=str(value.get("scene") or "open_world"),
        defense_reduction=float(value.get("defense_reduction") or 0.0),
        vulnerability=float(value.get("vulnerability") or 0.0),
        resistances=tuple(
            (str(key), float(number))
            for key, number in _pairs(value.get("resistances"))
        ),
        source_kind=str(value.get("source_kind") or ""),
        enemy_defense_base=(
            None
            if value.get("enemy_defense_base") is None
            else float(value["enemy_defense_base"])
        ),
        enemy_defense_up=float(value.get("enemy_defense_up") or 0.0),
        enemy_defense_add=float(value.get("enemy_defense_add") or 0.0),
        enemy_topple_limit=float(value.get("enemy_topple_limit") or 50.0),
        environment_kind=str(value.get("environment_kind") or "manual"),
        environment_ref=str(value.get("environment_ref") or ""),
        selected_target_ids=tuple(
            str(item) for item in value.get("selected_target_ids") or ()
        ),
        primary_target_id=str(value.get("primary_target_id") or ""),
        difficulty_id=(
            None
            if value.get("difficulty_id") is None
            else int(value["difficulty_id"])
        ),
        feast_options=tuple(
            (str(key), str(item))
            for key, item in _pairs(value.get("feast_options"))
        ),
        witch_buff_id=str(value.get("witch_buff_id") or ""),
        witch_buff_name_zh=str(value.get("witch_buff_name_zh") or ""),
        witch_buff_property_id=str(value.get("witch_buff_property_id") or ""),
        witch_buff_value=(
            None
            if value.get("witch_buff_value") is None
            else float(value["witch_buff_value"])
        ),
        witch_buff_is_percent=bool(value.get("witch_buff_is_percent")),
        selected_target_profiles=tuple(
            _profile(item)
            for item in value.get("selected_target_profiles") or ()
            if isinstance(item, Mapping)
        ),
        resolved_monster_id=str(value.get("resolved_monster_id") or ""),
    )


class BattleInferredTargetSnapshotService:
    """Version gate and codec for one derived encounter snapshot."""

    @staticmethod
    def payload(inferred: BattleInferredEncounter) -> dict[str, Any]:
        return asdict(inferred)

    @staticmethod
    def static_identity(
        static_database_path: Any,
    ) -> tuple[str | None, int | None]:
        if static_database_path is None:
            return None, None
        try:
            with StaticGameDataDao(static_database_path) as static_dao:
                summary = static_dao.summary()
        except Exception:
            return None, None
        dataset = summary.get("dataset") or {}
        return (
            str(dataset.get("dataset_id") or "").strip() or None,
            int(summary.get("schema_version") or 0) or None,
        )

    @classmethod
    def resolve(
        cls,
        *,
        persisted_row: Mapping[str, Any] | None,
        battle_record_id: int,
        fitted_cache: dict[int, Any],
        inferred_cache: dict[int, Any],
        static_database_path: Any,
        combat_context_kind: str,
        floor: int | None,
        evidence: Mapping[str, Any] | None,
        dependencies: Any,
        context_is_current: Callable[[Any], bool],
    ) -> tuple[
        BattleInferredEncounter | None,
        bool,
        BattleInferredEncounter | None,
    ]:
        """Prefer a durable snapshot, then session caches, then infer once."""

        static_dataset_id, static_schema_version = cls.static_identity(
            static_database_path
        )
        persisted = cls.restore(
            persisted_row,
            static_dataset_id=static_dataset_id,
            static_schema_version=static_schema_version,
        )
        inferred = persisted or fitted_cache.get(battle_record_id)
        fit_cached = bool(
            inferred is not None
            and (
                not inferred.formula_profile_conflict
                or inferred.residual_fit_score is not None
            )
        )
        if inferred is None:
            if battle_record_id in inferred_cache:
                inferred = inferred_cache[battle_record_id]
            else:
                inferred = BattleInferredTargetConditionService.infer(
                    static_database_path=static_database_path,
                    combat_context_kind=combat_context_kind,
                    floor=floor,
                    evidence=evidence,
                    range_start_us=None,
                    range_end_us=None,
                )
                inferred_cache[battle_record_id] = inferred
        saved_seed = bool(
            persisted is None
            and inferred is not None
            and cls.persist(
                dependencies=dependencies,
                context_is_current=context_is_current,
                battle_record_id=battle_record_id,
                inferred=inferred,
            )
        )
        if saved_seed:
            persisted = inferred
        return inferred, fit_cached, persisted

    @classmethod
    def persist(
        cls,
        *,
        dependencies: Any,
        context_is_current: Callable[[Any], bool],
        battle_record_id: int,
        inferred: BattleInferredEncounter,
    ) -> bool:
        """Best-effort write after immutable battle facts are already durable."""

        if not context_is_current(dependencies):
            return False
        try:
            static_dataset_id, static_schema_version = cls.static_identity(
                dependencies.static_database_path
            )
            with UserDataDao(
                dependencies.user_database_path,
                account_id=dependencies.account_id,
                account_name=dependencies.account_id,
            ) as user_dao:
                if not context_is_current(dependencies):
                    return False
                user_dao.save_battle_inferred_target_snapshot(
                    battle_record_id=battle_record_id,
                    payload_schema_version=INFERRED_TARGET_SNAPSHOT_SCHEMA_VERSION,
                    algorithm_version=inferred.algorithm_version,
                    static_dataset_id=static_dataset_id,
                    static_schema_version=static_schema_version,
                    inference_status="resolved",
                    environment_kind=inferred.environment_kind,
                    environment_ref=inferred.environment_ref,
                    environment_name=inferred.environment_name,
                    source_kind=inferred.source_kind,
                    confidence=inferred.confidence,
                    inferred_payload=cls.payload(inferred),
                )
            return True
        except Exception as error:
            log_event(
                "WARNING",
                "battle_report.inferred_target_snapshot_save_failed",
                "保存战报自动目标推断快照失败",
                OperationContext.create(
                    "battle_report",
                    account_id=dependencies.account_id,
                    context_generation=dependencies.generation,
                ),
                phase="failed",
                battle_record_id=battle_record_id,
                error=safe_exception(error),
            )
            return False

    @staticmethod
    def restore(
        row: Mapping[str, Any] | None,
        *,
        static_dataset_id: str | None = None,
        static_schema_version: int | None = None,
    ) -> BattleInferredEncounter | None:
        if row is None or row.get("inference_status") != "resolved":
            return None
        if (
            int(row.get("payload_schema_version") or 0)
            != INFERRED_TARGET_SNAPSHOT_SCHEMA_VERSION
        ):
            return None
        if (
            str(row.get("algorithm_version") or "")
            != INFERRED_ENCOUNTER_ALGORITHM_VERSION
        ):
            return None
        if (
            static_dataset_id is not None
            and str(row.get("static_dataset_id") or "") != static_dataset_id
        ):
            return None
        if (
            static_schema_version is not None
            and int(row.get("static_schema_version") or 0)
            != static_schema_version
        ):
            return None
        value = row.get("inferred_payload")
        if not isinstance(value, Mapping):
            return None
        if str(value.get("algorithm_version") or "") != str(
            row.get("algorithm_version") or ""
        ):
            return None
        for key in (
            "environment_kind",
            "environment_ref",
            "environment_name",
            "source_kind",
            "confidence",
        ):
            if str(value.get(key) or "") != str(row.get(key) or ""):
                return None
        try:
            return BattleInferredEncounter(
                environment_kind=str(value.get("environment_kind") or ""),
                environment_ref=str(value.get("environment_ref") or ""),
                environment_name=str(value.get("environment_name") or ""),
                source_kind=str(value.get("source_kind") or ""),
                confidence=str(value.get("confidence") or ""),
                inference_basis=str(value.get("inference_basis") or ""),
                scope_half=str(value.get("scope_half") or ""),
                outer_realm_floor=(
                    None
                    if value.get("outer_realm_floor") is None
                    else int(value["outer_realm_floor"])
                ),
                difficulty_id=(
                    None
                    if value.get("difficulty_id") is None
                    else int(value["difficulty_id"])
                ),
                feast_options=tuple(
                    (str(key), str(item))
                    for key, item in _pairs(value.get("feast_options"))
                ),
                targets=tuple(
                    _target(item)
                    for item in value.get("targets") or ()
                    if isinstance(item, Mapping)
                ),
                identities=tuple(
                    BattleInferredTargetIdentity(
                        scope_half=str(item.get("scope_half") or ""),
                        captured_target_id=str(
                            item.get("captured_target_id") or ""
                        ),
                        target_name=str(item.get("target_name") or ""),
                        inferred_monster_id=str(
                            item.get("inferred_monster_id") or ""
                        ),
                        initial_max_hp=float(item.get("initial_max_hp") or 0.0),
                    )
                    for item in value.get("identities") or ()
                    if isinstance(item, Mapping)
                ),
                target_condition=_condition(value.get("target_condition")),
                ambiguous=bool(value.get("ambiguous")),
                ambiguity_alternatives=tuple(
                    str(item)
                    for item in value.get("ambiguity_alternatives") or ()
                ),
                target_conditions_by_half=tuple(
                    (str(item[0]), condition)
                    for item in value.get("target_conditions_by_half") or ()
                    if isinstance(item, (list, tuple)) and len(item) == 2
                    and (condition := _condition(item[1])) is not None
                ),
                target_mapping_conditions_by_half=tuple(
                    (str(item[0]), condition)
                    for item in value.get("target_mapping_conditions_by_half") or ()
                    if isinstance(item, (list, tuple)) and len(item) == 2
                    and (condition := _condition(item[1])) is not None
                ),
                selection_mode=str(value.get("selection_mode") or "unique_hard"),
                default_reason=str(value.get("default_reason") or ""),
                alternative_environment_refs=tuple(
                    str(item)
                    for item in value.get("alternative_environment_refs") or ()
                ),
                formula_matches=tuple(
                    _match(item)
                    for item in value.get("formula_matches") or ()
                    if isinstance(item, Mapping)
                ),
                formula_profile_conflict=bool(
                    value.get("formula_profile_conflict")
                ),
                residual_fit_score=(
                    None
                    if value.get("residual_fit_score") is None
                    else float(value["residual_fit_score"])
                ),
                residual_fit_gap=(
                    None
                    if value.get("residual_fit_gap") is None
                    else float(value["residual_fit_gap"])
                ),
                algorithm_version=str(
                    value.get("algorithm_version")
                    or INFERRED_ENCOUNTER_ALGORITHM_VERSION
                ),
            )
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def is_current_row(
        row: Mapping[str, Any] | None,
        *,
        static_dataset_id: str | None = None,
        static_schema_version: int | None = None,
    ) -> bool:
        return bool(
            row is not None
            and row.get("inference_status") == "resolved"
            and int(row.get("payload_schema_version") or 0)
            == INFERRED_TARGET_SNAPSHOT_SCHEMA_VERSION
            and str(row.get("algorithm_version") or "")
            == INFERRED_ENCOUNTER_ALGORITHM_VERSION
            and (
                static_dataset_id is None
                or str(row.get("static_dataset_id") or "")
                == static_dataset_id
            )
            and (
                static_schema_version is None
                or int(row.get("static_schema_version") or 0)
                == static_schema_version
            )
        )
