# 完成剩余伤害向弧盘的静态消费者与固定轴状态机。
"""Damage-first completion rules for the fork audit catalog."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.domain.battle_report import (
    BattleBuffModifierEvidence,
)
from src.services.battle_fork_residual_completion_service import (
    BattleForkResidualCompletionService,
)


FORK_DAMAGE_COMPLETION_MODEL_VERSION = "battle-fork-damage-completion-v3"
ROSE_STACK_EVENT = "FORK_ROSE_DAMAGE_STACK"
TIGER_NORMAL_STACK_EVENT = "FORK_TIGER_NORMAL_STACK"
TIGER_COMMANDER_EVENT = "FORK_TIGER_COMMANDER_INFERRED"
TIME_Q_CONSUME_EVENT = "FORK_TIME_Q_CONSUME"
MOON_PSYCHIC_STACK_EVENT = "FORK_MOON_PSYCHIC_STACK"
SPIDER_Q_CONSUME_EVENT = "FORK_SPIDER_Q_CONSUME"

_PROKARYON = "upgradestar_pack_fork_prokaryon"
_ROSE = "upgradestar_pack_fork_rose"
_THIEF_CANDY = "upgradestar_pack_fork_thiefcandy"
_TIGER_TALLY = "upgradestar_pack_fork_tigertally"
_TIME = "upgradestar_pack_fork_time"
_WHALE = "upgradestar_pack_fork_whale"
_WORLDRAIN = "upgradestar_pack_fork_worldrain"
_APPLIANCE = "upgradestar_pack_fork_appliance"
_BOPU = "upgradestar_pack_fork_bopu"
_JIAOJUAN = "upgradestar_pack_fork_jiaojuan"
_MOFEIKESI = "upgradestar_pack_fork_mofeikesi"
_MOON = "upgradestar_pack_fork_moon"
_NONOS = "upgradestar_pack_fork_nonos"
_OULA = "upgradestar_pack_fork_oulaquantao"
_RISHI = "upgradestar_pack_fork_rishi"
_SPIDER = "upgradestar_pack_fork_spider"
_AUDITED_MARKERS = frozenset({
    _PROKARYON,
    _ROSE,
    _THIEF_CANDY,
    _TIGER_TALLY,
    _TIME,
    _WHALE,
    _WORLDRAIN,
    _APPLIANCE,
    _BOPU,
    _JIAOJUAN,
    _MOFEIKESI,
    _MOON,
    _NONOS,
    _OULA,
    _RISHI,
    _SPIDER,
})

def _parameter(definition: Mapping[str, Any] | None, name_id: str) -> float | None:
    parameters = (definition or {}).get("parameters") or ()
    if isinstance(parameters, Mapping):
        value = parameters.get(name_id)
        return float(value) if isinstance(value, (int, float)) else None
    if not isinstance(parameters, Sequence) or isinstance(parameters, str):
        return None
    for row in parameters:
        if not isinstance(row, Mapping) or row.get("name_id") != name_id:
            continue
        value = row.get("value")
        return float(value) if isinstance(value, (int, float)) else None
    return None


def _modifier(
    property_id: str,
    value: float,
    *,
    source_tags: Sequence[str] = (),
    target_tags: Sequence[str] = (),
) -> BattleBuffModifierEvidence:
    return BattleBuffModifierEvidence(
        property_id=property_id,
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind="confirmed_fork_parameter",
        magnitude_value=float(value),
        calculation_asset_path="",
        value_confidence="高",
        source_require_tags=tuple(source_tags),
        target_require_tags=tuple(target_tags),
    )


def _rule(
    selected: Any,
    factory: type[Any],
    *,
    suffix: str,
    name: str,
    event_type: str,
    modifiers: tuple[BattleBuffModifierEvidence, ...],
    scope: str = "self",
    duration: float | None = None,
    stack_limit: int = 1,
    cooldown: float | None = None,
    stacking: str = "AggregateBySource",
    application_requirement: str = "",
) -> Any:
    effect_id = str(selected.effect_definition_id)
    return factory(
        rule_id=f"{effect_id}:confirmed-fork:{suffix}",
        source_effect_definition_id=effect_id,
        source_kind="confirmed_fork_refinement",
        source_character_id=int(selected.character_id),
        source_character_name=str(selected.character_name),
        source_asset_path=f"combat-effect:{effect_id}",
        target_asset_path=f"confirmed-fork:{suffix}",
        target_name=name,
        target_scope=scope,
        event_type=event_type,
        effect_type="ADD",
        duration_policy=("HasDuration" if duration is not None else "Equipped"),
        duration_seconds=duration,
        stack_count=1,
        modifiers=modifiers,
        stacking_type=stacking,
        stack_limit_count=stack_limit,
        cooldown_seconds=cooldown,
        application_requirement_asset_path=application_requirement,
    )


def _static(
    selected: Any,
    factory: type[Any],
    *,
    suffix: str,
    name: str,
    modifiers: tuple[BattleBuffModifierEvidence, ...],
    scope: str = "self",
) -> Any:
    return _rule(
        selected,
        factory,
        suffix=suffix,
        name=name,
        event_type="STATIC_EQUIPPED_SOURCE",
        modifiers=modifiers,
        scope=scope,
    )


def _rules_prokaryon(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    value = _parameter(selected.definition, "buff_Prokaryon_Up")
    if value is None:
        return ()
    return (_static(
        selected,
        factory,
        suffix="prokaryon-normal",
        name="「我们。」：普通攻击伤害",
        modifiers=(_modifier(
            "DamageUpGeneralBase",
            value,
            source_tags=("State.Damage.NormalAttack",),
        ),),
    ),)


def _rules_rose(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    attack = _parameter(selected.definition, "buff_Rose_AtkUp")
    crit = _parameter(selected.definition, "buff_Rose_CritDamageUp")
    duration = _parameter(selected.definition, "buff_Rose_CD")
    topple_extension = _parameter(selected.definition, "buff_Rose_UnbalTime")
    if None in {attack, crit, duration, topple_extension}:
        return ()
    return (
        _static(
            selected,
            factory,
            suffix="rose-attack",
            name="最后一朵玫瑰：攻击力",
            modifiers=(_modifier("AtkUp", attack),),
        ),
        _rule(
            selected,
            factory,
            suffix="rose-thorn-stack",
            name="最后一朵玫瑰：暗棘暴击伤害",
            event_type=ROSE_STACK_EVENT,
            modifiers=(_modifier("CritDamageBase", crit),),
            duration=duration,
            stack_limit=10,
            cooldown=0.3,
            stacking="AggregateBySource|RefreshWholeStack",
        ),
        _static(
            selected,
            factory,
            suffix="rose-topple-extension",
            name=(
                f"最后一朵玫瑰：单次倾陷延长 {topple_extension:g} 秒"
                "（缺少逐目标倾陷生命周期写入能力）"
            ),
            scope="unknown",
            modifiers=(_modifier("ToppleDurationAdd", topple_extension),),
        ),
    )


def _rules_thief(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    value = _parameter(selected.definition, "buff_ThiefCandy_Up")
    duration = _parameter(selected.definition, "buff_ThiefCandy_CD")
    if None in {value, duration}:
        return ()
    return (_rule(
        selected,
        factory,
        suffix="thief-perfect-evade",
        name="灵敏之绵：极限闪避后伤害",
        event_type="EBuffEventType::BUFF_EVENT_PERFECT_EVADE",
        modifiers=(_modifier("DamageUpGeneralBase", value),),
        duration=duration,
        stack_limit=3,
        stacking="AggregateBySource|RefreshWholeStack",
    ),)


def _rules_tiger(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    attack = _parameter(selected.definition, "buff_TigerTally_AtkUp")
    normal = _parameter(selected.definition, "buff_TigerTally_NormalUp")
    duration = _parameter(selected.definition, "buff_TigerTally_CD")
    token_window = _parameter(selected.definition, "buff_TigerTally_CD4")
    commander = _parameter(selected.definition, "buff_TigerTally_Qup")
    commander_duration = _parameter(selected.definition, "buff_TigerTally_CD3")
    if None in {
        attack,
        normal,
        duration,
        token_window,
        commander,
        commander_duration,
    }:
        return ()
    return (
        _static(
            selected,
            factory,
            suffix="tiger-attack",
            name="预备备：攻击力",
            modifiers=(_modifier("AtkUp", attack),),
        ),
        _rule(
            selected,
            factory,
            suffix="tiger-normal-stack",
            name="预备备：普攻与极限反击伤害",
            event_type=TIGER_NORMAL_STACK_EVENT,
            modifiers=(_modifier(
                "DamageUpGeneralBase",
                normal,
                source_tags=("State.Damage.NormalOrCounter",),
            ),),
            duration=duration,
            stack_limit=2,
        ),
        _rule(
            selected,
            factory,
            suffix="tiger-commander",
            name="预备备：司令虎符 Boss 伤害",
            event_type=TIGER_COMMANDER_EVENT,
            modifiers=(_modifier(
                "DamageUpGeneralBase",
                commander,
                target_tags=("Con_IsBoss",),
            ),),
            duration=commander_duration,
            cooldown=token_window,
            stacking="Override",
        ),
    )


def _rules_time(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    attack = _parameter(selected.definition, "buff_Time_AtkUp")
    base = _parameter(selected.definition, "buff_Time_stateCritDamageUp")
    per_stack = _parameter(selected.definition, "buff_Time_CritDamageUp")
    defence = _parameter(selected.definition, "buff_Time_DefIgnore")
    duration = _parameter(selected.definition, "buff_Time_DefIgnore_Dur")
    if None in {attack, base, per_stack, defence, duration}:
        return ()
    return (
        _static(
            selected,
            factory,
            suffix="time-attack",
            name="行进于时间之外：攻击力",
            modifiers=(_modifier("AtkUp", attack),),
        ),
        _rule(
            selected,
            factory,
            suffix="time-q-consume",
            name="行进于时间之外：消耗荒时强化 Q",
            event_type=TIME_Q_CONSUME_EVENT,
            modifiers=(
                _modifier(
                    "CritDamageBase",
                    base,
                    source_tags=("State.Damage.UltraSkill",),
                ),
                _modifier(
                    "CritDamageBase",
                    per_stack,
                    source_tags=("State.Damage.UltraSkill",),
                ),
                _modifier(
                    "DefIgnore",
                    defence,
                    source_tags=("State.Damage.UltraSkill",),
                ),
            ),
            duration=duration,
            stack_limit=3,
        ),
    )


def _rules_whale(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    attack = _parameter(selected.definition, "buff_Whale_AtkUp")
    topple = _parameter(selected.definition, "buff_Whale_Up")
    heal = _parameter(selected.definition, "buff_Whale_Hp")
    cooldown = _parameter(selected.definition, "buff_Whale_CD")
    if None in {attack, topple, heal, cooldown}:
        return ()
    return (
        _static(
            selected,
            factory,
            suffix="whale-attack",
            name="鲸之歌：攻击力",
            modifiers=(_modifier("AtkUp", attack),),
        ),
        _static(
            selected,
            factory,
            suffix="whale-topple-target",
            name="鲸之歌：倾陷目标伤害",
            scope="unknown",
            modifiers=(_modifier(
                "DamageUpGeneralBase",
                topple,
                target_tags=("confirmed-target-state:topple",),
            ),),
        ),
        _static(
            selected,
            factory,
            suffix="whale-topple-kill-heal",
            name=(
                f"鲸之歌：倾陷击杀回复最大生命的 {heal * 100:g}%"
                f"（冷却 {cooldown:g} 秒；缺少正式击杀事件）"
            ),
            scope="unknown",
            modifiers=(_modifier("HPCurrentRestoreRatio", heal),),
        ),
    )


def _rules_worldrain(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    cosmos = _parameter(selected.definition, "buff_worldrain_CosmosUp")
    magnitude = _parameter(selected.definition, "buff_worldrain_Mag")
    duration = _parameter(selected.definition, "buff_worldrain_CD")
    if None in {cosmos, magnitude, duration}:
        return ()
    skill_tags = (
        _modifier(
            "DamageUpCosmosBase",
            cosmos,
            source_tags=("State.Damage.Skill",),
        ),
        _modifier(
            "DamageUpCosmosBase",
            cosmos,
            source_tags=("State.Damage.UltraSkill",),
        ),
    )
    return (
        _static(
            selected,
            factory,
            suffix="worldrain-eq-cosmos",
            name="倾世之雨：E/Q 光属性伤害",
            modifiers=skill_tags,
        ),
        _rule(
            selected,
            factory,
            suffix="worldrain-e-magnitude",
            name="倾世之雨：E 后环合强度",
            event_type="EBuffEventType::BUFF_EVENT_E_SKILL_BEGIN",
            modifiers=(_modifier("MagBase", magnitude),),
            duration=duration,
            stacking="AggregateBySource|RefreshWholeStack",
        ),
    )


def _rules_appliance(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    value = _parameter(selected.definition, "buff_appliance_Up")
    if value is None:
        return ()
    return (_static(
        selected,
        factory,
        suffix="appliance-e",
        name="电音狂欢：E 伤害",
        modifiers=(_modifier(
            "DamageUpGeneralBase",
            value,
            source_tags=("State.Damage.Skill",),
        ),),
    ),)


def _rules_bopu(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    duration = _parameter(selected.definition, "buff_haokewu_CD")
    value = _parameter(selected.definition, "buff_haokewu_Up")
    cooldown = _parameter(selected.definition, "buff_haokewu_CD2")
    if None in {duration, value, cooldown}:
        return ()
    return (_rule(
        selected,
        factory,
        suffix="bopu-qte-window",
        name="光波眩晕：QTE 后伤害",
        event_type="EBuffEventType::BUFF_EVENT_QTE_BEGIN",
        modifiers=(_modifier("DamageUpGeneralBase", value),),
        duration=duration,
        cooldown=cooldown,
        stacking="AggregateBySource|RefreshWholeStack",
    ),)


def _rules_jiaojuan(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    unbalance = _parameter(selected.definition, "buff_oulaquantao_Unbal")
    damage = _parameter(selected.definition, "buff_oulaquantao_Up")
    if None in {unbalance, damage}:
        return ()
    return (
        _static(
            selected,
            factory,
            suffix="jiaojuan-unbalance",
            name="闪耀的每一天：倾陷强度",
            modifiers=(_modifier("UnbalIntensityBase", unbalance),),
        ),
        _static(
            selected,
            factory,
            suffix="jiaojuan-topple-target",
            name="闪耀的每一天：倾陷目标伤害",
            scope="unknown",
            modifiers=(_modifier(
                "DamageUpGeneralBase",
                damage,
                target_tags=("confirmed-target-state:topple",),
            ),),
        ),
    )


def _rules_mofeikesi(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    charge = _parameter(selected.definition, "buff_mofeikesi_ChargeGetEfficiency")
    duration = _parameter(selected.definition, "buff_mofeikesi_CD")
    attack = _parameter(selected.definition, "buff_mofeikesi_Atk")
    controlled = _parameter(selected.definition, "buff_mofeikesi_Up")
    if None in {charge, duration, attack, controlled}:
        return ()
    return (
        _static(
            selected,
            factory,
            suffix="mofeikesi-charge",
            name="好狗狗走四方：充能效率（固定轴不补造后续 Q）",
            modifiers=(_modifier("ChargeGetEfficiencyBase", charge),),
        ),
        _rule(
            selected,
            factory,
            suffix="mofeikesi-q-team-attack",
            name="好狗狗走四方：Q 后全队攻击力",
            event_type="EBuffEventType::BUFF_EVENT_Q_SKILL_BEGIN",
            modifiers=(_modifier("AtkUp", attack),),
            scope="team",
            duration=duration,
            stacking="AggregateBySource|RefreshWholeStack",
        ),
        _rule(
            selected,
            factory,
            suffix="mofeikesi-controlled-extra",
            name="好狗狗走四方：Q 控制触发后额外攻击",
            event_type="FORK_MOFEIKESI_CONTROLLED_HIT",
            modifiers=(_modifier("AtkUp", controlled),),
            scope="team",
            duration=duration,
            stacking="AggregateBySource|RefreshWholeStack",
            application_requirement=(
                "/Game/Blueprints/Abilities/Condition/Fork/"
                "Con_Fork_mofeikesi/Con_Fork_mofeikesi_1"
            ),
        ),
    )


def _rules_moon(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    psyche = _parameter(selected.definition, "buff_moon_PsycheUp")
    crit = _parameter(selected.definition, "buff_moon_CritDamageUp")
    duration = _parameter(selected.definition, "buff_moon_CD")
    if None in {psyche, crit, duration}:
        return ()
    return (
        _static(
            selected,
            factory,
            suffix="moon-psyche",
            name="银河暂留：魂属性伤害",
            modifiers=(_modifier("DamageUpPsycheBase", psyche),),
        ),
        _rule(
            selected,
            factory,
            suffix="moon-crit-stack",
            name="银河暂留：魂伤暴击伤害层数",
            event_type=MOON_PSYCHIC_STACK_EVENT,
            modifiers=(_modifier("CritDamageBase", crit),),
            duration=duration,
            stack_limit=10,
            cooldown=0.1,
        ),
    )


def _rules_nonos(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    attack = _parameter(selected.definition, "buff_nonos_AtkUp")
    duration = _parameter(selected.definition, "buff_nonos_CD")
    cooldown = _parameter(selected.definition, "buff_nonos_CD2")
    if None in {attack, duration, cooldown}:
        return ()
    return (_rule(
        selected,
        factory,
        suffix="nonos-e-attack",
        name="成功的第一步：E 后攻击力",
        event_type="EBuffEventType::BUFF_EVENT_E_SKILL_BEGIN",
        modifiers=(_modifier("AtkUp", attack),),
        duration=duration,
        cooldown=cooldown,
        stacking="AggregateBySource|RefreshWholeStack",
    ),)


def _rules_oula(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    duration = _parameter(selected.definition, "buff_wujinjieti_CD")
    value = _parameter(selected.definition, "buff_wujinjieti_Up")
    if None in {duration, value}:
        return ()
    return (_rule(
        selected,
        factory,
        suffix="oula-normal-stack",
        name="欧拉欧拉：普通攻击独立层数",
        event_type="suit_source_attack_hit|a",
        modifiers=(_modifier(
            "DamageUpGeneralBase",
            value,
            source_tags=("State.Damage.NormalAttack",),
        ),),
        duration=duration,
        stack_limit=10,
    ),)


def _rules_rishi(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    attack = _parameter(selected.definition, "buff_rishi_AtkUp")
    duration = _parameter(selected.definition, "buff_rishi_CD2")
    energy = _parameter(selected.definition, "buff_rishi_energy")
    stack_limit = _parameter(selected.definition, "buff_rishi_stack")
    cooldown = _parameter(selected.definition, "buff_rishi_CD")
    if None in {attack, duration, energy, stack_limit, cooldown}:
        return ()
    return (
        _static(
            selected,
            factory,
            suffix="rishi-attack",
            name="休息日：攻击力",
            modifiers=(_modifier("AtkUp", attack),),
        ),
        _static(
            selected,
            factory,
            suffix="rishi-eclipse-resource",
            name=(
                f"休息日：日蚀持续 {duration:g} 秒、每次击杀回复 {energy:g} "
                f"终结能量、最多 {stack_limit:g} 次（冷却 {cooldown:g} 秒；"
                "缺少主动与击杀事件）"
            ),
            scope="unknown",
            modifiers=(_modifier("UltraEnergyAdd", energy),),
        ),
    )


def _rules_spider(selected: Any, factory: type[Any]) -> tuple[Any, ...]:
    duration = _parameter(selected.definition, "buff_spider_CD")
    attack = _parameter(selected.definition, "buff_spider_AtkUp")
    extra = _parameter(selected.definition, "buff_spider_AtkUp2")
    if None in {duration, attack, extra}:
        return ()
    return (_rule(
        selected,
        factory,
        suffix="spider-q-consume",
        name="挂你在心口难开：消耗蜘识全队攻击力",
        event_type=SPIDER_Q_CONSUME_EVENT,
        modifiers=(
            _modifier("AtkUp", attack),
            _modifier("AtkUp", extra),
        ),
        scope="team",
        duration=duration,
        stack_limit=8,
    ),)


_BUILDERS = (
    (_PROKARYON, _rules_prokaryon),
    (_ROSE, _rules_rose),
    (_THIEF_CANDY, _rules_thief),
    (_TIGER_TALLY, _rules_tiger),
    (_TIME, _rules_time),
    (_WHALE, _rules_whale),
    (_WORLDRAIN, _rules_worldrain),
    (_APPLIANCE, _rules_appliance),
    (_BOPU, _rules_bopu),
    (_JIAOJUAN, _rules_jiaojuan),
    (_MOFEIKESI, _rules_mofeikesi),
    (_MOON, _rules_moon),
    (_NONOS, _rules_nonos),
    (_OULA, _rules_oula),
    (_RISHI, _rules_rishi),
    (_SPIDER, _rules_spider),
)


class BattleForkDamageCompletionService:
    """Own damage-focused forks whose semantics are confirmed by static text."""

    @staticmethod
    def owns_effect(effect_definition_id: str) -> bool:
        normalized = str(effect_definition_id or "").casefold()
        return (
            any(marker in normalized for marker in _AUDITED_MARKERS)
            or BattleForkResidualCompletionService.owns_effect(normalized)
        )

    @classmethod
    def rules_for_selected_effect(
        cls,
        selected: Any,
        rule_factory: type[Any],
    ) -> tuple[Any, ...]:
        effect_id = str(selected.effect_definition_id).casefold()
        for marker, builder in _BUILDERS:
            if marker in effect_id:
                return builder(selected, rule_factory)
        return BattleForkResidualCompletionService.rules_for_selected_effect(
            selected,
            rule_factory,
        )

    @classmethod
    def infer_specialized(
        cls,
        rules: Sequence[Any],
        *,
        actions: Sequence[Any],
        hits: Sequence[Any],
        battle_end_us: int,
        time_stop_intervals: Sequence[tuple[int | None, int | None]] = (),
    ) -> tuple[Any, ...]:
        from src.services.battle_fork_damage_state_service import (
            BattleForkDamageStateService,
        )

        return BattleForkDamageStateService.infer_specialized(
            rules,
            actions=actions,
            hits=hits,
            battle_end_us=battle_end_us,
            time_stop_intervals=time_stop_intervals,
        )
