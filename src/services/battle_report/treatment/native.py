# 将冻结养成与整场动作逐击送入治疗计算核，并恢复公开领域证据对象。
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from src.domain.battle_report import (
    BattleBuffModifierEvidence,
    BattleInferredBuffInterval,
    BattleTreatmentEvent,
)
from src.domain.native_analysis import BattleComputeBackend


TREATMENT_TEXT = {'oneiroi_awaken2_team': {'target_scope': 'team',
                          'evidence_kind': 'confirmed_other_character_e',
                          'confidence': '低',
                          'inference_basis': '伊洛伊二觉按当前项目已确认口径由其他角色实际 E 动作触发，5 '
                                             '个有效战斗秒冷却；三种随机召唤均只生成一次全队治疗，不按召唤攻击 hit '
                                             '数复制。正式导出未暴露完整调度时点，暂锚定到触发 E 动作起点。',
                          'amount_basis': '伊洛伊结算时攻击力 × 120%；当前事件量保持未知。'},
 'oneiroi_q_period': {'target_scope': 'team',
                      'evidence_kind': 'formal_skill_animation',
                      'confidence': '中',
                      'inference_basis': '伊洛伊 Q 持续治疗正式持续 14 秒、周期 1 秒且施加时不立即执行；周期与持续时间使用扣除时停的活动时钟。'},
 'edgar_hold_e_segment': {'target_scope': 'lowest_hp_team_member',
                          'evidence_kind': 'formal_damage_segment_binding',
                          'confidence': '中',
                          'inference_basis': '埃德嘉长按 E 每个实际伤害段同时应用一次治疗；只按战报真实存在的长按段生成，不补足理论七段。',
                          'amount_basis': '技能等级固定值 + 结算时受治疗目标最大生命 × '
                                          '技能等级比例；当前轴缺少受治疗目标与角色生命，数值保持未知。'},
 'shinku_effect5_rage_e': {'target_scope': 'self',
                           'evidence_kind': 'formal_rage_e_damage_binding',
                           'confidence': '中',
                           'inference_basis': '真红五觉仅在升腾之赤替换 E 的第二段正式伤害项实际出现时生成一次自疗；同刻多目标 hit '
                                              '不重复生成来源侧治疗。'},
 'kuhara_effect2_q_settlement': {'target_scope': 'team',
                                 'evidence_kind': 'formal_q_settlement_hit',
                                 'confidence': '中',
                                 'inference_basis': '九原二觉只在 Q 主动清算的真实玫约结算时治疗全队；长按 A '
                                                    '清算和自然到期不生成治疗。多目标按各自已观测清算事件保留独立来源。',
                                 'amount_basis': '该玫约目标累计总伤害 × 5%；当前轴未保存治疗执行器读取的累计值，数值保持未知。'},
 'lacrimosa_effect5_period': {'target_scope': 'self',
                              'evidence_kind': 'formal_period_and_damage_window',
                              'confidence': '中',
                              'inference_basis': '安魂曲五觉每 3 个有效战斗秒汇总上一窗口的噩梦本体伤害，并按总和的 1.5% '
                                                 '依当前统一结算规则向下取整自疗；无正数噩梦伤害的窗口不推断来源侧治疗广播。'},
 'edgar_q_field_period': {'target_scope': 'active_character',
                          'evidence_kind': 'formal_field_period',
                          'confidence': '中',
                          'inference_basis': '埃德嘉 Q 领域生成 1 个有效战斗秒后开始逐秒治疗。真理之匙按本场已推断的埃德嘉 E/QTE '
                                             '与一觉队友 QTE动作累计至三把，Q 时消耗并把基础 10 '
                                             '跳逐把延长；检测到其他角色动作时停止后续领域治疗。',
                          'amount_basis': '技能等级固定值 + 当前受治疗角色最大生命 × 技能等级比例；当前轴缺少受治疗目标与角色生命，数值保持未知。'},
 'zankou_effect3_huo_period': {'target_scope': 'self',
                               'evidence_kind': 'formal_huo_interval_and_period',
                               'confidence': '中',
                               'inference_basis': '残虹三觉治疗由惑状态上的 1 '
                                                  '秒正式周期执行；只在已重建惑持有区间内按扣除时停的活动时钟生成。当前轴没有玩家阵亡事件，一觉常驻惑因此投影至战斗结束。'},
 'oneiroi_e_tap': {'target_scope': 'team',
                   'evidence_kind': 'formal_skill_animation',
                   'confidence': '中',
                   'inference_basis': '伊洛伊正式技能在动画事件 E.1 施加治疗；事件时点由动作起点与正式动画 Notify 偏移派生。'},
 'oneiroi_qte': {'target_scope': 'team',
                 'evidence_kind': 'formal_skill_animation',
                 'confidence': '中',
                 'inference_basis': '伊洛伊正式技能在动画事件 QTE.2 施加治疗；事件时点由动作起点与正式动画 Notify 偏移派生。'},
 'oneiroi_q': {'target_scope': 'team',
               'evidence_kind': 'formal_skill_animation',
               'confidence': '中',
               'inference_basis': '伊洛伊正式技能在动画事件 UltraSkill.1 施加治疗；事件时点由动作起点与正式动画 Notify 偏移派生。'}}

TREATMENT_BUFF_TEXT = {'oneiroi-passive-2': {'buff_asset_path': '/Game/Blueprints/Abilities/Player/Ability_075_Oneiroi/PassiveEffect/Buff_Oneiroi075_Passive2_DamUP',
                       'buff_name': '交感性神经系统',
                       'source_effect_definition_id': 'character_passive:1075:GA_Oneiroi_Passive_2',
                       'target_scope': 'team',
                       'trigger_event_type': 'BUFF_EVENT_TREATMENT',
                       'inference_basis': '正式被动监听 BUFF_EVENT_TREATMENT；每次来源侧治疗事件刷新全队 20 '
                                          '秒无视防御，满血或零有效治疗仍视为治疗事件。',
                       'property_id': 'DefIgnore',
                       'magnitude_kind': 'formal_character_passive',
                       'calculation_asset_path': '/Game/Blueprints/Abilities/Calculation/Oneiroi/Calc_Oneiroi_Passive2_DefIgnore'},
 'oneiroi-e-attack': {'buff_asset_path': '/Game/Blueprints/Abilities/Player/Ability_075_Oneiroi/Buff/Buff_Oneiroi_Skill_AtkUP',
                      'buff_name': '伊洛伊 E：全队攻击力',
                      'source_effect_definition_id': 'character_skill:1075:GA_Oneiroi_Skill',
                      'target_scope': 'team',
                      'trigger_event_type': 'BUFF_EVENT_TREATMENT|oneiroi_e_tap',
                      'inference_basis': '伊洛伊点按 E 的正式治疗事件同时施加全队固定攻击力：min(来源基础攻击力 × 20%, 500)，持续 20 '
                                         '秒。',
                      'property_id': 'AtkAdd',
                      'magnitude_kind': 'formal_source_atk_base_calculation',
                      'calculation_asset_path': '/Game/DataTable/Skill/GlobalCharacterData/DT_OneiroiEffectFigure:Oneiroi_Skill_AtkAdd'},
 'oneiroi-effect-5': {'buff_asset_path': '/Game/Blueprints/Abilities/Player/Ability_075_Oneiroi/PassiveEffect/Buff_Oneiroi075_Passive2_LV4_DamUP',
                      'buff_name': '清晰',
                      'source_effect_definition_id': 'character_awaken:1075:Effect5',
                      'target_scope': 'team',
                      'trigger_event_type': 'BUFF_EVENT_TREATMENT',
                      'inference_basis': '伊洛伊五觉在每次正式治疗事件时刷新全队固定攻击力：min(来源基础攻击力 × 15%, 500)，持续 20 '
                                         '秒。',
                      'property_id': 'AtkAdd',
                      'magnitude_kind': 'formal_source_atk_base_calculation',
                      'calculation_asset_path': '/Game/Blueprints/Abilities/Calculation/Oneiroi/Calc_Oneiroi_Passive2_AtkAddLV4'}}


def _character(row: Mapping) -> dict:
    profile = row.get("profile") or {}
    profile = profile if isinstance(profile, Mapping) else {}
    if bool(profile.get("awakening_selection_initialized")):
        selected = {str(value).casefold() for value in profile.get("selected_awaken_effect_ids") or ()}
        effects = [f"Effect{index}" for index in range(1, 7) if f"effect{index}" in selected]
    else:
        awakening = int(row.get("awakening_level") or profile.get("awakening_level") or 0)
        effects = [f"Effect{index}" for index in range(1, 7) if awakening >= index]
    return {
        "id": int(row.get("character_id") or 0),
        "name": str(row.get("observed_name") or ""),
        "stage": int(row.get("breakthrough_stage") or profile.get("breakthrough_stage") or 0),
        "effects": effects,
        "stats": [
            (str(stat.get("property_id") or ""), str(stat.get("source_group") or ""),
             float(stat.get("value") or 0.0))
            for stat in row.get("stats") or ()
        ],
    }


def _event(payload: dict) -> BattleTreatmentEvent:
    payload = dict(payload)
    arguments = payload.pop("amount_arguments")
    kind = payload["treatment_kind"]
    text = dict(TREATMENT_TEXT[kind])
    if kind == "lacrimosa_effect5_period":
        text["amount_basis"] = f"floor({arguments['damage']:g} × 0.015)"
    elif kind == "shinku_effect5_rage_e":
        base = arguments["base_attack"]
        text["amount_basis"] = "GetAtkBase × 300%" if base is None else f"{base:g} × 300%"
    elif kind == "zankou_effect3_huo_period":
        maximum, ratio = arguments["maximum_health"], arguments["recover_ratio"]
        text["amount_basis"] = (
            f"残虹结算时最大生命 × {ratio:g}"
            if maximum is None else f"{maximum:g} × {ratio:g}"
        )
    payload["evidence_event_ids"] = tuple(payload["evidence_event_ids"])
    payload["target_character_ids"] = tuple(payload["target_character_ids"])
    return BattleTreatmentEvent(**payload, **text)


def _buff(payload: dict) -> BattleInferredBuffInterval:
    payload = dict(payload)
    kind, ordinal = payload.pop("kind"), payload.pop("ordinal")
    text = dict(TREATMENT_BUFF_TEXT[kind])
    modifier = BattleBuffModifierEvidence(
        property_id=text.pop("property_id"),
        modifier_operation="EGameplayModOp::Additive",
        magnitude_kind=text.pop("magnitude_kind"),
        magnitude_value=payload.pop("amount"),
        calculation_asset_path=text.pop("calculation_asset_path"),
        value_confidence="高",
    )
    for key in ("evidence_action_ids", "evidence_event_ids"):
        payload[key] = tuple(payload[key])
    return BattleInferredBuffInterval(
        **payload, **text,
        interval_id=f"buff:treatment:{kind}:{ordinal}",
        source_kind="formal_treatment_consumer", source_character_id=1075,
        stacks=1, duration_policy="HasDuration", state_confidence="中",
        value_confidence="高", modifiers=(modifier,),
        stacking_type="AggregateByTarget", stack_limit_count=1,
    )


def infer_treatment_batch(
    *,
    build: Mapping | None,
    actions: Sequence,
    hits: Sequence,
    battle_end_us: int,
    time_stop_intervals: Sequence,
    state_buff_intervals: Sequence,
    zankou_effect_three_recover_ratio: float | None,
    infer_buffs: bool,
    backend: BattleComputeBackend,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[tuple[BattleTreatmentEvent, ...], tuple[BattleInferredBuffInterval, ...]]:
    def rows(values, fields):
        result = []
        for index, row in enumerate(values):
            if checkpoint is not None and index % 64 == 0:
                checkpoint()
            result.append({key: getattr(row, key) for key in fields})
        return result

    payload = {
        "characters": [_character(row) for row in (build or {}).get("characters") or ()],
        "actions": rows(actions, (
            "action_id", "character_id", "character_name", "input_kind", "input_gesture",
            "start_us", "end_us", "evidence_event_ids",
        )),
        "hits": rows(hits, (
            "event_id", "character_id", "character_name", "relative_time_us", "damage",
            "damage_name", "skill_name", "gameplay_effect_id", "ability_id", "target_id",
        )),
        "state_buff_intervals": rows(state_buff_intervals, (
            "interval_id", "source_character_id", "source_effect_definition_id",
            "start_us", "end_us",
        )),
        "battle_end_us": battle_end_us,
        "time_stop_intervals": time_stop_intervals,
        "recover_ratio": zankou_effect_three_recover_ratio,
        "infer_buffs": infer_buffs,
    }
    # 整批响应次数严格复核；原生错误/取消直接传播，不触发Python推断重试。
    responses = backend.compute_batch("treatment_replay_v1", (payload,), checkpoint=checkpoint)
    if len(responses) != 1:
        raise ValueError("treatment response count mismatch")
    response, = responses
    return tuple(map(_event, response["events"])), tuple(map(_buff, response["buffs"]))
