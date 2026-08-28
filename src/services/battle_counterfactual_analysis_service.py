# 从正式逐击证据构建战报区间统计和角色反事实边际。
"""Qt-free battle-axis analysis and counterfactual marginal calculations."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.battle_report import (
    BattleAnalysisHit,
    BattleAnalysisSnapshot,
    BattleCharacterBaseline,
    BattleCharacterSourceStat,
    BattleCharacterStat,
    BattleInferredBuffInterval,
    BattleMaxHpReductionEvent,
    BattleRangeRoleSummary,
    BattleRangeSkillSummary,
    BattleTargetCondition,
)
from src.services.battle_action_inference_service import (
    ACTION_INFERENCE_MODEL_VERSION,
    BattleActionAnimationCandidate,
    BattleActionInferenceService,
)
from src.services.battle_buff_inference_service import (
    BUFF_INFERENCE_MODEL_VERSION,
    BattleBuffInferenceService,
    BattleStaticBuffRule,
)
from src.services.battle_buff_attribute_projection_service import (
    BUFF_ATTRIBUTE_PROJECTION_VERSION,
)
from src.services.battle_fadia_hp_stack_service import (
    BattleFadiaHpStackService,
    resolve_fadia_inherent_hp,
    resolve_fadia_source_max_hp,
)
from src.services.battle_half_buff_scope_service import BattleHalfBuffScopeService
from src.services.battle_target_ledger_service import BattleTargetLedgerService
from src.services.battle_time_stop_projection_service import (
    TIME_STOP_PROJECTION_MODEL_VERSION,
    BattleTimeStopProjectionService,
)
from src.services.battle_outer_realm_buff_service import (
    OUTER_REALM_BUFF_MODEL_VERSION,
    BattleOuterRealmBuffConfig,
    BattleOuterRealmBuffService,
)
from src.services.battle_environment_condition_service import (
    battle_witch_buff_interval,
    resolve_battle_target_condition,
)
from src.services.battle_zankou_form_buff_service import (
    BattleZankouFormBuffService,
    BattleZankouFormConfig,
)
from src.services.battle_timeline_projection_service import (
    TIMELINE_PROJECTION_MODEL_VERSION,
    BattleTimelineProjectionService,
)
from src.services.battle_target_vital_analysis_service import (
    TARGET_VITAL_MODEL_VERSION,
    BattleTargetVitalAnalysisService,
    battle_target_identity_mode,
    bind_confirmed_single_target,
    resolve_battle_target_identity,
)
from src.services.battle_single_target_damage_normalization_service import (
    BattleSingleTargetDamageNormalizationService,
)
from src.services.battle_fork_trigger_refinement_service import ForkCriticalEvent
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService,
)
from src.services.battle_treatment_replay_service import (
    TREATMENT_EVENT_MODEL_VERSION,
    BattleTreatmentReplayService,
)
from src.services.battle_daffodill_awakening_service import BattleDaffodillAwakeningService


FORMULA_MODEL_VERSION = "battle-counterfactual-v21"

_REACTION_MARKERS = ("创生", "黯星", "浊燃", "浸染", "盈蓄", "失谐", "延滞", "倾陷", "reaction", "topple")
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


def _split_hits(
    rows: Sequence[Mapping[str, Any]],
    *,
    origin_us: int | None = None,
) -> tuple[BattleAnalysisHit, ...]:
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
                    damage=primary_damage - (overkill_damage or 0.0),
                    is_follow_up=False,
                    raw_damage=(
                        primary_damage if overkill_damage is not None else None
                    ),
                    overkill_damage=overkill_damage,
                    damage_correction_kind=(
                        "nte_core_overkill_v3"
                        if overkill_damage is not None
                        else ""
                    ),
                    damage_correction_confidence=(
                        "高" if overkill_damage is not None else ""
                    ),
                    damage_correction_basis=(
                        "nte-core v3 权威 overkill_damage；仅从主伤害扣除，追击不扣。"
                        if overkill_damage is not None
                        else ""
                    ),
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
        if follow_up_damage > 0:
            follow_up_relative_us = relative_time_us
            follow_up_timestamp_us = row.get("follow_up_timestamp_unix_us")
            if follow_up_timestamp_us is None:
                follow_up_timestamp = row.get("follow_up_timestamp_unix")
                if isinstance(follow_up_timestamp, (int, float)):
                    follow_up_timestamp_us = round(float(follow_up_timestamp) * 1_000_000)
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


def _baselines(build: Mapping[str, Any] | None) -> tuple[BattleCharacterBaseline, ...]:
    if not build:
        return ()
    baselines = []
    fadia_inherent_hp = resolve_fadia_inherent_hp(build)
    fadia_source_max_hp = resolve_fadia_source_max_hp(build)
    enabled_team_passive_ids = tuple(
        row.definition.passive_id
        for row in BattleCharacterPassiveService.enabled_passives(build)
    )
    for character in build.get("characters") or ():
        resolved = [
            row
            for row in character.get("stats") or ()
            if str(row.get("source_group")) == "resolved"
        ]
        stats = tuple(
            BattleCharacterStat(
                property_id=str(row["property_id"]),
                label=str(row.get("display_name") or row["property_id"]),
                value=float(row.get("value") or 0.0),
                is_percent=bool(row.get("is_percent")),
            )
            for row in resolved
        )
        source_stats = tuple(
            BattleCharacterSourceStat(
                source_group=str(row.get("source_group") or "unknown"),
                source_name={
                    "character": "人物",
                    "fork": "弧盘",
                    "likeability": "好感度 10 级",
                    "world_bonus": "世界加成",
                    "equipment": "装备",
                    "battle_override": "边际手工调整",
                }.get(str(row.get("source_group") or ""), "其他"),
                property_id=str(row["property_id"]),
                label=str(row.get("display_name") or row["property_id"]),
                value=float(row.get("value") or 0.0),
                is_percent=bool(row.get("is_percent")),
            )
            for row in character.get("stats") or ()
            if str(row.get("source_group") or "") != "resolved"
        )
        baselines.append(
            BattleCharacterBaseline(
                character_id=int(character["character_id"]),
                character_name=_text(
                    character.get("observed_name"),
                    str(character["character_id"]),
                ),
                source=str(
                    character.get("stat_snapshot_source")
                    or ("frozen_v25" if stats else "build_without_resolved_stats")
                ),
                stats=stats,
                character_level=float(
                    character.get("character_level")
                    or (character.get("profile") or {}).get("character_level")
                    or 80.0
                ),
                source_stats=source_stats,
                inherent_hp=(
                    fadia_inherent_hp
                    if int(character["character_id"]) == 1039
                    else None
                ),
                source_max_hp=(
                    fadia_source_max_hp
                    if int(character["character_id"]) == 1039
                    else None
                ),
                enabled_team_passive_ids=enabled_team_passive_ids,
            )
        )
    return tuple(baselines)


class BattleCounterfactualAnalysisService:
    """Build one immutable range projection and calculate role margins."""

    @staticmethod
    def analyze(
        *,
        battle_record_id: int,
        evidence: Mapping[str, Any] | None,
        build: Mapping[str, Any] | None,
        capability_level: str,
        requested_start_us: int | None = None,
        requested_end_us: int | None = None,
        animation_candidates: Sequence[BattleActionAnimationCandidate] = (),
        buff_rules: Sequence[BattleStaticBuffRule] = (),
        target_condition: Mapping[str, Any] | BattleTargetCondition | None = None,
        zankou_form_config: BattleZankouFormConfig | None = None,
        outer_realm_buff_config: BattleOuterRealmBuffConfig | None = None,
        infer_buffs: bool = True,
        critical_events: Sequence[ForkCriticalEvent] = (),
        target_control_policy: str = "eligible_default",
    ) -> BattleAnalysisSnapshot:
        resolved_target_condition = resolve_battle_target_condition(target_condition)
        source_hits = (evidence or {}).get("hits") or ()
        raw_hits, confirmed_single_target = bind_confirmed_single_target(
            source_hits,
            resolved_target_condition,
        )
        origin_candidates = [
            int(row["timestamp_unix_us"]) - int(row["relative_time_us"])
            for row in raw_hits
            if row.get("timestamp_unix_us") is not None
            and row.get("relative_time_us") is not None
        ]
        origin_us = min(origin_candidates) if origin_candidates else None
        all_max_hp_events = BattleTargetVitalAnalysisService.derive(
            rows=raw_hits,
            build=build,
            structured_max_hp_reduction=bool(
                int((evidence or {}).get("contract_version") or 0) >= 4
                and (evidence or {}).get("axis_complete")
            ),
        )
        all_hits = BattleSingleTargetDamageNormalizationService.normalize(
            _split_hits(raw_hits, origin_us=origin_us),
            confirmed_single_target=confirmed_single_target,
        )
        all_estimated_max_hp_events = (
            BattleTargetVitalAnalysisService.estimate_from_descriptions(
                rows=raw_hits,
                build=build,
                observed_events=all_max_hp_events,
            )
        )
        observed_time_stop_intervals = (
            BattleTimeStopProjectionService.observed_intervals(
                (evidence or {}).get("time_stop_intervals") or (),
                origin_us=origin_us,
            )
        )
        inferred_actions = BattleActionInferenceService.infer(
            all_hits,
            time_stop_intervals=observed_time_stop_intervals,
            animation_candidates=animation_candidates,
        )
        time_stop_projection = BattleTimeStopProjectionService.resolve(
            observed_time_stop_intervals,
            inferred_actions,
        )
        intervals = time_stop_projection.intervals
        inferred_inputs = BattleTimelineProjectionService.infer_inputs(
            inferred_actions
        )
        timeline_damage_groups = tuple(
            sorted(
                (
                    *BattleTimelineProjectionService.group_damage_hits(all_hits),
                    *BattleTargetVitalAnalysisService.timeline_groups(
                        all_max_hp_events
                    ),
                    *BattleTargetVitalAnalysisService.timeline_groups(
                        all_estimated_max_hp_events
                    ),
                ),
                key=lambda row: (row.start_us, row.end_us, row.group_id),
            )
        )
        maximum = max(
            (
                *(hit.relative_time_us for hit in all_hits),
                *(int(row.get("relative_time_us") or 0) for row in raw_hits),
                *(event.observed_at_us for event in all_max_hp_events),
                *(event.observed_at_us for event in all_estimated_max_hp_events),
            ),
            default=0,
        ) + 1
        zankou_form_intervals = (
            BattleZankouFormBuffService.infer(
                build=build,
                actions=inferred_actions,
                hits=all_hits,
                battle_end_us=maximum,
                config=zankou_form_config,
                time_stop_intervals=intervals,
            )
            if infer_buffs
            else ()
        )
        treatment_projection = BattleTreatmentReplayService.infer(
            build=build,
            actions=inferred_actions,
            hits=all_hits,
            battle_end_us=maximum,
            time_stop_intervals=intervals,
            state_buff_intervals=zankou_form_intervals,
            zankou_effect_three_recover_ratio=(
                zankou_form_config.effect_three_recover_ratio
                if zankou_form_config is not None
                else None
            ),
            infer_buffs=infer_buffs,
        )
        treatment_events = treatment_projection.events
        buff_intervals: tuple[BattleInferredBuffInterval, ...] = ()
        if infer_buffs:
            buff_intervals = BattleBuffInferenceService.infer(
                buff_rules,
                actions=inferred_actions,
                hits=all_hits,
                battle_end_us=maximum,
                time_stop_intervals=intervals,
                treatment_events=treatment_events,
                critical_events=critical_events,
                target_control_policy=target_control_policy,
            )
            buff_intervals = tuple((
                *buff_intervals,
                *treatment_projection.buff_intervals,
                *BattleFadiaHpStackService.infer(
                    build=build,
                    hits=all_hits,
                    battle_end_us=maximum,
                    max_hp_events=all_max_hp_events,
                ),
                *zankou_form_intervals,
                *BattleDaffodillAwakeningService.infer(
                    build=build,
                    actions=inferred_actions,
                    hits=all_hits,
                    battle_end_us=maximum,
                    time_stop_intervals=intervals,
                    topple_duration_us=BattleDaffodillAwakeningService.reliable_topple_duration_us(
                        outer_realm_buff_config
                    ),
                ),
                *BattleOuterRealmBuffService.infer(
                    BattleOuterRealmBuffService.apply_target_condition(
                        outer_realm_buff_config,
                        resolved_target_condition,
                    ),
                    hits=all_hits,
                    battle_end_us=maximum,
                    time_stop_intervals=intervals,
                ),
            ))
            witch_interval = battle_witch_buff_interval(
                resolved_target_condition,
                maximum,
            )
            if witch_interval is not None:
                buff_intervals = tuple((*buff_intervals, witch_interval))
            buff_intervals = BattleHalfBuffScopeService.scope(
                buff_intervals,
                raw_hits=raw_hits,
                battle_end_us=maximum,
            )
        timeline_end_us = max(
            maximum,
            max((action.end_us for action in inferred_actions), default=maximum),
            max(
                (group.end_us for group in timeline_damage_groups),
                default=maximum,
            ),
            max((row.end_us for row in buff_intervals), default=maximum),
        )
        start_us = min(maximum - 1, max(0, int(requested_start_us or 0)))
        requested_end = maximum if requested_end_us is None else int(requested_end_us)
        end_us = min(maximum, max(start_us + 1, requested_end))
        hits = tuple(hit for hit in all_hits if start_us <= hit.relative_time_us < end_us)
        max_hp_events = tuple(
            event
            for event in all_max_hp_events
            if start_us <= event.observed_at_us < end_us
        )
        estimated_max_hp_events = tuple(
            event
            for event in all_estimated_max_hp_events
            if start_us <= event.observed_at_us < end_us
        )
        selected_buff_intervals = tuple(
            row for row in buff_intervals
            if row.start_us < end_us and row.end_us > start_us
        )
        outgoing = tuple(hit for hit in hits if hit.direction == "outgoing")
        raw_total_damage = sum(
            float(hit.raw_damage if hit.raw_damage is not None else hit.damage)
            for hit in outgoing
        )
        duration = max((end_us - start_us) / 1_000_000.0, 0.001)
        total_damage = sum(hit.damage for hit in outgoing)
        damage_correction_total = sum(
            max(0.0, float(hit.raw_damage or hit.damage) - hit.damage)
            for hit in outgoing
        )
        damage_overlap_correction_total = sum(
            max(0.0, hit.damage_overlap_correction)
            for hit in outgoing
        )
        max_hp_reduction_damage = sum(
            event.effective_hp_loss for event in max_hp_events
        )
        effective_damage = total_damage + max_hp_reduction_damage
        role_groups: dict[tuple[int, str], list[BattleAnalysisHit]] = defaultdict(list)
        role_vital_groups: dict[
            tuple[int, str], list[BattleMaxHpReductionEvent]
        ] = defaultdict(list)
        skill_groups: dict[
            tuple[int | None, str, str, str, str, str],
            list[BattleAnalysisHit | BattleMaxHpReductionEvent],
        ] = defaultdict(list)
        for hit in outgoing:
            if hit.character_id is not None:
                role_groups[(hit.character_id, hit.character_name)].append(hit)
            skill_groups[
                (
                    hit.character_id,
                    hit.character_name,
                    hit.skill_name,
                    hit.damage_name,
                    hit.classification,
                    hit.ability_id,
                )
            ].append(hit)
        for event in max_hp_events:
            if event.source_character_id is not None:
                role_vital_groups[
                    (event.source_character_id, event.source_character_name)
                ].append(event)
            skill_groups[
                (
                    event.source_character_id,
                    event.source_character_name,
                    event.source_skill_name or event.mechanic_name,
                    event.mechanic_name,
                    "max_hp_reduction",
                    event.mechanic_kind,
                )
            ].append(event)
        roles = tuple(
            BattleRangeRoleSummary(
                character_id=character_id,
                character_name=name,
                hits=len(role_groups.get((character_id, name), ())),
                damage=(
                    sum(row.damage for row in role_groups.get((character_id, name), ()))
                    + sum(
                        row.effective_hp_loss
                        for row in role_vital_groups.get((character_id, name), ())
                    )
                ),
                dps=(
                    sum(row.damage for row in role_groups.get((character_id, name), ()))
                    + sum(
                        row.effective_hp_loss
                        for row in role_vital_groups.get((character_id, name), ())
                    )
                )
                / duration,
                share_percent=(
                    (
                        sum(row.damage for row in role_groups.get((character_id, name), ()))
                        + sum(
                            row.effective_hp_loss
                            for row in role_vital_groups.get((character_id, name), ())
                        )
                    )
                    / effective_damage
                    * 100.0
                    if effective_damage
                    else 0.0
                ),
                raw_damage=sum(
                    row.damage for row in role_groups.get((character_id, name), ())
                ),
                max_hp_reduction_damage=sum(
                    row.effective_hp_loss
                    for row in role_vital_groups.get((character_id, name), ())
                ),
                max_hp_reduction_events=len(
                    role_vital_groups.get((character_id, name), ())
                ),
            )
            for character_id, name in sorted(
                role_groups.keys() | role_vital_groups.keys(),
                key=lambda key: (
                    sum(row.damage for row in role_groups.get(key, ()))
                    + sum(
                        row.effective_hp_loss
                        for row in role_vital_groups.get(key, ())
                    )
                ),
                reverse=True,
            )
        )
        skills = tuple(
            BattleRangeSkillSummary(
                character_id=key[0],
                character_name=key[1],
                skill_name=key[2],
                damage_name=key[3],
                classification=key[4],
                hits=len(rows),
                damage=sum(
                    row.effective_hp_loss
                    if isinstance(row, BattleMaxHpReductionEvent)
                    else row.damage
                    for row in rows
                ),
                share_percent=(
                    sum(
                        row.effective_hp_loss
                        if isinstance(row, BattleMaxHpReductionEvent)
                        else row.damage
                        for row in rows
                    )
                    / effective_damage
                    * 100.0
                    if effective_damage
                    else 0.0
                ),
                ability_id=key[5],
            )
            for key, rows in sorted(
                skill_groups.items(),
                key=lambda item: sum(
                    row.effective_hp_loss
                    if isinstance(row, BattleMaxHpReductionEvent)
                    else row.damage
                    for row in item[1]
                ),
                reverse=True,
            )
        )
        return BattleAnalysisSnapshot(
            battle_record_id=battle_record_id,
            capability_level=capability_level,
            axis_complete=bool((evidence or {}).get("axis_complete", False)),
            formula_model_version=FORMULA_MODEL_VERSION,
            name_mapping_version=str(
                (build or {}).get("name_mapping_version")
                or "legacy-current-mapping"
            ),
            action_inference_version=ACTION_INFERENCE_MODEL_VERSION,
            timeline_projection_version=TIMELINE_PROJECTION_MODEL_VERSION,
            battle_start_us=0,
            battle_end_us=maximum,
            timeline_end_us=timeline_end_us,
            range_start_us=start_us,
            range_end_us=end_us,
            duration_seconds=duration,
            total_damage=total_damage,
            total_dps=total_damage / duration,
            raw_total_damage=raw_total_damage,
            timeline_hits=all_hits,
            inferred_actions=inferred_actions,
            inferred_inputs=inferred_inputs,
            timeline_damage_groups=timeline_damage_groups,
            treatment_events=treatment_events,
            treatment_event_model_version=TREATMENT_EVENT_MODEL_VERSION,
            timeline_buff_intervals=buff_intervals,
            hits=hits,
            roles=roles,
            skills=skills,
            targets=BattleTargetLedgerService.summarize(
                hits,
                max_hp_events,
                estimated_max_hp_events,
            ),
            baselines=_baselines(build),
            buff_intervals=selected_buff_intervals,
            buff_inference_version=(
                BUFF_INFERENCE_MODEL_VERSION if infer_buffs else ""
            ),
            buff_attribute_projection_version=(
                BUFF_ATTRIBUTE_PROJECTION_VERSION if infer_buffs else ""
            ),
            outer_realm_buff_model_version=(
                OUTER_REALM_BUFF_MODEL_VERSION
                if infer_buffs and outer_realm_buff_config is not None
                else ""
            ),
            time_stop_intervals=intervals,
            observed_time_stop_intervals=observed_time_stop_intervals,
            time_stop_source_kind=time_stop_projection.source_kind,
            time_stop_confidence=time_stop_projection.confidence,
            time_stop_inference_basis=time_stop_projection.inference_basis,
            time_stop_projection_version=TIME_STOP_PROJECTION_MODEL_VERSION,
            timeline_max_hp_events=all_max_hp_events,
            max_hp_events=max_hp_events,
            max_hp_reduction_damage=max_hp_reduction_damage,
            effective_damage=effective_damage,
            effective_dps=effective_damage / duration,
            target_vital_model_version=TARGET_VITAL_MODEL_VERSION,
            target_identity_mode=(
                "user_confirmed_single_target"
                if confirmed_single_target
                else battle_target_identity_mode(source_hits)
            ),
            timeline_estimated_max_hp_events=all_estimated_max_hp_events,
            estimated_max_hp_events=estimated_max_hp_events,
            estimated_max_hp_reduction_damage=sum(
                event.effective_hp_loss for event in estimated_max_hp_events
            ),
            target_condition=resolved_target_condition,
            damage_correction_total=damage_correction_total,
            timeline_damage_correction_total=sum(
                max(0.0, float(hit.raw_damage or hit.damage) - hit.damage)
                for hit in all_hits
                if hit.direction == "outgoing"
            ),
            damage_overlap_correction_total=damage_overlap_correction_total,
            timeline_damage_overlap_correction_total=sum(
                max(0.0, hit.damage_overlap_correction)
                for hit in all_hits
                if hit.direction == "outgoing"
            ),
        )
