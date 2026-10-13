# 真红升腾和凝视各按正式曲线与原轴证据推断，未知与弱置信状态不互相补齐。
from __future__ import annotations
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from math import isfinite
from typing import Any
from src.services.battle_report.native.state_payload import HIT_FIELDS, state_rows, state_row
from src.domain.native_analysis import BattleComputeBackend
from src.services.battle_report.characters.state_compute import compute_character_state
from src.domain.battle_report import BattleAnalysisHit, BattleBuffModifierEvidence, BattleInferredBuffInterval, BattleAnalysisSnapshot, BattleSkillDamageEvidence
from src.services.battle_report.characters.awakening import SHINKU_RAGE_DAMAGE_IDS, SHINKU_RAGE_REQUIREMENT
from src.services.official_role.awakening import active_awaken_effects
from src.services.battle_report.timeline.clock import ACTIVE_TIME_MODE, projected_range_duration_us


_CURVE_TABLE = "/Game/DataTable/Skill/GlobalCharacterData/DT_ShinkuEffectFigure"


_BUFF_ASSET = (
    "/Game/Blueprints/Abilities/Player/Ability_076_Shinku/Buff/Buff_Shinku_Rage"
)


_CALC_ASSET = (
    "/Game/Blueprints/Abilities/Calculation/Shinku/Calc_Shinku_RageDmgUp"
)


@dataclass(frozen=True, slots=True)
class BattleShinkuRageConfig:
    damage_up: float
    resonance_damage_up: float
    awakenings: tuple[Mapping[str, Any], ...]


def _curve_value(static_dao: Any, curve_id: str) -> float:
    points = tuple((static_dao.get_combat_curve(_CURVE_TABLE, curve_id) or {}).get(
        "points"
    ) or ())
    if len(points) != 1 or not isinstance(points[0].get("value"), (int, float)):
        raise ValueError(f"真红升腾曲线 {curve_id} 缺少唯一数值证据")
    value = float(points[0]["value"])
    if not isfinite(value) or value < 0.0:
        raise ValueError(f"真红升腾曲线 {curve_id} 数值无效")
    return value


class BattleShinkuRageBuffService:
    @staticmethod
    def load_config(static_dao: Any) -> BattleShinkuRageConfig:
        return BattleShinkuRageConfig(
            damage_up=_curve_value(static_dao, "Shinku_Rage_DmgUp"),
            resonance_damage_up=_curve_value(static_dao, "Shinku_Rage_DmgUpEx_L3"),
            awakenings=tuple(static_dao.list_character_awaken_effects(1076)),
        )

    @staticmethod
    def infer(
        *,
        build: Mapping[str, Any] | None,
        hits: Sequence[BattleAnalysisHit],
        config: BattleShinkuRageConfig | None,
        compute_backend: BattleComputeBackend | None = None,
        checkpoint: Callable[[], None] | None = None,
    ) -> tuple[BattleInferredBuffInterval, ...]:
        character = next((
            row for row in (build or {}).get("characters") or ()
            if int(row.get("character_id") or 0) == 1076
        ), None)
        if character is None:
            return ()
        profile = dict(character.get("profile") or {})
        profile.setdefault("awakening_level", int(character.get("awakening_level") or 0))
        resonance = config is not None and any(
            row.get("effect_id") == "resonance_3"
            for row in active_awaken_effects(profile, config.awakenings)
        )
        native = compute_character_state("shinku", {
            "hits": state_rows(hits, HIT_FIELDS), "resonance": resonance,
            "config": state_row(config, ("damage_up", "resonance_damage_up")) if config is not None else None,
            "damage_ids": sorted(SHINKU_RAGE_DAMAGE_IDS),
        }, backend=compute_backend, checkpoint=checkpoint) if (
            isinstance(compute_backend, BattleComputeBackend) and compute_backend.supports_battle_compute
        ) else None
        grouped: dict[int, list[str]] = {}
        if native is None:
            for hit in hits:
                if (
                    hit.character_id == 1076
                    and hit.direction == "outgoing"
                    and hit.gameplay_effect_id.casefold() in SHINKU_RAGE_DAMAGE_IDS
                ):
                    grouped.setdefault(hit.relative_time_us, []).append(hit.event_id)
        else:
            groups = native.get("groups")
            if not isinstance(groups, list):
                raise ValueError("invalid_character_state_result")
            for group in groups:
                if (not isinstance(group, list) or len(group) != 2 or type(group[0]) is not int
                    or not isinstance(group[1], list) or not all(isinstance(item, str) for item in group[1])):
                    raise ValueError("invalid_character_state_result")
                grouped[group[0]] = group[1]
        if config is None:
            value = None
            basis = "缺少真红升腾正式曲线，增伤数值保持未知。"
        else:
            value = (native.get("value") if native is not None else
                     config.damage_up + (config.resonance_damage_up if resonance else 0.0))
            if native is not None and (type(value) not in (int, float) or not isfinite(value)):
                raise ValueError("invalid_character_state_result")
            basis = (
                f"正式曲线 Shinku_Rage_DmgUp={config.damage_up:g}"
                + (
                    f" + Shinku_Rage_DmgUpEx_L3={config.resonance_damage_up:g}"
                    if resonance else ""
                )
                + " 加入 DamageUpGeneralBase。"
            )
        return tuple(
            BattleInferredBuffInterval(
                interval_id=f"buff:shinku-rage:{at_us}",
                buff_asset_path=_BUFF_ASSET,
                buff_name="升腾之赤（含三觉共鸣）" if resonance else "升腾之赤",
                source_effect_definition_id="character-rage:1076",
                source_kind="confirmed_character_form",
                source_character_id=1076,
                source_character_name=str(character.get("observed_name") or "真红"),
                target_scope="self",
                start_us=at_us,
                end_us=at_us + 1,
                stacks=1,
                duration_policy="ObservedRageHitOnly",
                state_confidence="中",
                value_confidence="未解析" if value is None else "高",
                inference_basis=(
                    "正式 Rage 伤害项确认该击属于升腾技能；仅投影命中时点，"
                    "不补齐未观测的持续状态，不外推威慑凝视、独行或普通技能。"
                    + basis
                ),
                trigger_event_type="FORMAL_SHINKU_RAGE_HIT",
                evidence_action_ids=(),
                evidence_event_ids=tuple(event_ids),
                modifiers=(BattleBuffModifierEvidence(
                    property_id="DamageUpGeneralBase",
                    modifier_operation="EGameplayModOp::Additive",
                    magnitude_kind="confirmed_static_curve",
                    magnitude_value=value,
                    calculation_asset_path=_CALC_ASSET,
                    value_confidence="未解析" if value is None else "高",
                    application_requirement_asset_path=SHINKU_RAGE_REQUIREMENT,
                ),),
                stacking_type="AggregateByTarget",
                stack_limit_count=1,
            )
            for at_us, event_ids in sorted(grouped.items())
        )


_WATCH = "ge_player_shinku_watch_damage"


_WATCH_EX = "ge_player_shinku_watchex_damage"


_WATCH_DAMAGE_IDS = frozenset({_WATCH, _WATCH_EX})


_PAIRED_SETTLEMENT_WINDOW_US = 150_000


_SAME_SETTLEMENT_US = 1_000


_ENTRY_DELAY_US = 2_000_000


_STACK_PERIOD_US = 1_000_000


@dataclass(frozen=True, slots=True)
class _WatchEstimate:
    stacks: int
    basis: str


def _selected_effects(character: Mapping[str, Any]) -> frozenset[str]:
    profile = character.get("profile") or {}
    if profile.get("awakening_selection_initialized"):
        return frozenset(str(value) for value in (
            profile.get("selected_awaken_effect_ids") or ()
        ))
    level = int(profile.get("awakening_level") or character.get("awakening_level") or 0)
    return frozenset(f"Effect{index}" for index in range(1, level + 1))


def _is_active_shinku_hit(hit: BattleAnalysisHit) -> bool:
    effect = hit.gameplay_effect_id.casefold()
    return bool(
        hit.character_id == 1076 and hit.direction == "outgoing"
        and hit.classification == "direct" and not hit.is_follow_up
        and effect.startswith("ge_player_shinku_")
        and effect not in _WATCH_DAMAGE_IDS
        and not any(token in effect for token in ("reaction", "qte", "passive"))
    )


def _unknown(reason: str) -> _WatchEstimate:
    return _WatchEstimate(0, "威慑凝视层数未解析：" + reason + (
        "；保留正式每层技能曲线，不以默认一层、觉醒上限或实测伤害反推层数。"
    ))


def _estimate_one(
    hit: BattleAnalysisHit,
    *,
    analysis: BattleAnalysisSnapshot,
    context_hits: Sequence[BattleAnalysisHit],
    selected: frozenset[str],
) -> _WatchEstimate:
    if not getattr(analysis, "axis_complete", False):
        return _unknown("逐击轴不完整，无法建立连续的增长与重置弱锚")
    # The triggering attack and Watch can share one server settlement.
    earlier = tuple(
        row for row in context_hits
        if row.relative_time_us < hit.relative_time_us - _SAME_SETTLEMENT_US
        and row.scope_half == hit.scope_half
        and (
            _is_active_shinku_hit(row)
            or (
                row.direction == "outgoing"
                and row.gameplay_effect_id.casefold() in _WATCH_DAMAGE_IDS
                and row.relative_time_us
                < hit.relative_time_us - _PAIRED_SETTLEMENT_WINDOW_US
            )
        )
    )
    if not earlier:
        return _unknown("本击之前没有真红主动命中或凝视消费锚，开场前积层未知")
    anchor = max(earlier, key=lambda row: (row.relative_time_us, row.sequence))
    retains_off_field = "Effect3" in selected
    if not retains_off_field:
        # Only inferred foreground E/Q actions are weak switch evidence.
        # Teammate damage, QTE and autonomous A hits are not switch facts.
        switched_out = any(
            action.character_id != 1076 and action.input_kind in {"E", "Q"}
            and anchor.relative_time_us < action.start_us < hit.relative_time_us
            and any(
                row.event_id in action.evidence_event_ids
                and row.scope_half == hit.scope_half
                and row.direction == "outgoing" and row.classification == "direct"
                and not row.is_follow_up
                for row in context_hits
            )
            for action in getattr(analysis, "inferred_actions", ())
        )
        if switched_out:
            return _unknown(
                "第3项觉醒未启用，最近真红命中后出现队友前台技能切人弱锚；"
                "切出应清层，但切回后的无攻击停留起点未采集"
            )
    active_us = projected_range_duration_us(
        anchor.relative_time_us, hit.relative_time_us,
        intervals=getattr(analysis, "time_stop_intervals", ()),
        mode=ACTIVE_TIME_MODE,
    )
    if active_us < _ENTRY_DELAY_US:
        return _unknown("最近主动命中后扣停表空窗不足两秒，计时弱模型无法解释本击")
    # Explicit weak convention: first stack at the 2 s entry boundary, then
    # each complete second. Real first-tick phase is not in this capture.
    stacks = 1 + (active_us - _ENTRY_DELAY_US) // _STACK_PERIOD_US
    trigger_evade = any(
        _is_active_shinku_hit(row) and row.target_id == hit.target_id
        and row.scope_half == hit.scope_half
        and abs(row.relative_time_us - hit.relative_time_us) <= _SAME_SETTLEMENT_US
        and "perfectevade" in row.gameplay_effect_id.casefold()
        for row in context_hits
    )
    if trigger_evade:
        stacks += 1
    cap = 16 if "Effect4" in selected else 8
    stacks = min(cap, stacks)
    return _WatchEstimate(stacks, (
        f"威慑凝视弱推断：以前次真红主动命中/凝视消费 {anchor.event_id} "
        f"为重置锚，按本场停表投影扣除后的空窗 {active_us / 1_000_000:.3f} 秒；"
        "假设空窗内无未命中主动攻击、无额外未记录极限闪避，"
        "并假设满两秒进入时获得首层、随后每满一秒增加一层。"
        + ("本次同批极限反击提供一次额外闪避层的弱锚。" if trigger_evade else "")
        + ("第3项觉醒启用，切人不清层并允许后台增长；" if retains_off_field else
           "第3项觉醒未启用，未发现打断本段的前台切人弱锚；")
        + f"按冻结觉醒取上限 {cap} 层，估算 {stacks} 层。"
        "这不是 Core 实测层数；未命中攻击、切换和首层相位仍可能使估算偏移。"
    ))


# 同半场、同目标有界配对；缺少锚点继续运输未知，不按伤害反推层数。
def apply_shinku_watch_state_boundary(
    evidence: Sequence[BattleSkillDamageEvidence],
    *,
    analysis: BattleAnalysisSnapshot,
    character: Mapping[str, Any] | None,
) -> tuple[BattleSkillDamageEvidence, ...]:
    """Use only a bounded weak estimate; keep missing anchors unresolved."""
    context_hits = tuple(getattr(analysis, "timeline_hits", ()) or analysis.hits)
    hits = {row.event_id: row for row in context_hits}
    selected = _selected_effects(character or {})
    estimates: dict[str, _WatchEstimate] = {}
    for row in evidence:
        if row.damage_id.casefold() not in _WATCH_DAMAGE_IDS:
            continue
        hit = hits.get(row.event_id)
        if hit is None or character is None:
            estimates[row.event_id] = _unknown("缺少冻结角色或本击原始身份")
            continue
        if row.damage_id.casefold() == _WATCH_EX:
            paired = tuple(
                other for other in context_hits
                if other.gameplay_effect_id.casefold() == _WATCH
                and other.target_id == hit.target_id and other.scope_half == hit.scope_half
                and abs(other.relative_time_us - hit.relative_time_us)
                <= _PAIRED_SETTLEMENT_WINDOW_US
            )
            if len(paired) != 1:
                estimates[row.event_id] = _unknown(
                    "追加凝视在同目标有界时间窗内没有唯一主凝视配对"
                )
                continue
            hit = paired[0]
        estimates[row.event_id] = _estimate_one(
            hit, analysis=analysis, context_hits=context_hits, selected=selected,
        )
    return tuple(
        replace(
            row, state_multiplier=float(estimates[row.event_id].stacks),
            state_multiplier_label=(
                "威慑凝视结算层数（弱推断）" if estimates[row.event_id].stacks else
                "威慑凝视结算层数（未解析）"
            ),
            state_multiplier_basis=estimates[row.event_id].basis,
            state_confidence="低" if estimates[row.event_id].stacks else "未解析",
        )
        if row.event_id in estimates else row
        for row in evidence
    )
