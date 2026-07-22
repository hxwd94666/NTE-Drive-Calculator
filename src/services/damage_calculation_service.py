# 按项目金标准计算可解释的直伤乘区。
"""Pure direct-damage calculations for the project combat-rule specification."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class DamageScalingStat(str, Enum):
    """技能伤害倍率对应的角色属性。"""

    ATTACK = "attack"
    HEALTH = "health"
    DEFENSE = "defense"


class DamageScene(str, Enum):
    """已定义敌人防御基数的战斗场景。"""

    OUTER_REALM = "outer_realm"
    OPEN_WORLD = "open_world"


@dataclass(frozen=True)
class DirectDamageInput:
    """One direct-damage instance. Percentage values use fractions (30% = 0.30)."""

    skill_multiplier: float
    scaling_stat: DamageScalingStat
    attack_base: float
    attack_up: float
    attack_add: float
    health_base: float
    health_up: float
    health_add: float
    defense_base: float
    defense_up: float
    defense_add: float
    character_level: float
    enemy_level: float
    crit_rate: float
    crit_damage: float
    defense_penetration: float
    defense_reduction: float
    damage_increases: tuple[float, ...] = ()
    vulnerability_increases: tuple[float, ...] = ()
    boss_resistance: float = 0.20
    enemy_resistance_reductions: tuple[float, ...] = ()
    resistance_penetrations: tuple[float, ...] = ()
    independent_damage_bonuses: tuple[float, ...] = ()
    scene: DamageScene = DamageScene.OUTER_REALM


@dataclass(frozen=True)
class DirectDamageResult:
    """Transparent direct-damage breakdown for display and later optimization."""

    damage: float
    attack: float
    health: float
    defense: float
    scaling_attribute_value: float
    damage_increase_multiplier: float
    vulnerability_multiplier: float
    critical_multiplier: float
    enemy_defense: float
    defense_multiplier: float
    effective_resistance: float
    resistance_multiplier: float
    independent_multiplier: float


@dataclass(frozen=True)
class DotDamageInput:
    """DOT parameters; each duration represents one independently settled stack."""

    damage: DirectDamageInput
    remaining_durations: tuple[float, ...]
    max_stacks: int


@dataclass(frozen=True)
class DotSettlementLayer:
    """The settlement result for a single DOT stack."""

    stack_index: int
    remaining_duration: float
    damage: float


@dataclass(frozen=True)
class DotDamageResult:
    """One DOT tick and the current all-stack settlement total."""

    tick_damage: float
    stack_count: int
    max_stacks: int
    settlement_layers: tuple[DotSettlementLayer, ...]
    settlement_damage: float


@dataclass(frozen=True)
class ToppleDamageInput:
    """倾陷伤害参数；mitigation 提供防御区与抗性区所需的目标数据。"""

    level_multiplier: float
    mitigation: DirectDamageInput
    team_topple_strength: float
    topple_damage_increases: tuple[float, ...] = ()
    enemy_topple_limit: float = 50.0


@dataclass(frozen=True)
class ToppleDamageResult:
    """倾陷伤害的可解释乘区结果。"""

    damage: float
    level_multiplier: float
    topple_strength_multiplier: float
    enemy_topple_limit_multiplier: float
    defense_multiplier: float
    resistance_multiplier: float


@dataclass(frozen=True)
class RingCharacter:
    """A ring-reaction participant whose strength can own the reaction calculation."""

    character_id: str
    level_multiplier: float
    ring_strength: float
    crit_damage: float = 0.0


@dataclass(frozen=True)
class DarkStarState:
    """Legacy owner set retained for callers that only need duplicate detection."""

    owner_ids: tuple[str, ...] = ()

    def add(self, owner_id: str) -> "DarkStarState":
        if owner_id in self.owner_ids:
            return self
        return DarkStarState(owner_ids=(*self.owner_ids, owner_id))


@dataclass(frozen=True)
class TimedReactionState:
    """A refreshable reaction status. Reapplying it refreshes, never stacks, its duration."""

    expires_at: float | None = None

    def apply(self, now: float, duration: float) -> "TimedReactionState":
        if duration < 0:
            raise ValueError("反应持续时间不能为负数。")
        return TimedReactionState(expires_at=now + duration)

    def is_active(self, now: float) -> bool:
        return self.expires_at is not None and now < self.expires_at


@dataclass(frozen=True)
class DarkStarInstance:
    """One independently timed Dark Star created by one trigger owner."""

    owner_id: str
    expires_at: float


@dataclass(frozen=True)
class DarkStarInstances:
    """Dark Star instances: same owner refreshes; different owners explode independently."""

    instances: tuple[DarkStarInstance, ...] = ()

    def apply(self, owner_id: str, now: float, duration: float) -> "DarkStarInstances":
        if duration < 0:
            raise ValueError("黯星持续时间不能为负数。")
        refreshed = DarkStarInstance(owner_id=owner_id, expires_at=now + duration)
        retained = tuple(item for item in self.instances if item.owner_id != owner_id)
        return DarkStarInstances(instances=(*retained, refreshed))

    def expired(self, now: float) -> tuple[DarkStarInstance, ...]:
        return tuple(item for item in self.instances if item.expires_at <= now)

    def remove_expired(self, now: float) -> "DarkStarInstances":
        return DarkStarInstances(tuple(item for item in self.instances if item.expires_at > now))


class DamageCalculationService:
    """Calculate the confirmed direct-damage formula without side effects."""

    @staticmethod
    def calculate_direct(values: DirectDamageInput) -> DirectDamageResult:
        """Return direct damage and every multiplier in the project-standard formula."""
        if values.skill_multiplier < 0:
            raise ValueError("技能伤害倍率不能为负数。")
        if values.character_level < 0 or values.enemy_level < 0:
            raise ValueError("角色等级和敌人等级不能为负数。")

        attack = calculate_attribute_value(values.attack_base, values.attack_up, values.attack_add)
        health = calculate_attribute_value(values.health_base, values.health_up, values.health_add)
        defense = calculate_attribute_value(values.defense_base, values.defense_up, values.defense_add)
        scaling_attribute_value = {
            DamageScalingStat.ATTACK: attack,
            DamageScalingStat.HEALTH: health,
            DamageScalingStat.DEFENSE: defense,
        }[values.scaling_stat]

        damage_increase_multiplier = calculate_additive_multiplier(values.damage_increases)
        vulnerability_multiplier = calculate_additive_multiplier(values.vulnerability_increases)
        critical_multiplier = calculate_critical_multiplier(values.crit_rate, values.crit_damage)
        enemy_defense = calculate_enemy_defense(
            values.enemy_level, values.defense_penetration, values.defense_reduction, values.scene
        )
        defense_multiplier = calculate_defense_multiplier(values.character_level, enemy_defense)
        effective_resistance = calculate_effective_resistance(
            values.boss_resistance,
            values.enemy_resistance_reductions,
            values.resistance_penetrations,
        )
        resistance_multiplier = calculate_resistance_multiplier(effective_resistance)
        independent_multiplier = calculate_independent_multiplier(values.independent_damage_bonuses)
        damage = (
            values.skill_multiplier
            * scaling_attribute_value
            * damage_increase_multiplier
            * critical_multiplier
            * defense_multiplier
            * resistance_multiplier
            * vulnerability_multiplier
            * independent_multiplier
        )
        return DirectDamageResult(
            damage=damage,
            attack=attack,
            health=health,
            defense=defense,
            scaling_attribute_value=scaling_attribute_value,
            damage_increase_multiplier=damage_increase_multiplier,
            vulnerability_multiplier=vulnerability_multiplier,
            critical_multiplier=critical_multiplier,
            enemy_defense=enemy_defense,
            defense_multiplier=defense_multiplier,
            effective_resistance=effective_resistance,
            resistance_multiplier=resistance_multiplier,
            independent_multiplier=independent_multiplier,
        )

    @staticmethod
    def calculate_dot(values: DotDamageInput) -> DotDamageResult:
        """Calculate one DOT tick and the current per-stack settlement total."""
        if values.max_stacks < 1:
            raise ValueError("DOT 最大层数必须至少为 1。")
        if len(values.remaining_durations) > values.max_stacks:
            raise ValueError("DOT 当前层数不能超过最大层数。")
        if any(duration < 0 for duration in values.remaining_durations):
            raise ValueError("DOT 剩余时长不能为负数。")

        tick_result = DamageCalculationService.calculate_direct(
            replace(values.damage, crit_rate=0.50)
        )
        settlement_layers = tuple(
            DotSettlementLayer(
                stack_index=index,
                remaining_duration=duration,
                damage=tick_result.damage * duration,
            )
            for index, duration in enumerate(values.remaining_durations, start=1)
        )
        return DotDamageResult(
            tick_damage=tick_result.damage,
            stack_count=len(settlement_layers),
            max_stacks=values.max_stacks,
            settlement_layers=settlement_layers,
            settlement_damage=sum(layer.damage for layer in settlement_layers),
        )

    @staticmethod
    def calculate_topple(values: ToppleDamageInput) -> ToppleDamageResult:
        """Calculate topple damage: level × strength × target limit × defense × resistance."""
        if values.level_multiplier < 0:
            raise ValueError("倾陷角色等级乘区不能为负数。")
        if values.team_topple_strength < 0 or values.enemy_topple_limit < 0:
            raise ValueError("倾陷强度和敌方倾陷上限不能为负数。")

        source = values.mitigation
        enemy_defense = calculate_enemy_defense(
            source.enemy_level,
            source.defense_penetration,
            source.defense_reduction,
            source.scene,
        )
        defense_multiplier = calculate_defense_multiplier(source.character_level, enemy_defense)
        effective_resistance = calculate_effective_resistance(
            source.boss_resistance,
            source.enemy_resistance_reductions,
            source.resistance_penetrations,
        )
        resistance_multiplier = calculate_resistance_multiplier(effective_resistance)
        topple_strength_multiplier = calculate_topple_strength_multiplier(
            values.team_topple_strength, values.topple_damage_increases
        )
        enemy_topple_limit_multiplier = calculate_enemy_topple_limit_multiplier(
            values.enemy_topple_limit
        )
        damage = (
            values.level_multiplier
            * topple_strength_multiplier
            * enemy_topple_limit_multiplier
            * defense_multiplier
            * resistance_multiplier
        )
        return ToppleDamageResult(
            damage=damage,
            level_multiplier=values.level_multiplier,
            topple_strength_multiplier=topple_strength_multiplier,
            enemy_topple_limit_multiplier=enemy_topple_limit_multiplier,
            defense_multiplier=defense_multiplier,
            resistance_multiplier=resistance_multiplier,
        )

    @staticmethod
    def calculate_dissonance_topple_reduction(enemy_topple_limit: float) -> float:
        """Project-default Dissonance reduction: 15% of target maximum topple value."""
        return calculate_dissonance_topple_reduction(enemy_topple_limit)


def calculate_attribute_value(base: float, increase: float, additive: float) -> float:
    """Calculate attack, health, or defense: base × (1 + increase) + additive."""
    return base * (1 + increase) + additive


def calculate_additive_multiplier(bonuses: tuple[float, ...]) -> float:
    """Calculate an additive multiplier such as damage increase or vulnerability."""
    return 1 + sum(bonuses)


def calculate_critical_multiplier(crit_rate: float, crit_damage: float) -> float:
    """Calculate expected critical damage."""
    return 1 + crit_rate * crit_damage


def _enemy_defense_offset(scene: DamageScene) -> float:
    if scene is DamageScene.OUTER_REALM:
        return 90.0
    if scene is DamageScene.OPEN_WORLD:
        return 100.0
    raise ValueError(f"不支持的战斗场景：{scene}")


def calculate_enemy_defense(
    enemy_level: float,
    defense_penetration: float,
    defense_reduction: float,
    scene: DamageScene,
) -> float:
    """Calculate scene-specific enemy defense after penetration and reduction."""
    return (enemy_level + _enemy_defense_offset(scene)) * (1 - defense_penetration) * (1 - defense_reduction)


def calculate_defense_multiplier(character_level: float, enemy_defense: float) -> float:
    """Calculate the defense multiplier for the active scene."""
    return (character_level + 100) / (enemy_defense + character_level + 100)


def calculate_effective_resistance(
    boss_resistance: float,
    resistance_reductions: tuple[float, ...],
    resistance_penetrations: tuple[float, ...],
) -> float:
    """Calculate elemental resistance after enemy debuffs and attacker penetration."""
    return boss_resistance - sum(resistance_reductions) - sum(resistance_penetrations)


def calculate_resistance_multiplier(effective_resistance: float) -> float:
    if effective_resistance >= 0:
        return 1 - effective_resistance
    return 1 - effective_resistance / 1.10


def calculate_independent_multiplier(bonuses: tuple[float, ...]) -> float:
    multiplier = 1.0
    for bonus in bonuses:
        multiplier *= 1 + bonus
    return multiplier


def select_ring_owner(*participants: RingCharacter) -> RingCharacter:
    """Select the participant with the highest ring-strength × level-multiplier value."""
    if not participants:
        raise ValueError("环合至少需要一名参与角色。")
    return max(participants, key=lambda item: item.ring_strength * item.level_multiplier)


def calculate_ring_strength_multiplier(ring_strength: float) -> float:
    """Convert ring strength to its additive damage multiplier (strength / 6 percent)."""
    return 1 + ring_strength / 600


def calculate_ring_amplification(ring_strength: float) -> float:
    """Calculate 覆纹/浸染的 24 × strength / (strength + 180) factor."""
    if ring_strength < 0:
        raise ValueError("环合强度不能为负数。")
    return 24 * ring_strength / (ring_strength + 180)


def calculate_dissonance_topple_reduction(enemy_topple_limit: float) -> float:
    """Project-default Dissonance reduction: 15% of target maximum topple value."""
    if enemy_topple_limit < 0:
        raise ValueError("敌方倾陷上限不能为负数。")
    return enemy_topple_limit * 0.15


def reaction_tier_for_character_level(character_level: int) -> int:
    """Map level 1–80 to the project 16-tier reaction curve (five levels per tier)."""
    if not 1 <= character_level <= 80:
        raise ValueError("环合等级必须在 1 到 80 之间。")
    return (character_level - 1) // 5


def reaction_multiplier_for_character_level(
    character_level: int, reaction_damage_tiers: tuple[float, ...]
) -> float:
    """Select a reaction multiplier from its 16 official tiers."""
    if len(reaction_damage_tiers) != 16:
        raise ValueError("环合伤害曲线必须包含 16 个档位。")
    return reaction_damage_tiers[reaction_tier_for_character_level(character_level)]


def skill_tier_for_effective_level(effective_skill_level: int, tier_count: int = 15) -> int:
    """Map effective skill level to a source tier, clamping to available official data."""
    if effective_skill_level < 1:
        raise ValueError("有效技能等级必须至少为 1。")
    if tier_count < 1:
        raise ValueError("技能倍率档位数必须至少为 1。")
    return min(effective_skill_level - 1, tier_count - 1)


def effective_skill_level(base_skill_level: int, awakening_level: int) -> int:
    """Return the project-default effective skill level.

    The confirmed current rule is that awakening level three grants +1 to every
    skill. Other awakening effects are deliberately not inferred here.
    """
    if base_skill_level < 1:
        raise ValueError("基础技能等级必须至少为 1。")
    if awakening_level < 0:
        raise ValueError("觉醒等级不能为负数。")
    return base_skill_level + int(awakening_level >= 3)


def calculate_weave_followup_damage(actual_damage: float, ring_strength: float) -> float:
    """Calculate Weave's expiry follow-up from the already dealt actual damage value."""
    if actual_damage < 0:
        raise ValueError("覆纹记录的实际伤害不能为负数。")
    return actual_damage * 0.20 * calculate_ring_amplification(ring_strength)


def calculate_infusion_damage_increase(ring_strength: float) -> float:
    """Return Infusion's additive Soul/Lakshana damage-increase contribution."""
    return 0.20 * calculate_ring_amplification(ring_strength)


def calculate_topple_strength_multiplier(
    team_topple_strength: float, topple_damage_increases: tuple[float, ...]
) -> float:
    """Calculate 1 + team topple strength / 300 + all topple-damage bonuses."""
    return 1 + team_topple_strength / 300 + sum(topple_damage_increases)


def calculate_enemy_topple_limit_multiplier(enemy_topple_limit: float) -> float:
    """Calculate the enemy topple-limit multiplier."""
    return enemy_topple_limit / 3
