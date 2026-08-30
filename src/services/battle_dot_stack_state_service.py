# 按逐击与扣时停时钟重放噩梦、蚀心、鸩火和浊燃的目标层数。
"""Forward-only target stack reconstruction for direct-formula DOT hits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from src.domain.battle_report import BattleAnalysisHit, BattleAnalysisSnapshot
from src.services.battle_character_passive_service import (
    BattleCharacterPassiveService,
)
from src.services.battle_damage_composition_service import (
    explicit_reaction_channel_for_hit,
)
from src.services.battle_timeline_time_service import (
    ACTIVE_TIME_MODE,
    project_timeline_time_us,
)


_NIGHTMARE_IDS = frozenset({
    "ge_player_lacrimosa_blood_damage",
    "ge_player_lacrimosa_blood_damage_lv6",
})
_EROSION_ID = "ge_player_zankou_dotdamage"
_VENOM_ID = "ge_player_zankou_dotultradamage"
_ORDINARY_SCORCH_ID = "buff_reaction_5_new"
_ZANKOU_SCORCH_ID = "buff_reaction_5_new_1036"
_SCORCH_IDS = frozenset({_ORDINARY_SCORCH_ID, _ZANKOU_SCORCH_ID})
_CANG_FIELD_DOT_ID = "ge_player_cang_ultraskill_damage"
_ADLER_SKILL_DOT_ID = "ge_player_adler_skill_damage"
_RECENT_DOT_OBSERVATION_US = 1_500_000
_CANG_CAST_DAMAGE_IDS = frozenset({"ge_player_cang_ultraskill2_damage"})
_ADLER_CAST_DAMAGE_IDS = frozenset({
    "ge_player_adler_skill2_damage",
    "ge_player_adler_skill3_damage",
})
_DOT_KIND_BY_EFFECT = {
    **{effect_id: "nightmare" for effect_id in _NIGHTMARE_IDS},
    _EROSION_ID: "erosion",
    _VENOM_ID: "venom",
    _ORDINARY_SCORCH_ID: "scorch",
    _ZANKOU_SCORCH_ID: "scorch",
    _CANG_FIELD_DOT_ID: "cang_field",
    _ADLER_SKILL_DOT_ID: "adler_skill",
}
_DOT_KIND_NAMES = {
    "nightmare": "噩梦",
    "erosion": "蚀心",
    "venom": "鸩火",
    "scorch": "浊燃",
    "cang_field": "判予秋",
    "adler_skill": "诛恶护持",
}
@dataclass(frozen=True, slots=True)
class BattleDotStackState:
    event_id: str
    coefficient: int
    label: str
    confidence: str
    evidence_basis: str
    active_dot_kind_count: int = 0
    dot_final_multiplier: float = 1.0
    dot_final_multiplier_basis: str = ""


@dataclass(slots=True)
class _Stack:
    count: int = 0
    expires_at_active_us: int | None = None
    remove_one_on_expiry: bool = False
    duration_us: int = 0
    independent_expiry: bool = False
    expiry_times_active_us: list[int] = field(default_factory=list)

    def advance(self, active_us: int) -> None:
        if self.independent_expiry:
            self.expiry_times_active_us = [
                expiry
                for expiry in self.expiry_times_active_us
                if expiry > active_us
            ]
            self.count = len(self.expiry_times_active_us)
            return
        while (
            self.count > 0
            and self.expires_at_active_us is not None
            and active_us >= self.expires_at_active_us
        ):
            if not self.remove_one_on_expiry:
                self.count = 0
                self.expires_at_active_us = None
                return
            self.count -= 1
            self.expires_at_active_us += self.duration_us

    def add(self, amount: int, active_us: int) -> None:
        self.advance(active_us)
        if self.independent_expiry:
            available = max(0, 10 - len(self.expiry_times_active_us))
            accepted = min(available, max(0, amount))
            self.expiry_times_active_us.extend(
                active_us + self.duration_us for _ in range(accepted)
            )
            self.count = len(self.expiry_times_active_us)
            return
        self.count = min(10, self.count + max(0, amount))
        if self.count:
            self.expires_at_active_us = active_us + self.duration_us

    def refresh(self, active_us: int) -> None:
        self.advance(active_us)
        if self.count > 0:
            self.expires_at_active_us = active_us + self.duration_us

    def clear(self) -> None:
        self.count = 0
        self.expires_at_active_us = None
        self.expiry_times_active_us.clear()

    def set_single(self, active_us: int) -> None:
        self.count = 1
        self.expires_at_active_us = active_us + self.duration_us


@dataclass(slots=True)
class _VisibleDotBatch:
    """One target's application batch, bounded by cast identity and duration."""

    duration_us: int
    observed_cast_serial: int = -1
    expires_at_active_us: int | None = None

    def observe(self, active_us: int, cast_serial: int) -> int:
        new_cast = cast_serial != self.observed_cast_serial
        expired = (
            self.expires_at_active_us is None
            or active_us >= self.expires_at_active_us
        )
        if not new_cast and not expired:
            return 0
        self.observed_cast_serial = cast_serial
        self.expires_at_active_us = active_us + self.duration_us
        return 1


@dataclass(slots=True)
class _ScorchState:
    """One target's scorch layers plus Zankou's observed DOT activation gate."""

    stack_limit: int
    dot_activation_required: bool
    count: int = 0
    present: bool = False
    expires_at_active_us: int | None = None
    duration_us: int = 15_000_000

    def advance(self, active_us: int) -> None:
        if (
            self.expires_at_active_us is not None
            and active_us >= self.expires_at_active_us
        ):
            self.count = 0
            self.present = False
            self.expires_at_active_us = None

    def prepare_application(self, active_us: int) -> None:
        """Store one layer; Zankou keeps it dormant until an observed DOT hit."""

        self.advance(active_us)
        self.count = min(self.stack_limit, self.count + 1)
        if not self.dot_activation_required:
            self.present = True
        self.expires_at_active_us = active_us + self.duration_us

    def observe_settlement(self, active_us: int) -> None:
        """A recorded damage tick proves at least one layer was activated."""

        self.advance(active_us)
        if self.count == 0:
            self.count = 1
        self.present = True
        self.expires_at_active_us = active_us + self.duration_us

    def apply_dot_hit(self, active_us: int) -> None:
        """One recorded non-scorch DOT settlement activates and adds one layer."""

        self.advance(active_us)
        if not self.dot_activation_required or self.count <= 0:
            return
        self.count = min(self.stack_limit, self.count + 1)
        self.present = True
        self.expires_at_active_us = active_us + self.duration_us


def _builds(build: Mapping[str, object] | None) -> dict[int, Mapping[str, object]]:
    return {
        int(row["character_id"]): row
        for row in (build or {}).get("characters") or ()
        if isinstance(row, Mapping) and row.get("character_id") is not None
    }


def _nightmare_duration_seconds(build: Mapping[str, object] | None) -> float:
    character = _builds(build).get(1004, {})
    profile = character.get("profile") if isinstance(character, Mapping) else {}
    awakenings = (
        profile.get("selected_awaken_effect_ids")
        if isinstance(profile, Mapping)
        else ()
    ) or ()
    return 6.0 if "Effect4" in awakenings else 3.0


def _nightmare_early_settlement_enabled(
    build: Mapping[str, object] | None,
) -> bool:
    character = _builds(build).get(1004, {})
    profile = character.get("profile") if isinstance(character, Mapping) else {}
    awakenings = (
        profile.get("selected_awaken_effect_ids")
        if isinstance(profile, Mapping)
        else ()
    ) or ()
    return "Effect3" in awakenings


def _cang_field_duration_seconds(build: Mapping[str, object] | None) -> float:
    character = _builds(build).get(1023, {})
    profile = character.get("profile") if isinstance(character, Mapping) else {}
    awakenings = (
        profile.get("selected_awaken_effect_ids")
        if isinstance(profile, Mapping)
        else ()
    ) or ()
    return 16.0 if "Effect5" in awakenings else 12.0


def zankou_scorch_variant_for_build(
    build: Mapping[str, object] | None,
) -> str | None:
    """Resolve the replacement only from an explicitly frozen Zankou stage."""

    character = _builds(build).get(1036, {})
    if not character:
        return None
    profile = character.get("profile") if isinstance(character, Mapping) else {}
    has_explicit_stage = "breakthrough_stage" in character or (
        isinstance(profile, Mapping) and "breakthrough_stage" in profile
    )
    if not has_explicit_stage:
        return None
    stage = max(
        int(character.get("breakthrough_stage") or 0),
        int(profile.get("breakthrough_stage") or 0)
        if isinstance(profile, Mapping) else 0,
    )
    return "zankou" if stage >= 2 else "ordinary"


def _active_us(analysis: BattleAnalysisSnapshot, raw_us: int) -> int:
    return project_timeline_time_us(
        raw_us,
        battle_start_us=0,
        intervals=analysis.time_stop_intervals,
        mode=ACTIVE_TIME_MODE,
    )


def _is_nightmare_application(
    hit: BattleAnalysisHit,
) -> int:
    effect = hit.gameplay_effect_id.casefold()
    ability = hit.ability_id.casefold()
    if (
        hit.character_id != 1004
        or effect in _NIGHTMARE_IDS
        or hit.classification != "direct"
        or "qte" in effect
        or "qte" in ability
        or "steal" in effect
        or ability == "ga_lacrimosa_steal"
    ):
        return 0
    return 1


def _is_zankou_scorch_application_trigger(hit: BattleAnalysisHit) -> bool:
    if (
        hit.gameplay_effect_id.casefold() in _SCORCH_IDS
        or explicit_reaction_channel_for_hit(hit) == (
            "reaction_scorch",
            "浊燃",
        )
    ):
        return False
    return "浊燃" in " ".join((hit.attack_type, hit.skill_name, hit.damage_name))


def _is_erosion_application(hit: BattleAnalysisHit) -> int:
    effect = hit.gameplay_effect_id.casefold()
    if hit.character_id != 1036:
        return 0
    if _is_zankou_magic_melee(hit) or "zankou_magicbranch" in effect:
        return 1
    if effect in {
        "ge_player_zankou_skill1_1_damage",
        "ge_player_zankou_skill2_1_damage",
    }:
        return 5
    return 0


def _is_zankou_magic_melee(hit: BattleAnalysisHit) -> bool:
    return (
        hit.character_id == 1036
        and hit.classification == "direct"
        and "zankou_magicmelee" in hit.gameplay_effect_id.casefold()
    )


def _burst_final_markers(
    hits: Sequence[BattleAnalysisHit],
    *,
    character_id: int,
    effect_marker: str,
) -> set[str]:
    """Use the final hit of each multi-hit application burst as its state point."""

    markers: set[str] = set()
    by_target: dict[tuple[str, str], list[BattleAnalysisHit]] = {}
    for hit in hits:
        if (
            hit.character_id == character_id
            and effect_marker in hit.gameplay_effect_id.casefold()
        ):
            by_target.setdefault(_target_key(hit), []).append(hit)
    for target_hits in by_target.values():
        burst: list[BattleAnalysisHit] = []
        for hit in sorted(
            target_hits,
            key=lambda row: (row.relative_time_us, row.sequence),
        ):
            if (
                burst
                and hit.relative_time_us - burst[-1].relative_time_us
                > 2_000_000
            ):
                markers.add(burst[-1].event_id)
                burst = []
            burst.append(hit)
        if burst:
            markers.add(burst[-1].event_id)
    return markers


def _cast_first_markers(
    hits: Sequence[BattleAnalysisHit],
    *,
    character_id: int,
    damage_ids: frozenset[str],
) -> set[str]:
    """Collapse one cast's multi-target/multi-hit direct damage to its first hit."""

    candidates = sorted(
        (
            hit for hit in hits
            if hit.direction == "outgoing"
            and hit.character_id == character_id
            and hit.gameplay_effect_id.casefold() in damage_ids
        ),
        key=lambda row: (row.relative_time_us, row.sequence, row.event_id),
    )
    markers: set[str] = set()
    burst_first: BattleAnalysisHit | None = None
    previous: BattleAnalysisHit | None = None
    for hit in candidates:
        if (
            previous is not None
            and hit.relative_time_us - previous.relative_time_us > 2_000_000
        ):
            assert burst_first is not None
            markers.add(burst_first.event_id)
            burst_first = None
        if burst_first is None:
            burst_first = hit
        previous = hit
    if burst_first is not None:
        markers.add(burst_first.event_id)
    return markers


def _target_key(hit: BattleAnalysisHit) -> tuple[str, str]:
    return (
        str(hit.scope_half or "").casefold(),
        str(hit.target_id or "__unknown_target__"),
    )


def _stack_for(
    states: dict[tuple[str, str], _Stack],
    hit: BattleAnalysisHit,
    *,
    duration_us: int,
    independent_expiry: bool = False,
) -> _Stack:
    return states.setdefault(
        _target_key(hit),
        _Stack(
            duration_us=duration_us,
            independent_expiry=independent_expiry,
        ),
    )


def reconstruct_dot_stack_states(
    analysis: BattleAnalysisSnapshot,
    build: Mapping[str, object] | None,
) -> dict[str, BattleDotStackState]:
    """Return state evidence for each recorded DOT settlement hit."""

    nightmare_duration = round(_nightmare_duration_seconds(build) * 1_000_000)
    cang_field_duration = round(_cang_field_duration_seconds(build) * 1_000_000)
    early_settlement_enabled = _nightmare_early_settlement_enabled(build)
    nightmare_by_target: dict[tuple[str, str], _Stack] = {}
    erosion_by_target: dict[tuple[str, str], _Stack] = {}
    venom_by_target: dict[tuple[str, str], _Stack] = {}
    cang_field_by_target: dict[tuple[str, str], _Stack] = {}
    adler_skill_by_target: dict[tuple[str, str], _Stack] = {}
    cang_batch_by_target: dict[tuple[str, str], _VisibleDotBatch] = {}
    adler_batch_by_target: dict[tuple[str, str], _VisibleDotBatch] = {}
    scorch_variant = zankou_scorch_variant_for_build(build)
    scorch_stack_enabled = scorch_variant == "zankou"
    sagiri_dot_final_enabled = BattleCharacterPassiveService.is_unlocked(
        build,
        1003,
        2,
    )
    scorch_by_target: dict[tuple[str, str], _ScorchState] = {}
    venom_markers = _burst_final_markers(
        analysis.hits,
        character_id=1036,
        effect_marker="zankou_magicultraskill",
    )
    cang_cast_markers = _cast_first_markers(
        analysis.hits,
        character_id=1023,
        damage_ids=_CANG_CAST_DAMAGE_IDS,
    )
    adler_cast_markers = _cast_first_markers(
        analysis.hits,
        character_id=1033,
        damage_ids=_ADLER_CAST_DAMAGE_IDS,
    )
    cang_cast_serial = 0
    adler_cast_serial = 0
    results: dict[str, BattleDotStackState] = {}
    recent_dot_observations: dict[
        tuple[str, str],
        dict[str, int],
    ] = {}
    settlement_pending_by_target: dict[tuple[str, str], int] = {}
    ordered = sorted(
        (hit for hit in analysis.hits if hit.direction == "outgoing"),
        key=lambda row: (row.relative_time_us, row.sequence, row.event_id),
    )
    for hit in ordered:
        now = _active_us(analysis, hit.relative_time_us)
        state_wall_now = hit.relative_time_us
        target_key = _target_key(hit)
        if hit.event_id in cang_cast_markers:
            cang_cast_serial += 1
        if hit.event_id in adler_cast_markers:
            adler_cast_serial += 1
        nightmare = _stack_for(
            nightmare_by_target,
            hit,
            duration_us=nightmare_duration,
            independent_expiry=True,
        )
        erosion = _stack_for(
            erosion_by_target,
            hit,
            duration_us=30_000_000,
        )
        venom = _stack_for(
            venom_by_target,
            hit,
            duration_us=30_000_000,
        )
        cang_field = _stack_for(
            cang_field_by_target,
            hit,
            duration_us=cang_field_duration,
        )
        adler_skill = _stack_for(
            adler_skill_by_target,
            hit,
            duration_us=10_000_000,
        )
        cang_batch = cang_batch_by_target.setdefault(
            target_key,
            _VisibleDotBatch(duration_us=cang_field_duration),
        )
        adler_batch = adler_batch_by_target.setdefault(
            target_key,
            _VisibleDotBatch(duration_us=10_000_000),
        )
        scorch = scorch_by_target.setdefault(
            target_key,
            _ScorchState(
                stack_limit=3 if scorch_stack_enabled else 1,
                dot_activation_required=scorch_stack_enabled,
            ),
        )
        nightmare.advance(now)
        erosion.advance(now)
        venom.advance(now)
        cang_field.advance(now)
        adler_skill.advance(now)
        scorch.advance(now)
        observed_effect = hit.gameplay_effect_id.casefold()
        effect = observed_effect
        explicit_scorch = explicit_reaction_channel_for_hit(hit) == (
            "reaction_scorch",
            "浊燃",
        )
        ordinary_scorch_application = (
            not scorch_stack_enabled
            and (
                observed_effect == _ORDINARY_SCORCH_ID
                or explicit_scorch
            )
        )
        if scorch_stack_enabled and _is_zankou_scorch_application_trigger(hit):
            scorch.prepare_application(now)
        if effect in _SCORCH_IDS and scorch_variant is not None:
            effect = (
                _ZANKOU_SCORCH_ID
                if scorch_stack_enabled else _ORDINARY_SCORCH_ID
            )
        elif explicit_scorch and effect not in _SCORCH_IDS:
            effect = (
                _ZANKOU_SCORCH_ID
                if scorch_stack_enabled else _ORDINARY_SCORCH_ID
            )
        if ordinary_scorch_application:
            scorch.prepare_application(now)
        scorch_was_active = scorch.present
        is_early_settlement = False
        if effect in _NIGHTMARE_IDS:
            state = nightmare
            is_early_settlement = bool(
                now <= settlement_pending_by_target.get(target_key, -1)
            )
            label = "三觉：噩梦提前结算" if is_early_settlement else "噩梦当前层数"
        elif effect == _EROSION_ID:
            state = erosion
            label = "蚀心当前层数"
        elif effect == _VENOM_ID:
            state = venom
            label = "鸩火当前层数"
        elif effect in _SCORCH_IDS:
            state = scorch
            label = "浊燃结算前层数"
        elif effect == _CANG_FIELD_DOT_ID:
            state = cang_field
            label = "判予秋当前状态"
        elif effect == _ADLER_SKILL_DOT_ID:
            state = adler_skill
            label = "诛恶护持当前状态"
        else:
            state = None
            label = ""
        if state is not None:
            is_scorch = effect in _SCORCH_IDS
            current_kind = _DOT_KIND_BY_EFFECT[effect]
            coefficient_is_lower_bound = bool(
                not is_early_settlement
                and not is_scorch
                and state.count <= 0
            )
            if coefficient_is_lower_bound:
                label = f"{_DOT_KIND_NAMES[current_kind]}观测下限"
            modeled_kinds = {
                kind
                for kind, count in (
                    ("nightmare", nightmare.count),
                    ("erosion", erosion.count),
                    ("venom", venom.count),
                    (
                        "scorch",
                        scorch.count,
                    ),
                    ("cang_field", cang_field.count),
                    ("adler_skill", adler_skill.count),
                )
                if count > 0
            }
            recent_kinds = {
                kind
                for kind, observed_at_us in recent_dot_observations.get(
                    target_key,
                    {},
                ).items()
                if 0 <= state_wall_now - observed_at_us <= _RECENT_DOT_OBSERVATION_US
            }
            active_kinds = modeled_kinds | recent_kinds
            active_kinds.add(current_kind)
            scorch_confirmed_for_hit = scorch_was_active
            scorch_started_for_hit = (
                scorch.count > 0 or is_scorch
            )
            if scorch_started_for_hit:
                active_kinds.add("scorch")
            dot_kind_count = min(4, len(active_kinds))
            dot_final_multiplier = 1.0
            dot_final_basis = (
                "早雾突破 2 被动「可以吃吗？」未启用；"
                "DOT 专属最终乘区固定为 1"
            )
            if sagiri_dot_final_enabled:
                if scorch_confirmed_for_hit:
                    dot_final_multiplier += min(1.0, dot_kind_count * 0.25)
                    kind_names = "、".join(
                        _DOT_KIND_NAMES[kind] for kind in sorted(active_kinds)
                    )
                    recent_only = sorted(recent_kinds - modeled_kinds)
                    recent_basis = (
                        "；其中 "
                        + "、".join(_DOT_KIND_NAMES[kind] for kind in recent_only)
                        + " 由本击前 1.5 秒内近期正式跳伤确认，"
                        "不据此刷新其完整持续时间"
                        if recent_only
                        else ""
                    )
                    dot_final_basis = (
                        "早雾突破 2 被动「可以吃吗？」；"
                        + "目标结算前已处于浊燃；"
                        + f"活跃 DOT 种类为 {kind_names}，共 {dot_kind_count} 种；"
                        + "1 + min(种类数 × 25%, 100%)"
                        + recent_basis
                    )
                else:
                    dot_final_basis = (
                        "早雾突破 2 被动「可以吃吗？」已启用，但本击结算前"
                        "尚未确认目标处于浊燃；DOT 专属最终乘区固定为 1"
                    )
            visible_dot_basis = ""
            if effect == _CANG_FIELD_DOT_ID:
                visible_dot_basis = (
                    "优先按判予秋展开直伤划分 Q 批次；同目标第一跳作为该批状态"
                    "已施加 1 层的可见证据；缺失展开直伤时按正式 12/16 秒领域"
                    "维持保守批次；每个实际可见 DOT 跳伤另触发残虹补 1 层，"
                    "中间漏跳不反推"
                )
            elif effect == _ADLER_SKILL_DOT_ID:
                visible_dot_basis = (
                    "优先按诛恶护持初始直伤划分 E 批次；同目标第一跳作为该批状态"
                    "已施加 1 层的可见证据；缺失初始直伤时按正式 10 秒持续时间"
                    "维持保守批次；每个实际可见 DOT 跳伤另触发残虹补 1 层，"
                    "中间漏跳不反推"
                )
            state_basis = visible_dot_basis
            if effect in _NIGHTMARE_IDS:
                state_basis = (
                    "按同一目标逐击正向重放命中后加层；安魂曲除 QTE 与"
                    "学习 E 外，每个有效直伤 hit 施加 1 层噩梦；每次噩梦"
                    "实际跳伤后再触发残虹浊燃补 1 层；最大 10 层；"
                    "每层独立按扣时停时钟计算到期时间；"
                    + (
                        "本击前未找到施加事件；本跳只证明至少存在 1 份噩梦，"
                        "不反推精确层数"
                        if coefficient_is_lower_bound
                        else ""
                    )
                )
            elif effect == _EROSION_ID:
                state_basis = (
                    "按同一目标逐击正向重放蚀心施加；幻境形态普通/分支命中"
                    "加 1 层，强化技能命中加 5 层；最大 10 层；30 秒持续时间"
                    "按扣除时停的有效战斗时钟计算，时停期间不流逝"
                    + (
                        "；本击前缺少可见施加事件，本跳只证明至少存在 1 份蚀心，"
                        "不把观测下限解释成精确 1 层"
                        if coefficient_is_lower_bound
                        else ""
                    )
                )
            elif effect == _VENOM_ID:
                state_basis = (
                    "按同一目标逐击正向重放鸩火施加；「血宴入梦时」最终施加点"
                    "加 5 层；幻境形态普通攻击扩散只刷新已有鸩火持续时间，"
                    "不增加层数；最大 10 层；30 秒持续时间按扣除时停的有效"
                    "战斗时钟计算，时停期间不流逝"
                    + (
                        "；本击前缺少可见施加事件，本跳只证明至少存在 1 份鸩火，"
                        "不把观测下限解释成精确 1 层"
                        if coefficient_is_lower_bound
                        else ""
                    )
                )
            results[hit.event_id] = BattleDotStackState(
                event_id=hit.event_id,
                coefficient=(0 if is_early_settlement else max(1, state.count)),
                label=label,
                confidence=(
                    "未解析"
                    if is_early_settlement
                    else (
                        "中"
                        if is_scorch and scorch_stack_enabled and state.count > 1
                        else "低"
                        if is_scorch or coefficient_is_lower_bound
                        else "中"
                    )
                ),
                evidence_basis=(
                    (
                        "普通攻击终段触发三觉剩余伤害结算；当前尚未逐层重放"
                        "剩余结算次数，本击不按普通噩梦跳伤估算"
                    )
                    if is_early_settlement
                    else (
                        "按半场与目标隔离重放残虹浊燃共享状态；本跳先读取结算前层数；"
                        "残虹突破被动把上限改为 3；每次浊燃反应先存入 1 层未激活"
                        "浊燃；随后战报每实际出现 1 个非浊燃 DOT 跳伤 hit，浊燃同步"
                        "增加 1 层并激活整组伤害；中间漏跳不反推，浊燃自身跳伤不递归"
                        "加层。该投影由本机历史战报残差回归约束"
                        if is_scorch and effect == _ZANKOU_SCORCH_ID
                        else (
                            "普通浊燃最多 1 层；正式触发只刷新整组 15 秒持续时间，"
                            "实际周期跳伤不重置下一跳，也不把层数提升为残虹三层"
                            if is_scorch and effect == _ORDINARY_SCORCH_ID
                            else "逐击仅证明本目标当前至少存在 1 层浊燃"
                        )
                    )
                    if is_scorch
                    else state_basis
                ),
                active_dot_kind_count=dot_kind_count,
                dot_final_multiplier=dot_final_multiplier,
                dot_final_multiplier_basis=dot_final_basis,
            )
            if is_early_settlement:
                nightmare.clear()
                settlement_pending_by_target.pop(target_key, None)
            if is_scorch:
                scorch.observe_settlement(now)
            if effect not in _SCORCH_IDS:
                recent_dot_observations.setdefault(target_key, {})[
                    current_kind
                ] = state_wall_now
        nightmare.add(_is_nightmare_application(hit), now)
        if early_settlement_enabled and effect in {
            "ge_player_lacrimosa_melee5_damage",
            "ge_player_lacrimosa_b_melee8_damage",
        }:
            settlement_pending_by_target[target_key] = now + 500_000
        nightmare_application = _is_nightmare_application(hit)
        erosion_application = _is_erosion_application(hit)
        venom_application = 5 if hit.event_id in venom_markers else 0
        cang_field_application = (
            cang_batch.observe(now, cang_cast_serial)
            if effect == _CANG_FIELD_DOT_ID else 0
        )
        adler_skill_application = (
            adler_batch.observe(now, adler_cast_serial)
            if effect == _ADLER_SKILL_DOT_ID else 0
        )
        erosion.add(erosion_application, now)
        if _is_zankou_magic_melee(hit):
            venom.refresh(now)
        if venom_application:
            venom.add(venom_application, now)
        if cang_field_application:
            cang_field.set_single(now)
        if adler_skill_application:
            adler_skill.set_single(now)
        if (
            scorch_stack_enabled
            and effect in _DOT_KIND_BY_EFFECT
            and effect not in _SCORCH_IDS
        ):
            scorch.apply_dot_hit(now)
    return results
