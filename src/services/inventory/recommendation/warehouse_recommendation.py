# 在调用方已冻结的仓库业务请求上试算建议，不读取账号或包含状态写入接口。
from __future__ import annotations

from src.observability.stage_timings import timed, timed_call

from collections import Counter, defaultdict
from pathlib import Path
import time
from copy import deepcopy
from functools import partial
from concurrent.futures import CancelledError
from src.domain.equipment.warehouse.warehouse_decisions import predecessor_context, decision_row

from src.domain.inventory.post_actions import preserve_rule_matches_item
from src.domain.equipment.warehouse.intelligent_rules import automatic_main_supported, policy_from_rules
from src.domain.equipment.warehouse.warehouse_rule_order import apply_ordered_rules
from src.domain.equipment.warehouse.warehouse_quality import summarize_warehouse_quality
from src.services.inventory.recommendation.warehouse_recommendation_profiles import (
    freeze_profile_witnesses,
    run_profile_allocation,
    witness_uids,
)
from src.services.inventory.recommendation.warehouse_loss_guard import freeze_loss_witnesses, repair_known_losses

from src.domain.equipment.warehouse.warehouse_recommendation import (
    WarehouseFact,
    future_profiles,
    recommend,
    shape_demands,
)
from src.domain.equipment.properties.stat_catalog import StatCatalog
from src.models.equipment import Drive, Tape
from src.services.equipment.equipment_level_projection_service import project_equipment_items_to_max_level
from src.services.inventory.recommendation.warehouse_recommendation_display import freeze_recommendation_stat_weights
from src.integrations.game_assets.catalog import GameUiAssetCatalog
from src.optimizer.allocation_kernel import AllocationKernelRequest, estimate_candidate_pool_limits
from src.integrations.allocation.native_allocation import create_allocation_executor
from src.integrations.allocation.native_blueprints import create_blueprint_executor
from src.integrations.analysis_core.analysis_core_release import create_bundled_analysis_client
from src.optimizer.scoring import ScoringEngine
from src.services.identification.matching_context import build_equipment_match_context
from src.services.identification.matching_settings import (
    effective_matching_suits,
    set_matching_suits,
)
from src.solver.blueprint_utils import dedupe_blueprints_by_piece_signature
from src.services.inventory.recommendation.warehouse_calculation_inputs import warehouse_orchestrator
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


def _plan_items(plan):
    items = [*plan.get("assigned_set_drives", ()), *plan.get("assigned_extra_drives", ())]
    if plan.get("assigned_tape") is not None:
        items.append(plan["assigned_tape"])
    return items


def _plan_summary(plans):
    return {
        role: {
            "valid": bool(p.get("valid", True)),
            "score": float(p.get("score", 0)),
            "uids": [i.uid for i in _plan_items(p)],
            "shape_counts": dict(Counter(i.shape_id for i in _plan_items(p) if isinstance(i, Drive))),
            "has_core": p.get("assigned_tape") is not None,
        }
        for role, p in plans.items()
        if isinstance(p, dict)
    }


@timed("recommendation.total")
def run_warehouse_trial(
    *,
    inputs,
    static_database: Path,
    config_dir: Path,
    progress=lambda message: None,
    seconds_budget=300,
    cancel_check=lambda: False,
    matching_settings=None,
    preserve_rules=(),
    asset_root=None,
    predecessors=None,
    prepared_scorer=None,
    timings=None,
):
    """仅消费完整冻结请求；全程不连接游戏、不保存配装、不读取最新账号补齐输入。"""
    if not inputs.profiles_frozen:
        raise ValueError('缺少有效的配装校验范围或轮次预算')
    started = time.monotonic()
    rule_policy = policy_from_rules(preserve_rules)

    def cancelled():
        return cancel_check() or time.monotonic() - started > seconds_budget

    def checkpoint():
        if cancel_check():
            raise CancelledError()
        if time.monotonic() - started > seconds_budget:
            raise TimeoutError("仓库试算达到本次时间预算；不生成可执行建议")

    data = timed_call(timings, "recommendation.inventory", inputs.values)
    snapshot_id = inputs.snapshot_id
    originals, locked_owners, item_names = data['originals'], data['locked_owners'], data['item_names']
    raw_by_uid = {
        f"nte-{'module' if row['kind'] == 'module' else 'core'}-{row['uid_slot']}-{row['uid_serial']}": row
        for row in originals
    }
    items = [Drive(**row) if row["item_type"] == "drive" else Tape(**row) for row in data['projected']]
    originals_by_uid = deepcopy(raw_by_uid)
    raw_by_uid, fixed, excluded, fixed_retained = predecessor_context(raw_by_uid, predecessors)
    stats = StatCatalog.from_config_dir(config_dir)
    analysis_client = create_bundled_analysis_client(static_database_path=static_database, cancelled=cancelled)
    blueprint_executor = create_blueprint_executor(analysis_client, cancel_check=cancelled)
    orchestrator = timed_call(timings, "recommendation.catalog", warehouse_orchestrator,
        inputs, static_database, config_dir, cancelled, blueprint_executor=blueprint_executor, timings=timings)
    settings = (
        deepcopy(matching_settings)
        if matching_settings is not None
        else data['matching']
    )
    # 用户本次确认的跨套装用途只加入试算输入，不回写账号，也不按名字模糊匹配。
    by_id = {v["character_id"]: name for name, v in orchestrator.roles_db.items()}
    suits = {str(v["suit_id"]): name for name, v in orchestrator.sets_db.items()}
    cross = []
    if 1036 in by_id and "Suit6" in suits and 'cross_suit' in rule_policy:
        role = by_id[1036]
        default_name = orchestrator.roles_db[role]["default_set"]
        default_id = str(orchestrator.sets_db[default_name]["suit_id"])
        values = (*effective_matching_suits(settings, 1036, default_id), "Suit6")
        set_matching_suits(settings, 1036, default_id, values)
        cross.append({"character_id": 1036, "suit_id": "Suit6", "source": "user_confirmed_trial"})
    progress("准备全角色合法套装与蓝图")
    context = timed_call(timings, "recommendation.matching", build_equipment_match_context,orchestrator, settings, cancel_check=cancelled, batch_defaults=True)
    scorer = prepared_scorer or ScoringEngine(
        config_dir,
        static_database_path=static_database,
        roles_db=orchestrator.roles_db,
        stat_catalog=stats,
    )
    # 全角色按正式身份固定排序，不用装备多少或旧勾选决定谁值得保留。
    selected = sorted(orchestrator.roles_db, key=lambda r: orchestrator.roles_db[r]['character_id'])
    if not selected:
        raise ValueError("没有可用于试算的角色")
    blueprints = timed_call(timings, "recommendation.blueprints", orchestrator.solve_blueprints, selected, cancel_check=cancelled)
    blueprints = {r: dedupe_blueprints_by_piece_signature(v) for r, v in blueprints.items()}
    demands = shape_demands(blueprints)
    all_blueprints = blueprints
    multiplicity = defaultdict(int)
    for groups in all_blueprints.values():
        for group in dedupe_blueprints_by_piece_signature(groups):
            counts = Counter((*group.get("set_pieces", ()), *group.get("extra_pieces", ())))
            for shape, count in counts.items():
                multiplicity[shape] = max(multiplicity[shape], count)
    role_profiles = [
        {"key": r, "weights": v.get("weights") or {}, "main_weights": v.get("main_weights"), "max_weight": None}
        for r, v in orchestrator.roles_db.items()
    ]
    main_names = sorted({stats.normalize_tape_main_stat(i.main_stats) for i in items if isinstance(i, Tape)})
    weights = [v.get("main_weights") or v.get("weights") or {} for v in orchestrator.roles_db.values()]
    scorer.prepare_weight_queries(weights, main_names)
    main_allowed = {
        r: {m for m in main_names if scorer.flexible_weight(m, v.get("main_weights") or v.get("weights") or {}) > 0}
        for r, v in orchestrator.roles_db.items()
    }
    drive_limit, tape_limit = estimate_candidate_pool_limits(blueprints, selected)
    request = AllocationKernelRequest(
        inventory=tuple(item for item in items if item.uid not in excluded),
        # 本检查没有暴击上下限；未冻结角色暴击面板时明确传未知，不伪造零值。
        roles_db={r: {**data, 'crit_source_known': data.get('crit_source_known', False)}
                  for r, data in orchestrator.roles_db.items()},
        sets_db=orchestrator.sets_db,
        shapes_db=orchestrator.shapes_db,
        blueprints_db=blueprints,
        role_order=tuple(selected),
        strategy="role_priority",
        module_set_targets={},
        set_effect_modes={},
        core_main_filters={r: tuple(sorted(main_allowed[r])) for r in selected},
        core_set_targets={},
        stat_priority_configs={},
        property_limits={},
        drive_screen_limit=drive_limit,
        tape_screen_limit=tape_limit,
        blueprint_combo_limit=2000,
        cancel_check=cancelled,
    )
    progress("使用 Rust 配装内核，建立全角色可行配装见证")
    native_execute = timed_call(timings, "witness.native_prepare", create_allocation_executor,
        static_database_path=static_database, cancel_check=cancelled, analysis_client=analysis_client)
    execute = partial(native_execute, scorer=scorer)
    baseline = timed_call(timings, "witness.all_roles", execute, request)
    before = _plan_summary(baseline)
    progress('复核已保存配装偏好的实际约束')
    solve_profile = partial(run_profile_allocation, executor=native_execute, timings=timings,
                            blueprint_executor=blueprint_executor)
    profile_witnesses, profile_issues = freeze_profile_witnesses(
        inputs, checkpoint, cancelled,
        solve=solve_profile, excluded_uids=frozenset(excluded))
    incomplete = bool(
        profile_issues
        or context.errors
        or context.missing_character_ids
        or any(not before.get(r, {}).get("valid") for r in selected)
    )
    baseline_uids = frozenset(uid for row in before.values() if row["valid"] for uid in row["uids"])
    baseline_uids |= witness_uids(profile_witnesses)
    # 锁定配装即使不参与本次常用角色也必须保留。
    locked_plan_uids = frozenset(
        f"nte-{'module' if r['kind'] == 'module' else 'core'}-{r['uid_slot']}-{r['uid_serial']}" for r in locked_owners
    )
    baseline_uids |= locked_plan_uids
    timed_call(timings, "recommendation.batch_scores", scorer.prepare_scores,
        items, profiles=[*role_profiles, *future_profiles()], assign_scores=True, checkpoint=checkpoint
    )
    facts = []
    for item in items:
        raw = raw_by_uid[item.uid]
        roles = {m.role for m in context.for_item(item)}
        if item.uid in excluded:
            roles = set()
        main = stats.normalize_tape_main_stat(item.main_stats) if isinstance(item, Tape) else ""
        if isinstance(item, Tape):
            roles = {r for r in roles if main in main_allowed[r]}
        facts.append(
            WarehouseFact(
                item.uid,
                item.item_type,
                str(getattr(item, "suit_id", "") or ""),
                item.shape_id,
                main,
                item.area,
                item.quality,
                tuple(item.sub_stats.items()),
                tuple(item.role_scores.items()),
                tuple(sorted(roles)),
                bool(raw.get("equipped")),
                bool(raw.get("locked")),
                bool(raw.get("discarded")),
            )
        )
    quality_started = time.perf_counter()
    quality = summarize_warehouse_quality(facts, demands)
    quality['statistics_seconds'] = time.perf_counter() - quality_started
    quality['recommended_drive_grade'] = (scorer.get_grade_tag(quality['drive_rate'] * 10, 1)
                                           if quality['drive_rate'] is not None else None)
    progress("按当前用途、明确未来组合和备用桶生成建议")
    # 同一匹配结果供保护、预算和最终仲裁共用，避免自动规则在另一条入口绕过主属性用途校验。
    matched_by_uid = {item.uid: [] if item.uid in fixed else [rule for rule in preserve_rules
        if preserve_rule_matches_item(item, rule) and automatic_main_supported(rule, fact)]
        for item, fact in zip(items, facts)}
    arguments = {
        "selected_roles": selected,
        "demands": demands,
        "baseline_uids": baseline_uids,
        "incomplete": incomplete,
        "known_shape_multiplicity": dict(multiplicity),
        "policy": rule_policy,
        "target_inventory_size": rule_policy.get('future', {}).get('target_inventory_size', 0),
        "budget_protected_uids": frozenset(fixed_retained | {uid for uid, rules in matched_by_uid.items()
                                          if rules and rules[0].get('action') in {'keep', 'lock'}}),
        # 只预留首条静态规则的保护；最终动作仍与动态命中按完整列表统一排序。
        "rule_protection": {item.uid: ([('keep', '前序决定保留')] if item.uid in fixed_retained else
            [(r['action'], str(r.get('name') or '智能规则'))
             for r in matched_by_uid[item.uid][:1] if r.get('intelligent')]) for item in items},
    }
    # 对照计算也消费同一规则输入，只关闭未来储备，用于核对储备没有扩大弃置集合。
    control_sizes = {} if timings is not None else None
    rule_sizes = {} if timings is not None else None
    without_future = timed_call(timings, "recommendation.control", recommend, facts, **{**arguments, 'policy': {k: v for k, v in rule_policy.items() if k != 'future'},
                                       'rule_protection': {}}, diagnostics=control_sizes)
    result = timed_call(timings, "recommendation.rules", recommend, facts, **arguments, diagnostics=rule_sizes)
    if timings is not None:
        timings.note("recommendation.control_sizes", **control_sizes)
        timings.note("recommendation.rule_sizes", **rule_sizes)
    protected_uids = locked_plan_uids | frozenset(f.uid for f in facts if f.locked or f.equipped)
    for candidate_result in (without_future, result):
        for row in candidate_result['rows']:
            if row['uid'] in fixed:
                row.update(decision_row(fixed[row['uid']], originals_by_uid[row['uid']]))
        apply_ordered_rules(candidate_result, items, preserve_rules, matched_by_uid=matched_by_uid,
                            protected_uids=protected_uids, incomplete=incomplete)
    for row, fact in zip(result['rows'], facts):
        original = originals_by_uid[fact.uid]
        row.update(original_locked=original['locked'], original_equipped=original['equipped'], original_discarded=original['discarded'],
                   state_known=all(original.get(k) is not None for k in ('locked','discarded','equipped')))
    progress('冻结全角色、不同优先级及已保存配装的已知基线')
    witnesses = freeze_loss_witnesses(execute,request,before,
                                    profile_witnesses,checkpoint,cancelled,_plan_summary,solve_profile=solve_profile,timings=timings)
    loss_check = repair_known_losses(result['rows'],witnesses,checkpoint,progress=progress,timings=timings)
    after = loss_check['after']['all_roles']
    loss = [f"{row['scope']}:{row['role']}" for row in loss_check['losses']]
    ablation = {r["uid"]: r["decision"] for r in without_future["rows"]}
    assets = GameUiAssetCatalog(asset_root or static_database.parent / 'game_ui')
    with StaticGameDataDao(static_database) as static:
        projected_display = project_equipment_items_to_max_level(originals, static, include_projection_evidence=True)
    display_by_uid = {(r['uid_slot'], r['uid_serial']): r for r in projected_display}
    grade_keys = list(dict.fromkeys((fact.score(role), fact.area) for fact in facts for role in fact.eligible_roles))
    grades = dict(zip(grade_keys, scorer.get_grade_tags(grade_keys, checkpoint=checkpoint)))
    for row, fact, item in zip(result["rows"], facts, items):
        raw = raw_by_uid[fact.uid]
        # 卡片只消费本次快照及同源图片，不在 UI 重新读取当前库存。
        display_item = {key: deepcopy(raw.get(key)) for key in (
            'kind', 'item_id', 'geometry', 'quality', 'level', 'max_level', 'names', 'suit_names',
            'suit_id', 'main_stats', 'sub_stats', 'uid_slot', 'uid_serial',
        )}
        projected = display_by_uid[(raw['uid_slot'], raw['uid_serial'])]
        if projected['main_stats_at_max_level']:
            display_item['main_stats'] = deepcopy(projected['main_stats'])
            display_item['main_stats_at_max_level'] = True
        icon = assets.inventory_item_icon(raw['kind'], item.item_id)
        display_item['item_icon_path'] = str(icon) if icon else None
        role = max(fact.eligible_roles, key=fact.score, default=None)
        role_id = (orchestrator.roles_db.get(role) or {}).get('character_id')
        portrait = assets.character_icon(role_id) if role_id is not None else None
        row.update(
            display_item=display_item,
            best_role_icon_path=str(portrait) if portrait else None,
            kind=fact.kind,
            suit_id=fact.suit_id,
            set_name=getattr(item, "set_name", ""),
            shape_id=fact.shape_id,
            item_name=item_names.get(item.item_id) or '',
            main_stat=fact.main_stat,
            main_stats=dict(item.main_stats) if isinstance(item, Drive) else {},
            quality=fact.quality,
            stats=dict(fact.stats),
            eligible_roles=list(fact.eligible_roles),
            current_scores={r: fact.score(r) for r in fact.eligible_roles},
            current_grades={r: grades[(fact.score(r), fact.area)] for r in fact.eligible_roles},
            future_scores={k: v for k, v in fact.scores if k.startswith("future:")},
            area=fact.area,
            best_current_score=max((fact.score(r) for r in fact.eligible_roles), default=None),
            best_current_grade=(grades[(max(fact.score(r) for r in fact.eligible_roles), fact.area)]
                                if fact.eligible_roles else None),
            state_known=all(originals_by_uid[fact.uid].get(k) is not None for k in ("locked", "discarded", "equipped")),
            original_locked=originals_by_uid[fact.uid]['locked'],
            original_equipped=originals_by_uid[fact.uid]['equipped'],
            original_discarded=originals_by_uid[fact.uid]['discarded'],
            without_future=ablation[fact.uid],
        )
    result.update(
        loss_check=loss_check,
        stat_weight_profiles=freeze_recommendation_stat_weights(scorer, originals),
        limitations=profile_issues + [message for missing, message in (
            (bool(context.errors or context.missing_character_ids), '部分角色适配或评分资料不足，未确认用途的装备保留待核对。'),
            (any(not before.get(r, {}).get('valid') for r in selected), '部分角色未找到有效配装，本次不产生算法弃置。'),
            (any(not p.get('has_core') for p in loss_check['before']['all_roles'].values()),
             '部分角色在原库存及当前匹配条件下就缺少空幕；全角色校验要求不扩大原有缺口。'),
        ) if missing],
        snapshot_id=snapshot_id,
        total_items=len(items),
        counts_by_kind={
            kind: dict(Counter(r["decision"] for r in result["rows"] if r["kind"] == kind))
            for kind in ("tape", "drive")
        },
        evaluated_characters=[{"name": r, "id": orchestrator.roles_db[r]["character_id"]} for r in selected],
        warehouse_quality=quality,
        warehouse_score=quality['score'],
        role_count=len(orchestrator.roles_db),
        blueprint_counts={r: len(v) for r, v in blueprints.items()},
        shape_demands=demands,
        cross_suit_uses=cross,
        baseline=before,
        after_removal=after,
        known_shape_multiplicity=dict(multiplicity),
        scoring_profiles=role_profiles,
        reallocation_regressions=loss,
        verified_profile_count=len(profile_witnesses),
        rule_order=[{'position': index, 'name': r.get('name') or '未命名规则', 'smart_kind': r.get('smart_kind'),
                     'action': r.get('action'), 'enabled': bool(r.get('enabled'))}
                    for index, r in enumerate(preserve_rules, 1)],
        context_errors=list(context.errors),
        missing_character_ids=list(context.missing_character_ids),
        elapsed_seconds=time.monotonic() - started,
        scope="offline_trial_only",
        future_policy="saved combination rules and per-use reserve; distinct useful-stat signatures; known blueprint shape multiplicity",
    )
    result["counts"] = dict(Counter(r["decision"] for r in result["rows"]))
    result["violations"].extend("reallocation_loss:" + r for r in loss)
    if timings is not None:
        timings.note("recommendation", role_count=len(selected), item_count=len(items),
                     blueprint_count=sum(len(v) for v in blueprints.values()), scorer_reuse_hit=prepared_scorer is not None)
    return result
