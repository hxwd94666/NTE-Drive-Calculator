# 将固定轴结果与历史格式化为界面文案，不改变冻结目标、比例或数值。
from dataclasses import replace
import json

from src.domain.localization.catalog import localized_text
from src.domain.teams.graduation.axis_graduation_presentation import AxisGraduationDisplay, AxisGraduationHistoryView


KIND_LABELS = {"growth": "等级/突破", "skill_with_growth": "技能及成长前置", "awakening": "觉醒",
    "fork": "弧盘", "likeability": "好感", "world": "家具", "combined_cultivation": "联合养成（独立整队重放）"}
FIELD_LABELS = {"character_level": "角色等级", "breakthrough_stage": "角色突破", "skill_levels": "基础技能",
    "awakening_level": "觉醒数", "selected_awaken_effect_ids": "觉醒效果", "awakening_selection_initialized": "觉醒选择已确认",
    "fork_id": "弧盘", "fork_level": "弧盘等级", "fork_breakthrough_stage": "弧盘突破",
    "fork_refinement_level": "弧盘精炼", "likeability_level_10_enabled": "好感十级",
    "yaodao_attack_add": "妖刀家具攻击", "quantao_crit_damage": "拳套家具暴伤"}
OPERATION_LABELS = {"theoretical_benchmark": "首评价", "current_evaluation": "当前评价", "recommendation_evaluation": "养成建议", "equipment_advice_evaluation": "装备/词条建议"}


def _t(catalog, key, fallback, **values):
    """界面文案统一走冻结目录；未注入目录时输出原中文。"""
    return localized_text(catalog, key, fallback, **values)


def _label(catalog, prefix, table, code):
    text = table.get(code, code)
    return _t(catalog, f"{prefix}.{code}", text)


def _damage(value, catalog=None):
    return _t(catalog, "teams.graduation.axis.value.unknown", "未知") if value is None else f"{value:,.2f}"


def axis_graduation_display(response, target, *, rules=None, historical=False, catalog=None):
    current = response["current"]
    axis = current["axis_evaluation"]
    reference = response["benchmark_reference"]
    p = reference["payload"] if reference is not None else None
    qualifier = (_t(catalog, "teams.graduation.axis.qualifier.outside_target", "超出目标养成/家具条件")
                 if response["status"] == "outside_target"
                 else _t(catalog, "teams.graduation.axis.qualifier.unverified", "轴可执行性未证实"))
    mode = (_t(catalog, "teams.graduation.axis.mode.historical", "历史数学结果") if historical
            else _t(catalog, "teams.graduation.axis.mode.preview", "数学预览"))
    title = _t(catalog, "teams.graduation.axis.headline.default", "轴伤毕业：{qualifier} · {mode}",
               qualifier=qualifier, mode=mode)
    ratio = response.get("fixed_axis_graduation")
    if ratio is not None and ratio["ratio_percent"] is not None:
        scope_mode = (_t(catalog, "teams.graduation.axis.scope.historical", "历史") if historical
                      else _t(catalog, "teams.graduation.axis.scope.current", "本次"))
        title = _t(catalog, "teams.graduation.axis.headline.fixed_ratio", "固定观测轴毕业率：{percent}% · {scope}有界搜索标尺",
                   percent=f"{ratio['ratio_percent']:.2f}", scope=scope_mode)
    damage = _t(catalog, "teams.graduation.axis.damage.current", "当前轴伤（期望）：{damage}", damage=_damage(axis["damage"], catalog))
    if axis["damage"] is None:
        damage += _t(catalog, "teams.graduation.axis.damage.known", "；已建模部分：{damage}", damage=_damage(axis["known_damage"], catalog))
    damage += _t(catalog, "teams.graduation.axis.damage.reference", "\\n理论100%标尺（本次搜索参照）：{damage}",
                 damage=_damage(p["reference_damage"] if p else None, catalog))
    if ratio is not None and ratio["ratio_percent"] is not None:
        damage += _t(catalog, "teams.graduation.axis.damage.gap", "\\n距基准轴伤差：{damage}", damage=_damage(ratio["damage_gap"], catalog))
        gain = ratio["relative_gain_to_reference"]
        gain_text = (f"{gain:.2%}" if gain is not None
                     else _t(catalog, "teams.graduation.axis.value.gain_unknown", "未知（当前为零或数值不可表示）"))
        damage += _t(catalog, "teams.graduation.axis.damage.gain", "；到基准相对变化：{gain}", gain=gain_text)
    meta = response["axis_snapshot"]
    scope = meta["scope"]
    scope_text = scope['detail_scope'] or _t(catalog, "teams.graduation.axis.selected.full_scope", "所选完整范围")
    selected = _t(catalog, "teams.graduation.axis.selected", "参考轴：战报 #{record} · {scope}",
                  record=scope['battle_record_id'], scope=scope_text)
    if scope["start_us"] is not None or scope["end_us"] is not None:
        selected += _t(catalog, "teams.graduation.axis.selected.window", " · {start}–{end} μs",
                       start=scope['start_us'], end=scope['end_us'])
    evidence = [
        _t(catalog, "teams.graduation.axis.evidence.events", "事件量化：{quantified}/{total}；数学状态：{status}",
           quantified=axis['quantified_events'], total=axis['total_events'], status=axis['status']),
        _t(catalog, "teams.graduation.axis.evidence.scoring_domain", "计分域：{hash}",
           hash=axis["scoring_domain_sha256"][:16]),
        _t(catalog, "teams.graduation.axis.evidence.static", "静态：{dataset} / schema {schema}；引擎：{engine}",
           dataset=meta['dataset_id'], schema=meta['static_schema'], engine=response['engine_version']),
        _t(catalog, "teams.graduation.axis.evidence.boundary",
           "固定观测动作与时点不变；实战可执行性未证实。100%是所选有界参照，不是全局最优证明。")]
    if ratio is None:
        evidence.append(_t(catalog, "teams.graduation.axis.evidence.no_ratio",
                           "旧结果未请求固定轴比例；显式更新当前评价后由新组件生成，不在界面补算历史。"))
    elif ratio["ratio_percent"] is None:
        evidence.append(_t(catalog, "teams.graduation.axis.evidence.ratio_missing", "固定轴比例未生成：{status}",
                           status=ratio["status"]))
    elif ratio["above_reference"]:
        evidence.append(_t(catalog, "teams.graduation.axis.evidence.above_reference",
                           "当前高于冻结参照，比例保留超过100%；可能受搜索预算或不同目标养成子域影响，可显式深搜/修订目标。"))
    if p:
        search = p["search"]
        evidence.insert(1, _t(catalog, "teams.graduation.axis.evidence.search",
                              "有界搜索：{evaluated}/{budget} 次；{visited}/{presets} 个预设；停止原因：{stop}",
                              evaluated=search['evaluated_candidates'], budget=search['evaluation_budget'],
                              visited=search['visited_presets'], presets=search['total_presets'],
                              stop=search['stop_reason']))
        evidence.append(_t(catalog, "teams.graduation.axis.evidence.benchmark", "基准：{benchmark}；轴快照：{snapshot}",
                           benchmark=reference["benchmark_id"][-16:], snapshot=meta["snapshot_sha256"][:16]))
    evidence.extend(axis["global_gap_codes"][:16])
    if len(axis["global_gap_codes"]) > 16:
        evidence.append(_t(catalog, "teams.graduation.axis.evidence.global_gaps_more", "另有 {count} 项全局原因未展开。",
                           count=len(axis['global_gap_codes']) - 16))
    gaps = {}
    for event in axis["event_gaps"]:
        for code in event["codes"]:
            gaps[code] = gaps.get(code, 0) + 1
    evidence.extend(_t(catalog, "teams.graduation.axis.evidence.event_gap", "逐击缺口：{code}（{count} 个事件）",
                       code=code, count=count) for code, count in sorted(gaps.items())[:32])
    if len(gaps) > 32:
        evidence.append(_t(catalog, "teams.graduation.axis.evidence.event_gaps_more", "另有 {count} 类逐击缺口未展开。",
                           count=len(gaps) - 32))
    description = target_description(target, rules=rules, catalog=catalog)
    recommendations = response.get("recommendations")
    advice = [_t(catalog, "teams.graduation.axis.advice.pending", "尚未加载建议；可分别显式评价养成与装备/词条。")]
    if recommendations is not None:
        advice = [
            _t(catalog, "teams.graduation.axis.advice.growth",
               "养成建议：{stop}；完成重放 {evaluated} 项；未尝试 {omitted} 项。",
               stop=recommendations['stop_reason'], evaluated=recommendations['evaluated_candidates'],
               omitted=recommendations['omitted_candidates']),
            _t(catalog, "teams.graduation.axis.advice.growth_scope", "本节仅养成，不代表完整装备/词条建议。")]
        for item in recommendations["items"]:
            impact = item["impact"]
            summary = (_t(catalog, "teams.graduation.axis.advice.damage_delta", "伤害差 {value}",
                          value=_damage(impact["damage_delta"], catalog))
                       if impact else
                       _t(catalog, "teams.graduation.axis.advice.impact_unknown", "收益未知：{status}", status=item["status"]))
            advice.append(_t(catalog, "teams.graduation.axis.advice.line", "{label}：{summary}",
                             label=_label(catalog, "teams.graduation.kind", KIND_LABELS, item['kind']), summary=summary))
            for change in item["changes"]:
                advice.append(_t(catalog, "teams.graduation.axis.advice.change", "  {character} · {field}{entry} → {value}",
                                 character=change['character_id'] or _t(catalog, "teams.graduation.axis.advice.team", "队伍"),
                                 field=_label(catalog, "teams.graduation.field", FIELD_LABELS, change['field']),
                                 entry=(' / ' + change['entry_id']) if change['entry_id'] else '',
                                 value=change['value']))
    equipment = response.get("equipment_recommendations")
    if equipment is not None:
        advice = [
            _t(catalog, "teams.graduation.axis.advice.equipment",
               "装备/词条建议：{stop}；完成重放 {evaluated} 项；未尝试 {omitted} 项。",
               stop=equipment['stop_reason'], evaluated=equipment['evaluated_candidates'],
               omitted=equipment['omitted_candidates']),
            _t(catalog, "teams.graduation.axis.advice.equipment_scope",
               "保持当前养成；理论装备不是背包可达或真实装配指令，联合收益独立整队重放。")]
        goals = {a['character_id']: {p['theoretical_piece_id']: p for p in a['equipment']} for a in equipment['target_equipment']}
        labels = {"combined_equipment": "整队联合", "member_equipment": "单人整套", "piece_equipment": "单件词条"}
        for item in equipment['items']:
            impact = item['impact']
            summary = (_t(catalog, "teams.graduation.axis.advice.damage_delta", "伤害差 {value}",
                          value=_damage(impact['damage_delta'], catalog))
                       if impact else
                       _t(catalog, "teams.graduation.axis.advice.impact_unknown", "收益未知：{status}", status=item['status']))
            advice.append(_t(catalog, "teams.graduation.axis.advice.line", "{label}：{summary}",
                             label=labels[item['kind']], summary=summary))
            for change in item['changes']:
                for pid in change['piece_ids']:
                    piece = goals[change['character_id']][pid]
                    stats = "；".join(f"{s['stat_group']}:{s['property_id']}=" + (f"{s['value']:.2%}" if s['is_percent'] else f"{s['value']:g}") for s in piece['stats'])
                    advice.append(_t(catalog, "teams.graduation.axis.advice.piece", "  角色 {character} · {piece} / {item} · 强化 {level}：{stats}",
                                     character=change['character_id'], piece=pid, item=piece['item_id'],
                                     level=piece['level'], stats=stats))
        if equipment['piece_steps_unavailable_members']:
            advice.append(_t(catalog, "teams.graduation.axis.advice.piece_steps_missing",
                             "局部步骤缺少结构对应（只评价完整目标布局）：{members}",
                             members=equipment['piece_steps_unavailable_members']))
    return AxisGraduationDisplay(title, damage, selected, "\\n".join(evidence),
                                 description, "\\n".join(advice),
                                 ratio["ratio_percent"] if ratio else None, response["status"], historical,
                                 json.dumps(target, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")))


def target_description(target, *, rules=None, catalog=None):
    descriptions = [
        _t(catalog, "teams.graduation.target.revision", "目标修订：{revision}；预设 {presets} 组",
           revision=target['revision'], presets=len(target['presets'])),
        _t(catalog, "teams.graduation.target.readonly", "当前展示只读，不修改角色、库存、真实配装或原始战报。"),
        _t(catalog, "teams.graduation.target.equipment_policy", "装备约束：{policy}", policy=target["equipment_policy"]),
        _t(catalog, "teams.graduation.target.world_bonus", "家具：{bonus}", bonus=target["world_bonus"]),
        _t(catalog, "teams.graduation.target.assumptions", "附加假设：{codes}", codes=target["assumption_codes"])]
    for preset in target["presets"]:
        descriptions.append(_t(catalog, "teams.graduation.target.preset", "预设：{preset}", preset=preset["preset_id"]))
        for member in preset["members"]:
            fork = member["fork"]
            descriptions.append(_t(catalog, "teams.graduation.target.member",
                                   "角色 {character}：等级 {level} / 突破 {breakthrough} / 觉醒 {awakening}；弧盘 {fork}",
                                   character=member['character_id'], level=member['character_level'],
                                   breakthrough=member['breakthrough_stage'], awakening=member['awakening_level'],
                                   fork=fork['fork_id'] if fork else _t(catalog, "teams.graduation.target.fork_none", "未装备")))
            descriptions.append(_t(catalog, "teams.graduation.target.awakening",
                                   "  觉醒选择：{selections}；好感十级：{likeability}",
                                   selections=member["selected_awaken_effect_ids"],
                                   likeability=_t(catalog, "teams.graduation.target.yes", "是")
                                   if member["likeability_level_10_enabled"] else _t(catalog, "teams.graduation.target.no", "否")))
            descriptions.append(_t(catalog, "teams.graduation.target.skills", "  基础技能：{skills}",
                                   skills="；".join(f"{s['skill_id']}={s['level']}" for s in member["skill_levels"])))
            if fork:
                descriptions.append(_t(catalog, "teams.graduation.target.fork_levels",
                                       "  弧盘等级 {level} / 突破 {breakthrough} / 精炼 {refinement}",
                                       level=fork['level'], breakthrough=fork['breakthrough_stage'],
                                       refinement=fork['refinement_level']))
        for layout in preset["layouts"]:
            descriptions.append(_t(catalog, "teams.graduation.target.layout",
                                   "角色 {character} 图纸：卡带 {core_item}（{core_piece}）；最低套装数 {minimum_sets}；额外形状 {extra_shapes}",
                                   character=layout['character_id'], core_item=layout['core_item_id'],
                                   core_piece=layout['core_piece_id'], minimum_sets=layout['minimum_set_count'],
                                   extra_shapes=layout['extra_shape_ids']))
            descriptions.extend(_t(catalog, "teams.graduation.target.piece", "  {piece}：{item}，行 {row} / 列 {column}",
                                   piece=p['piece_id'], item=p['item_id'], row=p['row'], column=p['column'])
                                for p in layout["pieces"])
    if rules is not None:
        descriptions.append(_t(catalog, "teams.graduation.target.quality", "品质系数：{value}", value=rules["quality_coefficients"]))
        descriptions.append(_t(catalog, "teams.graduation.target.overlap", "允许主副同属性：{value}", value=rules["main_sub_overlap_allowed"]))
        for key, label in (("core_main_stats", _t(catalog, "teams.graduation.target.pool.core_main", "卡带主属性池")),
                           ("core_sub_stats", _t(catalog, "teams.graduation.target.pool.core_sub", "卡带副属性池")),
                           ("drive_sub_stats", _t(catalog, "teams.graduation.target.pool.drive_sub", "驱动副属性参考池（另按格数/品质缩放）"))):
            descriptions.append(_t(catalog, "teams.graduation.target.pool", "{label}：{values}", label=label,
                                   values="；".join(f"{s['property_id']}={s['display_value']}{'%' if s['is_percent'] else ''}" for s in rules[key])))
        chosen_items = {l["core_item_id"] for p in target["presets"] for l in p["layouts"]}
        chosen_items.update(v["item_id"] for p in target["presets"] for l in p["layouts"] for v in l["pieces"])
        for item in rules["items"]:
            if item["item_id"] in chosen_items:
                descriptions.append(_t(catalog, "teams.graduation.target.item",
                                       "装备 {item}：{quality}；强化 {level}；副词条 {count} 条；格数 {grid}；形状 {geometry}；套装 {suit}；固定主属性 {main}",
                                       item=item['item_id'], quality=item['quality'], level=item['max_level'],
                                       count=item['sub_stat_count'], grid=item['grid_count'],
                                       geometry=item['geometry_id'], suit=item['suit_id'],
                                       main=item['intrinsic_main_stats']))
    return "\\n".join(descriptions)


def axis_history_view(history, *, catalog=None):
    def entry(result):
        return _t(catalog, "teams.graduation.history.entry", "目标 v{revision} · {damage}",
                  revision=result.target.revision,
                  damage=_damage(result.reference.to_payload()['payload']['reference_damage'], catalog))
    evaluations = []
    for result in history.evaluations:
        value = result.to_payload()
        evaluations.append((result.evaluation_id,
                            _t(catalog, "teams.graduation.history.evaluation", "{operation} · 当前 {damage}",
                               operation=_label(catalog, "teams.graduation.operation", OPERATION_LABELS, result.operation),
                               damage=_damage(value['response']['current']['axis_evaluation']['damage'], catalog))))
    result = history.selected_result
    payload = result.to_payload() if result else None
    display = axis_graduation_display(payload["response"], result.target.to_payload(),
                                      rules=payload["request"]["rules"], historical=True, catalog=catalog) if result else None
    return AxisGraduationHistoryView(history.team.team_id, history.target_revision,
        history.selected.revision if history.selected else 0, history.selected.evaluation_id if history.selected else None,
        history.benchmark_id, tuple((v.benchmark_id, entry(v)) for v in history.benchmarks), tuple(evaluations),
        history.benchmark_offset, history.evaluation_offset, history.more_benchmarks, history.more_evaluations, display)


def validity_display(display, validity, *, catalog=None):
    if display is None:
        return None
    label = {
        "benchmark_stale": _t(catalog, "teams.graduation.validity.benchmark_stale", "历史结果 · 基准需重建"),
        "current_stale": _t(catalog, "teams.graduation.validity.current_stale", "历史结果 · 当前评价过期"),
        "component_pending": _t(catalog, "teams.graduation.validity.component_pending", "历史结果 · 组件待就绪"),
        "validity_unverified": _t(catalog, "teams.graduation.validity.unverified", "历史结果 · 有效性待确认"),
        "context_matching": _t(catalog, "teams.graduation.validity.context_matching", "上下文已核对（未重算）"),
    }[validity.state]
    reasons = "；".join(validity.reasons) or label
    return replace(display,
                   headline=_t(catalog, "teams.graduation.validity.headline", "{label}\\n{headline}",
                               label=label, headline=display.headline),
                   summary_status=validity.state,
                   evidence=display.evidence + _t(catalog, "teams.graduation.validity.evidence", "\\n有效性核对：{reasons}",
                                                  reasons=reasons))


def empty_axis_display(*, catalog=None):
    return AxisGraduationDisplay(_t(catalog, "teams.graduation.axis.empty", "轴伤毕业：待建立或选用当前草稿的基准"),
                                 "", "", "", "", "", summary_status="unset")
