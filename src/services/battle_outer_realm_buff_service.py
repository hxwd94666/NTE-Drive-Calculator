# 使用正式赛季配置和逐击证据重建轨外之境环境 Buff。
"""Outer-realm season Buff projection kept separate from character Buff rules."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
    BattleTargetCondition,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
    unproject_timeline_time_us,
)
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


OUTER_REALM_BUFF_MODEL_VERSION = "battle-outer-realm-buff-v1"
_TARGET_REQUIREMENT_PREFIX = "battle-target|id="


@dataclass(frozen=True, slots=True)
class BattleOuterRealmBuffComponent:
    component_ordinal: int
    trigger_kind: str
    property_id: str
    value: float
    duration_seconds: float | None = None
    trigger_cooldown_seconds: float | None = None
    stack_limit_count: int = 1


@dataclass(frozen=True, slots=True)
class BattleOuterRealmBuffConfig:
    level_config_id: str
    season_name: str
    buff_id: str
    buff_name: str
    description: str
    gameplay_effect_path: str
    components: tuple[BattleOuterRealmBuffComponent, ...]
    topple_limit: float | None = None
    topple_recovery_speed: float | None = None


def outer_realm_requirement_applies(
    requirement: str,
    hit: BattleAnalysisHit,
) -> tuple[bool, str]:
    if not requirement.startswith(_TARGET_REQUIREMENT_PREFIX):
        return True, ""
    target_id = requirement.removeprefix(_TARGET_REQUIREMENT_PREFIX)
    if hit.target_id != target_id:
        return False, "该轨外增伤只作用于本次进入倾陷的目标"
    return True, ""


def _modifier(
    component: BattleOuterRealmBuffComponent,
    *,
    requirement: str = "",
) -> BattleBuffModifierEvidence:
    return BattleBuffModifierEvidence(
        property_id=component.property_id,
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind="official_outer_realm_curve",
        magnitude_value=component.value,
        calculation_asset_path="",
        value_confidence="高",
        application_requirement_asset_path=requirement,
    )


class BattleOuterRealmBuffService:
    """Load and replay the current/next audited outer-realm season Buff."""

    @staticmethod
    def load(
        static_database_path: str | Path | None,
        environment_ref: str,
    ) -> BattleOuterRealmBuffConfig | None:
        parts = str(environment_ref or "").split("|")
        if static_database_path is None or len(parts) < 3:
            return None
        config_id, level_text, fight_stage = parts[:3]
        if not config_id.startswith("Abyss_"):
            return None
        try:
            level_id = int(level_text)
        except ValueError:
            return None
        with StaticGameDataDao(Path(static_database_path)) as dao:
            row = dao.get_outer_realm_season_buff(config_id)
            recovery = dao.get_outer_realm_topple_recovery(
                config_id,
                level_id,
                fight_stage,
            )
        if row is None:
            return None
        return BattleOuterRealmBuffConfig(
            level_config_id=config_id,
            season_name=str(row["season_name_zh"]),
            buff_id=str(row["buff_id"]),
            buff_name=str(row["buff_name_zh"]),
            description=str(row["description_zh"]),
            gameplay_effect_path=str(row["gameplay_effect_path"]),
            components=tuple(
                BattleOuterRealmBuffComponent(
                    component_ordinal=int(component["component_ordinal"]),
                    trigger_kind=str(component["trigger_kind"]),
                    property_id=str(component["property_id"]),
                    value=float(component["property_value"]),
                    duration_seconds=(
                        None
                        if component.get("duration_seconds") is None
                        else float(component["duration_seconds"])
                    ),
                    trigger_cooldown_seconds=(
                        None
                        if component.get("trigger_cooldown_seconds") is None
                        else float(component["trigger_cooldown_seconds"])
                    ),
                    stack_limit_count=int(component["stack_limit_count"]),
                )
                for component in row.get("components") or ()
            ),
            topple_limit=None if recovery is None else recovery["topple_limit"],
            topple_recovery_speed=(
                None if recovery is None else recovery["topple_recovery_speed"]
            ),
        )

    @staticmethod
    def apply_target_condition(
        config: BattleOuterRealmBuffConfig | None,
        condition: BattleTargetCondition | None,
    ) -> BattleOuterRealmBuffConfig | None:
        if config is None or condition is None:
            return config
        return replace(config, topple_limit=condition.enemy_topple_limit)

    @classmethod
    def infer(
        cls,
        config: BattleOuterRealmBuffConfig | None,
        *,
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
    ) -> tuple[BattleInferredBuffInterval, ...]:
        if config is None or battle_end_us <= 0:
            return ()
        intervals: list[BattleInferredBuffInterval] = []
        for component in config.components:
            if component.trigger_kind == "whole_battle":
                intervals.append(cls._whole_battle(config, component, battle_end_us))
            elif component.trigger_kind == "corruption_damage_stack":
                intervals.extend(cls._corruption_stacks(
                    config,
                    component,
                    hits,
                    battle_end_us,
                    time_stop_intervals,
                ))
            elif component.trigger_kind == "while_target_toppled":
                intervals.extend(cls._topple_windows(
                    config,
                    component,
                    hits,
                    battle_end_us,
                    time_stop_intervals,
                ))
        return tuple(sorted(intervals, key=lambda row: (
            row.start_us,
            row.end_us,
            row.interval_id,
        )))

    @staticmethod
    def _base_interval(
        config: BattleOuterRealmBuffConfig,
        component: BattleOuterRealmBuffComponent,
        *,
        interval_id: str,
        start_us: int,
        end_us: int,
        stacks: int,
        trigger_event_type: str,
        evidence_event_ids: tuple[str, ...],
        modifier: BattleBuffModifierEvidence,
        inference_basis: str,
    ) -> BattleInferredBuffInterval:
        return BattleInferredBuffInterval(
            interval_id=interval_id,
            buff_asset_path=config.gameplay_effect_path,
            buff_name=f"轨外之境 · {config.buff_name}",
            source_effect_definition_id=config.buff_id,
            source_kind="outer_realm_season_buff",
            source_character_id=0,
            source_character_name=f"轨外之境 · {config.season_name}",
            target_scope="team",
            start_us=start_us,
            end_us=end_us,
            stacks=stacks,
            duration_policy="outer_realm_season_rule",
            state_confidence="高",
            value_confidence="高",
            inference_basis=inference_basis,
            trigger_event_type=trigger_event_type,
            evidence_action_ids=(),
            evidence_event_ids=evidence_event_ids,
            modifiers=(modifier,),
            stacking_type=(
                "AggregateBySource+RefreshWholeStack"
                if component.stack_limit_count > 1
                else ""
            ),
            stack_limit_count=component.stack_limit_count,
        )

    @classmethod
    def _whole_battle(
        cls,
        config: BattleOuterRealmBuffConfig,
        component: BattleOuterRealmBuffComponent,
        battle_end_us: int,
    ) -> BattleInferredBuffInterval:
        return cls._base_interval(
            config,
            component,
            interval_id=f"outer:{config.level_config_id}:{component.component_ordinal}",
            start_us=0,
            end_us=battle_end_us,
            stacks=1,
            trigger_event_type="OUTER_REALM_WHOLE_BATTLE",
            evidence_event_ids=(),
            modifier=_modifier(component),
            inference_basis="正式轨外赛季配置声明整场生效，数值来自官方单值曲线。",
        )

    @classmethod
    def _corruption_stacks(
        cls,
        config: BattleOuterRealmBuffConfig,
        component: BattleOuterRealmBuffComponent,
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        duration_us = round(float(component.duration_seconds or 0.0) * 1_000_000)
        cooldown_us = round(float(component.trigger_cooldown_seconds or 0.0) * 1_000_000)
        if duration_us <= 0:
            return ()
        triggers = sorted(
            (
                hit for hit in hits
                if hit.direction == "outgoing"
                and hit.gameplay_effect_id.casefold().startswith("buff_reaction_5_new")
            ),
            key=lambda hit: (hit.relative_time_us, hit.sequence, hit.event_id),
        )
        results: list[BattleInferredBuffInterval] = []
        stack = 0
        last_trigger_active: int | None = None
        expires_active: int | None = None
        open_start: int | None = None
        open_evidence: tuple[str, ...] = ()

        def active(raw_us: int) -> int:
            return project_timeline_time_us(
                raw_us,
                battle_start_us=0,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
            )

        def raw(active_us: int) -> int:
            return unproject_timeline_time_us(
                active_us,
                battle_start_us=0,
                battle_end_us=battle_end_us,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
                prefer_interval_end=True,
            )

        def close(end_us: int) -> None:
            if open_start is None or stack <= 0 or end_us <= open_start:
                return
            results.append(cls._base_interval(
                config,
                component,
                interval_id=f"outer:{config.level_config_id}:stack:{len(results)}",
                start_us=open_start,
                end_us=end_us,
                stacks=stack,
                trigger_event_type="CORRUPTION_DAMAGE_AFTER_HIT",
                evidence_event_ids=open_evidence,
                modifier=_modifier(component),
                inference_basis=(
                    "按正式浊燃逐击在伤害结算后叠层；1 秒触发间隔、6 秒整组刷新"
                    "和最多 8 层均来自赛季说明，持续时间使用扣时停时钟。"
                ),
            ))

        for hit in triggers:
            now = active(hit.relative_time_us)
            if expires_active is not None and now >= expires_active:
                close(raw(expires_active))
                stack = 0
                last_trigger_active = None
                expires_active = None
                open_start = None
                open_evidence = ()
            if last_trigger_active is not None and now - last_trigger_active < cooldown_us:
                continue
            close(hit.relative_time_us + 1)
            stack = min(component.stack_limit_count, stack + 1)
            last_trigger_active = now
            expires_active = now + duration_us
            open_start = hit.relative_time_us + 1
            open_evidence = (hit.event_id,)
        if open_start is not None and expires_active is not None:
            close(min(battle_end_us, raw(expires_active)))
        return tuple(results)

    @classmethod
    def _topple_windows(
        cls,
        config: BattleOuterRealmBuffConfig,
        component: BattleOuterRealmBuffComponent,
        hits: Sequence[BattleAnalysisHit],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]],
    ) -> tuple[BattleInferredBuffInterval, ...]:
        limit = float(config.topple_limit or 0.0)
        speed = float(config.topple_recovery_speed or 0.0)
        if limit <= 0.0 or speed <= 0.0:
            return ()
        duration_us = round(limit / speed * 1_000_000)
        result = []
        for hit in hits:
            if (
                hit.direction != "outgoing"
                or hit.gameplay_effect_id.casefold() != "buff_tenacity_damage"
                or not hit.target_id
            ):
                continue
            start_us = hit.relative_time_us + 1
            start_active = project_timeline_time_us(
                hit.relative_time_us,
                battle_start_us=0,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
            )
            end_us = unproject_timeline_time_us(
                start_active + duration_us,
                battle_start_us=0,
                battle_end_us=battle_end_us,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
                prefer_interval_end=True,
            )
            end_us = min(battle_end_us, end_us)
            if end_us <= start_us:
                continue
            result.append(cls._base_interval(
                config,
                component,
                interval_id=f"outer:{config.level_config_id}:topple:{hit.event_id}",
                start_us=start_us,
                end_us=end_us,
                stacks=1,
                trigger_event_type="TARGET_TOPPLED",
                evidence_event_ids=(hit.event_id,),
                modifier=_modifier(
                    component,
                    requirement=f"{_TARGET_REQUIREMENT_PREFIX}{hit.target_id}",
                ),
                inference_basis=(
                    f"{hit.gameplay_effect_id} 证明目标进入倾陷；按该目标正式 "
                    f"UnbalMax={limit:g} ÷ UnbalReduceReset={speed:g}，"
                    "在扣时停时钟上重建倾陷恢复区间。"
                ),
            ))
        return tuple(result)
