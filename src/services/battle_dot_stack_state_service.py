# 按逐击与扣时停时钟重放噩梦、蚀心、鸩火和浊燃的目标层数。
"""Forward-only target stack reconstruction for direct-formula DOT hits."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from src.domain.battle_report import BattleAnalysisHit, BattleAnalysisSnapshot
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
_SCORCH_ID = "buff_reaction_5_new_1036"
_LACRIMOSA_GLOBAL_VALUE_TABLE = (
    "/Game/DataTable/Skill/GlobalCharacterData/DT_GlobalValueLacrimosaData"
)
_LACRIMOSA_SKILL_STACK_CURVE = "Lacrimosa_Skilldotnum_1"
_LACRIMOSA_ULTIMATE_STACK_CURVE = "Lacrimosa_UltraSkilldotnum_1"


@dataclass(frozen=True, slots=True)
class BattleDotStackRules:
    """Official per-cast DOT-stack values frozen before replay starts."""

    nightmare_skill_application: int
    nightmare_ultimate_application: int


def _constant_positive_integer_curve(
    static_dao: object,
    curve_id: str,
) -> int:
    getter = getattr(static_dao, "get_combat_curve", None)
    if not callable(getter):
        raise ValueError("静态数据读取器不支持角色战斗曲线")
    curve = getter(_LACRIMOSA_GLOBAL_VALUE_TABLE, curve_id)
    points = tuple((curve or {}).get("points") or ())
    values = tuple(float(point["value"]) for point in points)
    if not values or any(value != values[0] for value in values[1:]):
        raise ValueError(f"安魂曲技能层数曲线不是单值：{curve_id}")
    value = round(values[0])
    if value <= 0 or abs(values[0] - value) > 1e-9:
        raise ValueError(f"安魂曲技能层数不是正整数：{curve_id}={values[0]}")
    return value


def load_official_dot_stack_rules(static_dao: object) -> BattleDotStackRules:
    """Load the official E/Q Nightmare applications from static curves."""

    return BattleDotStackRules(
        nightmare_skill_application=_constant_positive_integer_curve(
            static_dao,
            _LACRIMOSA_SKILL_STACK_CURVE,
        ),
        nightmare_ultimate_application=_constant_positive_integer_curve(
            static_dao,
            _LACRIMOSA_ULTIMATE_STACK_CURVE,
        ),
    )


@dataclass(frozen=True, slots=True)
class BattleDotStackState:
    event_id: str
    coefficient: int
    label: str
    confidence: str
    evidence_basis: str


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

    def clear(self) -> None:
        self.count = 0
        self.expires_at_active_us = None
        self.expiry_times_active_us.clear()


@dataclass(slots=True)
class _ScorchState:
    """One target's shared scorch snapshot; periodic hits never add layers."""

    stack_limit: int
    count: int = 0
    expires_at_active_us: int | None = None
    duration_us: int = 15_000_000
    pending_applications: list[tuple[int, int]] = field(default_factory=list)

    def advance(self, active_us: int) -> None:
        self.pending_applications = [
            row for row in self.pending_applications
            if active_us - row[0] <= 1_000_000
        ]
        if (
            self.expires_at_active_us is not None
            and active_us >= self.expires_at_active_us
        ):
            self.count = 0
            self.expires_at_active_us = None

    def observe_settlement(self, active_us: int) -> None:
        """A recorded tick proves at least one pre-settlement layer exists."""

        self.advance(active_us)
        if self.count == 0:
            recent_applications = sum(amount for _, amount in self.pending_applications)
            self.count = min(self.stack_limit, 1 + recent_applications)
            self.expires_at_active_us = active_us + self.duration_us
        self.pending_applications.clear()

    def apply_dot_layers(self, amount: int, active_us: int) -> None:
        """Refresh the shared snapshot without moving the recorded next tick."""

        self.advance(active_us)
        if amount <= 0:
            return
        if self.count == 0:
            self.pending_applications.append((active_us, amount))
            return
        self.count = min(self.stack_limit, self.count + amount)
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


def _zankou_scorch_stack_enabled(build: Mapping[str, object] | None) -> bool:
    character = _builds(build).get(1036, {})
    profile = character.get("profile") if isinstance(character, Mapping) else {}
    stage = max(
        int(character.get("breakthrough_stage") or 0),
        int(profile.get("breakthrough_stage") or 0)
        if isinstance(profile, Mapping) else 0,
    )
    return stage >= 2


def _active_us(analysis: BattleAnalysisSnapshot, raw_us: int) -> int:
    return project_timeline_time_us(
        raw_us,
        battle_start_us=0,
        intervals=analysis.time_stop_intervals,
        mode=ACTIVE_TIME_MODE,
    )


def _is_nightmare_application(
    hit: BattleAnalysisHit,
    rules: BattleDotStackRules,
) -> int:
    effect = hit.gameplay_effect_id.casefold()
    if hit.character_id != 1004 or effect in _NIGHTMARE_IDS:
        return 0
    if effect == "ge_player_lacrimosa_skill_damage":
        return rules.nightmare_skill_application
    if hit.ability_id in {
        "GA_Lacrimosa_Melee",
        "GA_Lacrimosa_ExtremEvadeAtk",
    } and effect.startswith("ge_player_lacrimosa_"):
        return 1
    return 0


def _is_erosion_application(hit: BattleAnalysisHit) -> int:
    effect = hit.gameplay_effect_id.casefold()
    if hit.character_id != 1036:
        return 0
    if "zankou_magicmelee" in effect or "zankou_magicbranch" in effect:
        return 1
    if effect in {
        "ge_player_zankou_skill1_1_damage",
        "ge_player_zankou_skill2_1_damage",
    }:
        return 5
    return 0


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
    *,
    rules: BattleDotStackRules,
) -> dict[str, BattleDotStackState]:
    """Return state evidence for each recorded DOT settlement hit."""

    nightmare_duration = round(_nightmare_duration_seconds(build) * 1_000_000)
    early_settlement_enabled = _nightmare_early_settlement_enabled(build)
    nightmare_by_target: dict[tuple[str, str], _Stack] = {}
    erosion_by_target: dict[tuple[str, str], _Stack] = {}
    venom_by_target: dict[tuple[str, str], _Stack] = {}
    scorch_stack_enabled = _zankou_scorch_stack_enabled(build)
    scorch_by_target: dict[tuple[str, str], _ScorchState] = {}
    venom_markers = _burst_final_markers(
        analysis.hits,
        character_id=1036,
        effect_marker="zankou_magicultraskill",
    )
    nightmare_q_markers = _burst_final_markers(
        analysis.hits,
        character_id=1004,
        effect_marker="lacrimosa_ultraskill",
    )
    results: dict[str, BattleDotStackState] = {}
    settlement_pending_by_target: dict[tuple[str, str], int] = {}
    ordered = sorted(
        (hit for hit in analysis.hits if hit.direction == "outgoing"),
        key=lambda row: (row.relative_time_us, row.sequence, row.event_id),
    )
    for hit in ordered:
        now = _active_us(analysis, hit.relative_time_us)
        target_key = _target_key(hit)
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
        scorch = scorch_by_target.setdefault(
            target_key,
            _ScorchState(stack_limit=3 if scorch_stack_enabled else 1),
        )
        nightmare.advance(now)
        erosion.advance(now)
        venom.advance(now)
        scorch.advance(now)
        effect = hit.gameplay_effect_id.casefold()
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
        elif effect == _SCORCH_ID:
            scorch.observe_settlement(now)
            state = scorch
            label = "浊燃结算前层数"
        else:
            state = None
            label = ""
        if state is not None:
            is_scorch = effect == _SCORCH_ID
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
                        else "低" if is_scorch else ("中" if state.count else "低")
                    )
                ),
                evidence_basis=(
                    (
                        "普通攻击终段触发三觉剩余伤害结算；当前尚未逐层重放"
                        "剩余结算次数，本击不按普通噩梦跳伤估算"
                    )
                    if is_early_settlement
                    else (
                        "按半场与目标隔离重放浊燃共享状态；本跳先读取结算前层数；"
                        "残虹突破被动启用时，每次已识别的非浊燃 DOT 状态施加按层数"
                        "补充浊燃，最多 3 层；新增或满层触发只刷新整组快照与 15 秒"
                        "到期，不移动原轴下一跳；首次可见跳伤回看前 1 秒已识别施加；"
                        "浊燃自身跳伤不递归加层"
                        if is_scorch and scorch_stack_enabled
                        else "逐击仅证明本目标当前至少存在 1 层浊燃"
                    )
                    if is_scorch
                    else (
                        "按同一目标逐击正向重放命中后加层；最大 10 层；"
                        "每层独立按扣时停时钟计算到期时间；"
                        f"E按官方技能详情一次附加 "
                        f"{rules.nightmare_skill_application} 层；"
                        f"极轨终结按官方技能详情一次附加 "
                        f"{rules.nightmare_ultimate_application} 层"
                        + (
                            "；本击前未找到施加事件，暂按 1 层"
                            if not state.count else ""
                        )
                    )
                ),
            )
            if is_early_settlement:
                nightmare.clear()
                settlement_pending_by_target.pop(target_key, None)
        nightmare.add(_is_nightmare_application(hit, rules), now)
        if hit.event_id in nightmare_q_markers:
            nightmare.add(rules.nightmare_ultimate_application, now)
        if early_settlement_enabled and effect in {
            "ge_player_lacrimosa_melee5_damage",
            "ge_player_lacrimosa_b_melee8_damage",
        }:
            settlement_pending_by_target[target_key] = now + 500_000
        nightmare_application = _is_nightmare_application(hit, rules)
        if hit.event_id in nightmare_q_markers:
            nightmare_application += rules.nightmare_ultimate_application
        erosion_application = _is_erosion_application(hit)
        venom_application = 5 if hit.event_id in venom_markers else 0
        erosion.add(erosion_application, now)
        if venom_application:
            venom.add(venom_application, now)
        if scorch_stack_enabled and effect != _SCORCH_ID:
            scorch.apply_dot_layers(
                nightmare_application + erosion_application + venom_application,
                now,
            )
    return results
