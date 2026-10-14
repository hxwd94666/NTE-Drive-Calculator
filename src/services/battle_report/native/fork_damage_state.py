# 传递冻结弧盘状态事实，按原生区间描述恢复中文依据与原始修饰项。
from __future__ import annotations


from src.services.battle_report.native.state_payload import HIT_FIELDS, ACTION_FIELDS, state_rows
from src.domain.battle_report.buffs.models import BattleBuffModifierEvidence

_RULE_FIELDS = (
    "event_type", "source_character_id", "target_asset_path", "duration_seconds",
    "cooldown_seconds", "stack_limit_count",
)
_BASIS = {
    "tiger": "每次 E 或 Q 开始增加一层普攻与极限反击增伤；每层独立持续十五个有效战斗秒，命中时最多采用两层。",
    "commander": "E 实际结束取得左虎符、Q begin 取得右虎符，并在十五个有效战斗秒内凑齐；"
                 "第二枚正式虎符到达时立即建立十个有效战斗秒的司令虎符区间。",
    "rose": "持续伤害每 0.3 秒最多叠一层；E 开始立即补满十层。",
    "moon": "每次正式魂属性伤害结算后增加一层，0.1 秒最多一层；"
            "每层独立持续五个有效战斗秒，最多采用十层。",
    "time": "E 建立荒时迷宫并清零；队友 E/QTE 累积荒时；"
            "本次 Q 消耗 {stacks} 层荒时，暴击伤害强化只覆盖该次 Q。",
    "time_defense": "本次 Q 一次性消耗三层荒时，从 Q 起点获得"
                    "作用于装备者全部伤害的无视防御，持续七十个有效战斗秒。",
    "spider": "普通攻击每 0.5 秒最多获得一层蜘识；Q 消耗 {stacks} 层，八层时追加额外全队攻击力。",
}


def infer_native_fork_damage(
    backend, rules, *, actions, hits, battle_end_us, time_stop_intervals, checkpoint=None,
):
    from src.services.battle_report.forks.damage_state import _interval

    # 函数内导入领域 owner，保持冷启动与正式原生能力分派边界。
    payload = {
        "rules": [
            {**{key: getattr(rule, key) for key in _RULE_FIELDS},
             "modifiers": [{"property_id": modifier.property_id,
                            "magnitude_value": modifier.magnitude_value}
                           for modifier in rule.modifiers]}
            for rule in rules
        ],
        "actions": state_rows(actions, ACTION_FIELDS),
        "hits": state_rows(hits, HIT_FIELDS),
        "battle_end_us": battle_end_us,
        "time_stop_intervals": list(time_stop_intervals),
    }
    responses = backend.compute_batch("fork_damage_state_v1", (payload,), checkpoint=checkpoint)
    if len(responses) != 1:
        raise ValueError("Native fork damage response count mismatch")
    results = []
    for ordinal, descriptor in enumerate(responses[0]["intervals"]):
        if checkpoint is not None and ordinal % 64 == 0:
            checkpoint()
        rule_index = descriptor["rule_index"]
        if type(rule_index) is not int or not 0 <= rule_index < len(rules):
            raise ValueError("Native fork damage rule index mismatch")
        rule = rules[rule_index]
        kind = descriptor["modifier_kind"]
        modifiers = None
        if kind:
            if kind == "time":
                crit = tuple(row for row in rule.modifiers if row.property_id == "CritDamageBase")
                template = crit[1]
            elif kind == "spider":
                template = rule.modifiers[0]
            else:
                raise ValueError("Unsupported native fork modifier descriptor")
            modifier = BattleBuffModifierEvidence(
                property_id="CritDamageBase" if kind == "time" else "AtkUp",
                modifier_operation=template.modifier_operation,
                magnitude_kind=template.magnitude_kind,
                magnitude_value=descriptor["modifier_value"],
                calculation_asset_path=template.calculation_asset_path,
                value_confidence=template.value_confidence,
                source_require_tags=template.source_require_tags if kind == "time" else (),
            )
            modifiers = (crit[0], modifier) if kind == "time" else (modifier,)
        row = _interval(
            rule, suffix=descriptor["suffix"], start_us=descriptor["start_us"],
            end_us=descriptor["end_us"], stacks=descriptor["stacks"],
            basis=_BASIS[descriptor["basis_kind"]].format(stacks=descriptor["basis_stacks"]),
            action_ids=descriptor["action_ids"], event_ids=descriptor["event_ids"],
            modifiers=modifiers,
        )
        if row is None:
            raise ValueError("Native fork damage emitted an empty interval")
        results.append(row)
    return tuple(results)
