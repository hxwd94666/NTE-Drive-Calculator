# 将已校验固定轴比例与数学证据投影成展示文本；不计算伤害，不把假设重放包装成实战可兑现保证。
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AxisGraduationDisplay:
    headline: str
    damage: str
    scope: str
    evidence: str
    target: str
    advice: str


@dataclass(frozen=True, slots=True)
class AxisGraduationHistoryView:
    team_id: str
    target_revision: int
    selection_revision: int
    selected_evaluation_id: str | None
    benchmark_id: str | None
    benchmarks: tuple[tuple[str, str], ...]
    evaluations: tuple[tuple[str, str], ...]
    benchmark_offset: int
    evaluation_offset: int
    more_benchmarks: bool
    more_evaluations: bool
    display: AxisGraduationDisplay | None


KIND_LABELS = {"growth": "等级/突破", "skill_with_growth": "技能及成长前置", "awakening": "觉醒",
    "fork": "弧盘", "likeability": "好感", "world": "家具", "combined_cultivation": "联合养成（独立整队重放）"}
FIELD_LABELS = {"character_level": "角色等级", "breakthrough_stage": "角色突破", "skill_levels": "基础技能",
    "awakening_level": "觉醒数", "selected_awaken_effect_ids": "觉醒效果", "awakening_selection_initialized": "觉醒选择已确认",
    "fork_id": "弧盘", "fork_level": "弧盘等级", "fork_breakthrough_stage": "弧盘突破",
    "fork_refinement_level": "弧盘精炼", "likeability_level_10_enabled": "好感十级",
    "yaodao_attack_add": "妖刀家具攻击", "quantao_crit_damage": "拳套家具暴伤"}
OPERATION_LABELS = {"theoretical_benchmark": "首评价", "current_evaluation": "当前评价", "recommendation_evaluation": "养成建议", "equipment_advice_evaluation": "装备/词条建议"}


def _damage(value):
    return "未知" if value is None else f"{value:,.2f}"


def axis_graduation_display(response, target, *, rules=None, historical=False):
    current = response["current"]
    axis = current["axis_evaluation"]
    reference = response["benchmark_reference"]
    p = reference["payload"] if reference is not None else None
    qualifier = "超出目标养成/家具条件" if response["status"] == "outside_target" else "轴可执行性未证实"
    title = f"轴伤毕业：{qualifier} · {'历史数学结果' if historical else '数学预览'}"
    ratio = response.get("fixed_axis_graduation")
    if ratio is not None and ratio["ratio_percent"] is not None:
        title = f"固定观测轴毕业率：{ratio['ratio_percent']:.2f}% · {'历史' if historical else '本次'}有界搜索标尺"
    damage = "当前轴伤（期望）：" + _damage(axis["damage"])
    if axis["damage"] is None:
        damage += "；已建模部分：" + _damage(axis["known_damage"])
    damage += "\n理论100%标尺（本次搜索参照）：" + _damage(p["reference_damage"] if p else None)
    if ratio is not None and ratio["ratio_percent"] is not None:
        damage += "\n距基准轴伤差：" + _damage(ratio["damage_gap"])
        gain = ratio["relative_gain_to_reference"]
        damage += "；到基准相对变化：" + (f"{gain:.2%}" if gain is not None else "未知（当前为零或数值不可表示）")
    meta = response["axis_snapshot"]
    scope = meta["scope"]
    selected = f"参考轴：战报 #{scope['battle_record_id']} · {scope['detail_scope'] or '所选完整范围'}"
    if scope["start_us"] is not None or scope["end_us"] is not None:
        selected += f" · {scope['start_us']}–{scope['end_us']} μs"
    evidence = [f"事件量化：{axis['quantified_events']}/{axis['total_events']}；数学状态：{axis['status']}",
        "计分域：" + axis["scoring_domain_sha256"][:16],
        f"静态：{meta['dataset_id']} / schema {meta['static_schema']}；引擎：{response['engine_version']}",
        "固定观测动作与时点不变；实战可执行性未证实。100%是所选有界参照，不是全局最优证明。"]
    if ratio is None:
        evidence.append("旧结果未请求固定轴比例；显式更新当前评价后由新组件生成，不在界面补算历史。")
    elif ratio["ratio_percent"] is None:
        evidence.append("固定轴比例未生成：" + ratio["status"])
    elif ratio["above_reference"]:
        evidence.append("当前高于冻结参照，比例保留超过100%；可能受搜索预算或不同目标养成子域影响，可显式深搜/修订目标。")
    if p:
        search = p["search"]
        evidence.insert(1, f"有界搜索：{search['evaluated_candidates']}/{search['evaluation_budget']} 次；"
            f"{search['visited_presets']}/{search['total_presets']} 个预设；停止原因：{search['stop_reason']}")
        evidence.append("基准：" + reference["benchmark_id"][-16:] + "；轴快照：" + meta["snapshot_sha256"][:16])
    evidence.extend(axis["global_gap_codes"][:16])
    if len(axis["global_gap_codes"]) > 16:
        evidence.append(f"另有 {len(axis['global_gap_codes'])-16} 项全局原因未展开。")
    gaps = {}
    for event in axis["event_gaps"]:
        for code in event["codes"]:
            gaps[code] = gaps.get(code, 0) + 1
    evidence.extend(f"逐击缺口：{code}（{count} 个事件）" for code, count in sorted(gaps.items())[:32])
    if len(gaps) > 32:
        evidence.append(f"另有 {len(gaps)-32} 类逐击缺口未展开。")
    description = target_description(target, rules=rules)
    recommendations = response.get("recommendations")
    advice = ["尚未加载建议；可分别显式评价养成与装备/词条。"]
    if recommendations is not None:
        advice = [f"养成建议：{recommendations['stop_reason']}；完成重放 {recommendations['evaluated_candidates']} 项；"
                  f"未尝试 {recommendations['omitted_candidates']} 项。", "本节仅养成，不代表完整装备/词条建议。"]
        for item in recommendations["items"]:
            impact = item["impact"]
            summary = "伤害差 " + _damage(impact["damage_delta"]) if impact else "收益未知：" + item["status"]
            advice.append(f"{KIND_LABELS.get(item['kind'], item['kind'])}：{summary}")
            for change in item["changes"]:
                advice.append(f"  {change['character_id'] or '队伍'} · {FIELD_LABELS.get(change['field'], change['field'])}"
                    f"{(' / '+change['entry_id']) if change['entry_id'] else ''} → {change['value']}")
    equipment = response.get("equipment_recommendations")
    if equipment is not None:
        advice = [f"装备/词条建议：{equipment['stop_reason']}；完成重放 {equipment['evaluated_candidates']} 项；未尝试 {equipment['omitted_candidates']} 项。",
                  "保持当前养成；理论装备不是背包可达或真实装配指令，联合收益独立整队重放。"]
        goals = {a['character_id']: {p['theoretical_piece_id']: p for p in a['equipment']} for a in equipment['target_equipment']}
        labels = {"combined_equipment": "整队联合", "member_equipment": "单人整套", "piece_equipment": "单件词条"}
        for item in equipment['items']:
            impact = item['impact']
            advice.append(labels[item['kind']] + "：" + ("伤害差 " + _damage(impact['damage_delta']) if impact else "收益未知：" + item['status']))
            for change in item['changes']:
                for pid in change['piece_ids']:
                    piece = goals[change['character_id']][pid]
                    stats = "；".join(f"{s['stat_group']}:{s['property_id']}=" + (f"{s['value']:.2%}" if s['is_percent'] else f"{s['value']:g}") for s in piece['stats'])
                    advice.append(f"  角色 {change['character_id']} · {pid} / {piece['item_id']} · 强化 {piece['level']}：{stats}")
        if equipment['piece_steps_unavailable_members']:
            advice.append("局部步骤缺少结构对应（只评价完整目标布局）：" + str(equipment['piece_steps_unavailable_members']))
    return AxisGraduationDisplay(title, damage, selected, "\n".join(evidence),
                                 description, "\n".join(advice))


def target_description(target, *, rules=None):
    descriptions = [f"目标修订：{target['revision']}；预设 {len(target['presets'])} 组",
                    "当前展示只读，不修改角色、库存、真实配装或原始战报。",
                    "装备约束：" + target["equipment_policy"],
                    "家具：" + str(target["world_bonus"]), "附加假设：" + str(target["assumption_codes"])]
    for preset in target["presets"]:
        descriptions.append("预设：" + preset["preset_id"])
        for member in preset["members"]:
            fork = member["fork"]
            descriptions.append(f"角色 {member['character_id']}：等级 {member['character_level']} / 突破 {member['breakthrough_stage']} / "
                f"觉醒 {member['awakening_level']}；弧盘 {fork['fork_id'] if fork else '未装备'}")
            descriptions.append("  觉醒选择：" + str(member["selected_awaken_effect_ids"])
                                + "；好感十级：" + ("是" if member["likeability_level_10_enabled"] else "否"))
            descriptions.append("  基础技能：" + "；".join(f"{s['skill_id']}={s['level']}" for s in member["skill_levels"]))
            if fork:
                descriptions.append(f"  弧盘等级 {fork['level']} / 突破 {fork['breakthrough_stage']} / 精炼 {fork['refinement_level']}")
        for layout in preset["layouts"]:
            descriptions.append(f"角色 {layout['character_id']} 图纸：卡带 {layout['core_item_id']}（{layout['core_piece_id']}）；"
                f"最低套装数 {layout['minimum_set_count']}；额外形状 {layout['extra_shape_ids']}")
            descriptions.extend(f"  {p['piece_id']}：{p['item_id']}，行 {p['row']} / 列 {p['column']}" for p in layout["pieces"])
    if rules is not None:
        descriptions.append("品质系数：" + str(rules["quality_coefficients"]))
        descriptions.append("允许主副同属性：" + str(rules["main_sub_overlap_allowed"]))
        for key, label in (("core_main_stats", "卡带主属性池"), ("core_sub_stats", "卡带副属性池"), ("drive_sub_stats", "驱动副属性参考池（另按格数/品质缩放）")):
            descriptions.append(label + "：" + "；".join(f"{s['property_id']}={s['display_value']}{'%' if s['is_percent'] else ''}" for s in rules[key]))
        chosen_items = {l["core_item_id"] for p in target["presets"] for l in p["layouts"]}
        chosen_items.update(v["item_id"] for p in target["presets"] for l in p["layouts"] for v in l["pieces"])
        for item in rules["items"]:
            if item["item_id"] in chosen_items:
                descriptions.append(f"装备 {item['item_id']}：{item['quality']}；强化 {item['max_level']}；副词条 {item['sub_stat_count']} 条；"
                    f"格数 {item['grid_count']}；形状 {item['geometry_id']}；套装 {item['suit_id']}；固定主属性 {item['intrinsic_main_stats']}")
    return "\n".join(descriptions)


def axis_history_view(history):
    def entry(result):
        return f"目标 v{result.target.revision} · {_damage(result.reference.to_payload()['payload']['reference_damage'])}"
    evaluations = []
    for result in history.evaluations:
        value = result.to_payload()
        evaluations.append((result.evaluation_id, f"{OPERATION_LABELS[result.operation]} · 当前 {_damage(value['response']['current']['axis_evaluation']['damage'])}"))
    result = history.selected_result
    payload = result.to_payload() if result else None
    display = axis_graduation_display(payload["response"], result.target.to_payload(),
                                      rules=payload["request"]["rules"], historical=True) if result else None
    return AxisGraduationHistoryView(history.team.team_id, history.target_revision,
        history.selected.revision if history.selected else 0, history.selected.evaluation_id if history.selected else None,
        history.benchmark_id, tuple((v.benchmark_id, entry(v)) for v in history.benchmarks), tuple(evaluations),
        history.benchmark_offset, history.evaluation_offset, history.more_benchmarks, history.more_evaluations, display)
