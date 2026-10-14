# 残虹形态与蓄焰分别重放固定原轴，保留 Q 消费资格、停时和证据身份。
from __future__ import annotations
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from src.services.battle_report.native.state_payload import HIT_FIELDS, ACTION_FIELDS, state_rows, ZANKOU_CONFIG_FIELDS, state_row
from src.domain.battle_report.analysis.backend_contracts import BattleComputeBackend
from src.services.battle_report.native.hit_state import compute_hit_state, restore_q_final_states
from typing import Any
from src.domain.battle_report.timeline.events import BattleAnalysisHit, BattleInferredAction
from src.domain.battle_report.analysis.snapshot import BattleAnalysisSnapshot
from src.domain.battle_report.buffs.models import BattleBuffModifierEvidence, BattleInferredBuffInterval
from src.services.battle_report.damage.composition import explicit_reaction_channel_for_hit
from src.services.battle_report.characters.state_compute import compute_character_state, state_ids, state_ranges
from src.services.battle_report.timeline.clock import ACTIVE_TIME_MODE, project_timeline_time_us, unproject_timeline_time_us


_ZANKOU_ID = 1036


_CHARGE_FINAL_MULTIPLIER = 2.5


@dataclass(frozen=True, slots=True)
class ZankouQFinalDamageEvidence:
    event_id: str
    multiplier: float
    evidence_basis: str


def _selected_effects(character: Mapping[str, Any]) -> frozenset[str]:
    profile = character.get("profile")
    profile = profile if isinstance(profile, Mapping) else {}
    if bool(profile.get("awakening_selection_initialized")):
        return frozenset(
            str(value)
            for value in profile.get("selected_awaken_effect_ids") or ()
        )
    try:
        level = int(
            profile.get("awakening_level")
            or character.get("awakening_level")
            or 0
        )
    except (TypeError, ValueError):
        level = 0
    return frozenset(f"Effect{ordinal}" for ordinal in range(1, level + 1))


def _is_charge_trigger(hit: BattleAnalysisHit) -> bool:
    if hit.direction != "outgoing":
        return False
    if hit.classification == "weave" and hit.character_id == _ZANKOU_ID:
        return True
    return explicit_reaction_channel_for_hit(hit) == (
        "reaction_scorch",
        "浊燃",
    )


def _q_branch(hit: BattleAnalysisHit) -> str:
    if hit.character_id != _ZANKOU_ID or hit.classification != "direct":
        return ""
    identity = "|".join((hit.ability_id, hit.gameplay_effect_id)).casefold()
    if "zankou" not in identity or "ultraskill" not in identity:
        return ""
    if "magicultraskill" in identity:
        return "magic"
    return "force"


def _zankou_q_actions(
    analysis: BattleAnalysisSnapshot,
) -> tuple[BattleInferredAction, ...]:
    return tuple(
        sorted(
            (
                action
                for action in getattr(analysis, "inferred_actions", ())
                if action.character_id == _ZANKOU_ID and action.input_kind == "Q"
            ),
            key=lambda action: (action.start_us, action.action_id),
        )
    )


def reconstruct_zankou_q_final_damage(
    analysis: BattleAnalysisSnapshot,
    character: Mapping[str, Any] | None,
    *, compute_backend: BattleComputeBackend | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> dict[str, ZankouQFinalDamageEvidence]:
    """Return the independently consumed +150% final multiplier for Q hits."""

    if character is None or int(character.get("character_id") or 0) != _ZANKOU_ID:
        return {}
    selected = _selected_effects(character)
    if "Effect2" not in selected:
        return {}
    if isinstance(compute_backend, BattleComputeBackend) and compute_backend.supports_battle_compute:
        return restore_q_final_states(compute_hit_state("zankou_q", {
            "hits": state_rows(analysis.hits, HIT_FIELDS),
            "actions": state_rows(getattr(analysis, "inferred_actions", ()), ACTION_FIELDS),
            "effect_four_enabled": "Effect4" in selected,
        }, backend=compute_backend, checkpoint=checkpoint))

    hits_by_event = {hit.event_id: hit for hit in analysis.hits}
    triggers = tuple(
        sorted(
            (hit for hit in analysis.hits if _is_charge_trigger(hit)),
            key=lambda hit: (hit.relative_time_us, hit.sequence, hit.event_id),
        )
    )
    actions = _zankou_q_actions(analysis)
    if not actions:
        return {}

    trigger_index = 0
    force_ready = False
    magic_ready = False
    results: dict[str, ZankouQFinalDamageEvidence] = {}
    for action in actions:
        while (
            trigger_index < len(triggers)
            and triggers[trigger_index].relative_time_us < action.start_us
        ):
            force_ready = True
            if "Effect4" in selected:
                magic_ready = True
            trigger_index += 1

        branches: dict[str, list[BattleAnalysisHit]] = {"magic": [], "force": []}
        for event_id in action.evidence_event_ids:
            hit = hits_by_event.get(event_id)
            if hit is not None and (branch := _q_branch(hit)):
                branches[branch].append(hit)

        for branch, branch_hits in branches.items():
            ready = magic_ready if branch == "magic" else force_ready
            if not ready or not branch_hits:
                continue
            branch_name = "血宴入梦时" if branch == "magic" else "焚天烬灭舞"
            basis = (
                "觉醒二「渊底之吻」蓄焰：覆纹/浊燃触发后，"
                f"本次{branch_name}独立最终伤害 +150%，即 ×2.5"
                + (
                    "；觉醒四「梦魇生花」使血宴分支独立持有并消费资格"
                    if branch == "magic"
                    else ""
                )
            )
            for hit in branch_hits:
                results[hit.event_id] = ZankouQFinalDamageEvidence(
                    event_id=hit.event_id,
                    multiplier=_CHARGE_FINAL_MULTIPLIER,
                    evidence_basis=basis,
                )
            if branch == "magic":
                magic_ready = False
            else:
                force_ready = False

    return results


ZANKOU_FORM_BUFF_MODEL_VERSION = "battle-zankou-form-buff-v3"


_ZANKOU_CHARACTER_ID = 1036


_CURVE_TABLE = "/Game/DataTable/Skill/GlobalCharacterData/DT_ZankouEffectFigure"


_REALITY_TO_FANTASY_EFFECTS = (
    "ge_player_zankou_skill1_",
    "ge_player_zankou_skill2_",
)


_FANTASY_TO_REALITY_EFFECTS = (
    "ge_player_zankou_skill3_",
    "ge_player_zankou_skill4_",
)


_FANTASY_ACTION_MARKERS = (
    "ge_player_zankou_magicmelee",
    "ge_player_zankou_magicbranch",
    "ge_player_zankou_magicultraskill",
)


_REALITY_ACTION_MARKERS = (
    "ge_player_zankou_melee",
    "ge_player_zankou_branch",
    "ge_player_zankou_forceultraskill",
)


@dataclass(frozen=True, slots=True)
class BattleZankouFormConfig:
    shou_damage_up: float
    huo_dot_crit_damage_up: float
    fantasy_duration_seconds: float
    reality_to_fantasy_retention_seconds: float
    fantasy_to_reality_retention_seconds: float
    awakened_shou_damage_up: float = 0.40
    awakened_huo_dot_crit_damage_up: float = 0.50
    effect_three_recover_ratio: float = 0.04


def _single_curve_value(static_dao: Any, curve_id: str) -> float:
    curve = static_dao.get_combat_curve(_CURVE_TABLE, curve_id)
    points = tuple((curve or {}).get("points") or ())
    if len(points) != 1 or not isinstance(points[0].get("value"), (int, float)):
        raise ValueError(f"残虹形态曲线 {curve_id} 不是单值静态证据")
    return float(points[0]["value"])


def _selected_zankou(build: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    for row in (build or {}).get("characters") or ():
        if int(row.get("character_id") or 0) == _ZANKOU_CHARACTER_ID:
            return row
    return None


def _effect_enabled(character: Mapping[str, Any], effect_id: str) -> bool:
    profile = character.get("profile") or {}
    selected = {
        str(value).casefold()
        for value in (
            profile.get("selected_awaken_effect_ids")
            if isinstance(profile, Mapping)
            else ()
        ) or ()
    }
    if bool(
        profile.get("awakening_selection_initialized")
        if isinstance(profile, Mapping)
        else False
    ):
        return effect_id.casefold() in selected
    ordinal = int(effect_id.removeprefix("Effect") or 0)
    return int(
        character.get("awakening_level")
        or (profile.get("awakening_level") if isinstance(profile, Mapping) else 0)
        or 0
    ) >= ordinal


def _has_marker(action: BattleInferredAction, markers: Sequence[str]) -> bool:
    return any(
        marker in effect.casefold()
        for effect in action.gameplay_effect_ids
        for marker in markers
    )


def _merge_ranges(
    ranges: Sequence[tuple[int, int]],
    *,
    maximum: int,
) -> tuple[tuple[int, int], ...]:
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        start = min(maximum, max(0, int(start)))
        end = min(maximum, max(start, int(end)))
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return tuple((row[0], row[1]) for row in merged)


class BattleZankouFormBuffService:
    """Infer deletable zero-awakening Shou/Huo intervals from action evidence."""

    @staticmethod
    def load_config(static_dao: Any) -> BattleZankouFormConfig:
        return BattleZankouFormConfig(
            shou_damage_up=_single_curve_value(static_dao, "ZankouRealDamUp"),
            huo_dot_crit_damage_up=_single_curve_value(
                static_dao,
                "ZankouMagicCritDamageUp",
            ),
            fantasy_duration_seconds=_single_curve_value(
                static_dao,
                "ZankouMagicDur",
            ),
            reality_to_fantasy_retention_seconds=_single_curve_value(
                static_dao,
                "ZankouRtoMRetainBuffDur",
            ),
            fantasy_to_reality_retention_seconds=_single_curve_value(
                static_dao,
                "ZankouMtoRRetainBuffDur",
            ),
            awakened_shou_damage_up=_single_curve_value(
                static_dao,
                "ZankouRealDamUpLv1",
            ),
            awakened_huo_dot_crit_damage_up=_single_curve_value(
                static_dao,
                "ZankouMagicCritDamageUpLv1",
            ),
            effect_three_recover_ratio=_single_curve_value(
                static_dao,
                "ZankouRecoverMultLv3",
            ),
        )

    @classmethod
    def infer(
        cls,
        *,
        build: Mapping[str, Any] | None,
        actions: Sequence[BattleInferredAction],
        hits: Sequence[BattleAnalysisHit] = (),
        battle_end_us: int,
        config: BattleZankouFormConfig | None,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
        compute_backend: BattleComputeBackend | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        character = _selected_zankou(build)
        if character is None or config is None:
            return ()
        if battle_end_us <= 0:
            return ()

        effect_one_enabled = _effect_enabled(character, "Effect1")
        native = compute_character_state("zankou", {
            "actions": state_rows(actions, ACTION_FIELDS),
            "hits": state_rows(hits, HIT_FIELDS), "battle_end_us": battle_end_us,
            "time_stop_intervals": list(time_stop_intervals), "config": state_row(config, ZANKOU_CONFIG_FIELDS),
            "effect_one_enabled": effect_one_enabled,
        }, backend=compute_backend, checkpoint=checkpoint) if (
            isinstance(compute_backend, BattleComputeBackend) and compute_backend.supports_battle_compute
        ) else None
        if native is None:
            def active_time(wall_time_us: int) -> int:
                return project_timeline_time_us(
                    wall_time_us,
                    battle_start_us=0,
                    intervals=time_stop_intervals,
                    mode=ACTIVE_TIME_MODE,
                )

            active_end = active_time(battle_end_us)
            hit_times = {hit.event_id: hit.relative_time_us for hit in hits}

            def transition_time(
                action: BattleInferredAction,
                fallback_wall_us: int,
            ) -> int:
                evidence_times = tuple(
                    hit_times[event_id]
                    for event_id in action.evidence_event_ids
                    if event_id in hit_times
                )
                return active_time(max(evidence_times, default=fallback_wall_us))

            effect_one_enabled = _effect_enabled(character, "Effect1")
            fantasy_duration = round(config.fantasy_duration_seconds * 1_000_000)
            r_to_m_retention = round(
                config.reality_to_fantasy_retention_seconds * 1_000_000
            )
            m_to_r_retention = round(
                config.fantasy_to_reality_retention_seconds * 1_000_000
            )
            fantasy_ranges: list[tuple[int, int]] = []
            fantasy_start: int | None = None
            fantasy_expiry: int | None = None
            previous_character_id: int | None = None
            evidence_action_ids: list[str] = []

            def begin_fantasy(at_us: int) -> None:
                nonlocal fantasy_start, fantasy_expiry
                if fantasy_start is None:
                    fantasy_start = at_us
                fantasy_expiry = max(
                    fantasy_expiry or at_us,
                    at_us + fantasy_duration,
                )

            def end_fantasy(at_us: int) -> None:
                nonlocal fantasy_start, fantasy_expiry
                if fantasy_start is not None and at_us > fantasy_start:
                    fantasy_ranges.append((fantasy_start, at_us))
                fantasy_start = None
                fantasy_expiry = None

            if effect_one_enabled:
                shou = huo = ((0, active_end),)
            else:
                ordered = sorted(actions, key=lambda row: (row.start_us, row.action_id))
                for action in ordered:
                    start = active_time(action.start_us)
                    if fantasy_expiry is not None and fantasy_expiry <= start:
                        end_fantasy(fantasy_expiry)
                    if (
                        previous_character_id == _ZANKOU_CHARACTER_ID
                        and action.character_id != _ZANKOU_CHARACTER_ID
                    ):
                        end_fantasy(start)
                    previous_character_id = action.character_id
                    if action.character_id != _ZANKOU_CHARACTER_ID:
                        continue
                    evidence_action_ids.append(action.action_id)
                    if _has_marker(action, _FANTASY_ACTION_MARKERS):
                        begin_fantasy(start)
                    elif _has_marker(action, _REALITY_ACTION_MARKERS):
                        end_fantasy(start)

                    if _has_marker(action, _REALITY_TO_FANTASY_EFFECTS):
                        begin_fantasy(transition_time(action, action.end_us))
                    elif _has_marker(action, _FANTASY_TO_REALITY_EFFECTS):
                        end_fantasy(transition_time(action, action.end_us))
                    elif action.input_kind == "Q" and fantasy_start is not None:
                        end_fantasy(transition_time(action, action.end_us))

                if fantasy_start is not None:
                    end_fantasy(min(active_end, fantasy_expiry or active_end))
                fantasy = _merge_ranges(fantasy_ranges, maximum=active_end)
                reality: list[tuple[int, int]] = []
                cursor = 0
                for start, end in fantasy:
                    if start > cursor:
                        reality.append((cursor, start))
                    cursor = max(cursor, end)
                if cursor < active_end:
                    reality.append((cursor, active_end))
                shou = _merge_ranges(
                    tuple((start, end + r_to_m_retention) for start, end in reality),
                    maximum=active_end,
                )
                huo = _merge_ranges(
                    tuple((start, end + m_to_r_retention) for start, end in fantasy),
                    maximum=active_end,
                )

        else:
            shou, huo = state_ranges(native, "shou"), state_ranges(native, "huo")
            evidence_action_ids = list(state_ids(native, "evidence_action_ids"))

        def wall_time(active_us: int, *, prefer_end: bool) -> int:
            if native is not None:
                return active_us
            return unproject_timeline_time_us(
                active_us,
                battle_start_us=0,
                battle_end_us=battle_end_us,
                intervals=time_stop_intervals,
                mode=ACTIVE_TIME_MODE,
                prefer_interval_end=prefer_end,
            )

        character_name = str(character.get("observed_name") or "残虹")
        evidence_ids = tuple(dict.fromkeys(evidence_action_ids))
        intervals: list[BattleInferredBuffInterval] = []
        form_label = "觉醒一" if effect_one_enabled else "零觉"
        source_definition_id = (
            "character_awaken:1036:Effect1"
            if effect_one_enabled
            else "character-form:1036"
        )
        duration_policy = (
            "InfiniteUntilDeath"
            if effect_one_enabled
            else "FormStateWithRetention"
        )
        definitions = (
            (
                "shou",
                shou,
                "/Game/Blueprints/Abilities/Player/Ability_036_Zankou/Buff/Buff_Zankou_RealDamUp",
                "狩",
                "self",
                "DamageUpGeneralBase",
                (
                    config.awakened_shou_damage_up
                    if effect_one_enabled
                    else config.shou_damage_up
                ),
                "",
            ),
            (
                "huo",
                huo,
                "/Game/Blueprints/Abilities/Player/Ability_036_Zankou/Buff/Buff_Zankou_MagicDotDamCritUp",
                "惑",
                "team",
                "CritDamageBase",
                (
                    config.awakened_huo_dot_crit_damage_up
                    if effect_one_enabled
                    else config.huo_dot_crit_damage_up
                ),
                "battle-channel:continuous-damage",
            ),
        )
        for kind, ranges, asset_path, name, scope, property_id, value, requirement in definitions:
            for ordinal, (start, end) in enumerate(ranges):
                start_wall = wall_time(start, prefer_end=False)
                end_wall = wall_time(end, prefer_end=True)
                if end_wall <= start_wall:
                    continue
                intervals.append(BattleInferredBuffInterval(
                    interval_id=f"buff:zankou-form:{kind}:{ordinal}",
                    buff_asset_path=asset_path,
                    buff_name=f"{name}（{form_label}）",
                    source_effect_definition_id=f"{source_definition_id}:{kind}",
                    source_kind="confirmed_character_form",
                    source_character_id=_ZANKOU_CHARACTER_ID,
                    source_character_name=character_name,
                    target_scope=scope,
                    start_us=start_wall,
                    end_us=end_wall,
                    stacks=1,
                    duration_policy=duration_policy,
                    state_confidence="中",
                    value_confidence="高",
                    inference_basis=(
                        (
                            "冻结觉醒选择确认一觉生效；狩与惑从战斗开始常驻，"
                            "切形态或离场不移除；当前轴没有残虹阵亡事实，故投影至"
                            "战斗结束。"
                            if effect_one_enabled
                            else "冻结觉醒选择确认未启用一觉；形态由绯影闪、离魂错、"
                            "Q、角色切换与现实/幻境伤害项推算；技能切形态优先采用"
                            "动作证据中的最后一击时点，持续时间按静态曲线并扣除时停。"
                        )
                    ),
                    trigger_event_type="INFERRED_ZANKOU_FORM_STATE",
                    evidence_action_ids=evidence_ids,
                    evidence_event_ids=(),
                    modifiers=(BattleBuffModifierEvidence(
                        property_id=property_id,
                        modifier_operation="EGameplayModOp::Additive",
                        magnitude_kind="confirmed_static_curve",
                        magnitude_value=value,
                        calculation_asset_path="",
                        value_confidence="高",
                        application_requirement_asset_path=requirement,
                    ),),
                    stacking_type="AggregateByTarget",
                    stack_limit_count=1,
                ))
        return tuple(sorted(intervals, key=lambda row: (row.start_us, row.interval_id)))
