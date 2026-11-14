# 对冻结装备评分和蓝图需求生成只读保留建议，不读取账号或执行游戏操作。
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from math import ceil, isfinite
from typing import Mapping, Sequence

from src.domain.equipment.scoring.grade_limits import GRADE_MIN_RATIOS
from src.domain.equipment.warehouse.warehouse_reserve_budget import apply_reserve_budget
from src.domain.equipment.warehouse.warehouse_diversity import trim_similar_reserves
from src.domain.localization.catalog import TextCatalog, localized_text


CRIT = frozenset({"暴击率%", "暴击伤害%"})
SUPPORT = frozenset({"伤害增加%", "环合强度", "攻击力%"})
HP = frozenset({"生命值%", "生命值"})
DEF = frozenset({"防御力%", "防御力"})
ATK = frozenset({"攻击力%", "攻击力"})
OFFENSIVE_MAINS = frozenset({"暴击率%", "暴击伤害%", "攻击力%"})
FUTURE_PREFIX = "future:"
# 已审查的元素套用途，以正式套装 ID 连接；跨属性用途必须另有当前角色适配证据。
SUIT_ELEMENT_MAINS = {
    'Suit1': '暗属性异能伤害增强%', 'Suit2': '灵属性异能伤害增强%',
    'Suit3': '魂属性异能伤害增强%', 'Suit4': '咒属性异能伤害增强%',
    'Suit5': '相属性异能伤害增强%', 'Suit6': '光属性异能伤害增强%', 'Suit12': '心灵伤害增强%',
}
# 理由前缀是仓库多样性、储备预算与规则顺序共用的内部理由码；中文句子按
# warehouse.recommendation.reason.* 取词，内部分支仍按原前缀判断，不随译文变化。
_RULE_GUARD_PREFIX = "规则保护："
_RULE_GUARD_TEXT = _RULE_GUARD_PREFIX + "{name}"
_BASELINE_TEXT = "全角色可行配装见证"
_EQUIPPED_TEXT = "当前已装备，维持现状"
_GRADE_PREFIX = "评级保留："
_GRADE_TEXT = _GRADE_PREFIX + "全角色最高有效评级达到 {grade} 及以上"
_CURRENT_USE_PREFIX = "当前用途储备："
_CURRENT_USE_TEXT = _CURRENT_USE_PREFIX + "{role}／{shape}，第{index}候选"
_FUTURE_USE_PREFIX = "未来用途候选："
_FUTURE_USE_TEXT = _FUTURE_USE_PREFIX + "{profile}"
_REVIEW_INCOMPLETE_TEXT = "数据或搜索覆盖不完整"
_REVIEW_UNKNOWN_TEXT = "没有已确认用途或同用途候选，保留待判断"
_DISCARD_COVERED_TEXT = "当前用途已有评分候选且未命中本次储备；不等同于逐件伤害不降，须复核属性取舍"
_LOCKED_TEXT = "已有锁定保持，不自动解锁"


@dataclass(frozen=True)
class WarehouseFact:
    uid: str
    kind: str
    suit_id: str
    shape_id: str
    main_stat: str
    area: int
    quality: str
    stats: tuple[tuple[str, float], ...]
    scores: tuple[tuple[str, float], ...]
    eligible_roles: tuple[str, ...]
    equipped: bool = False
    locked: bool = False
    discarded: bool = False

    def score(self, role: str) -> float:
        return dict(self.scores).get(role, 0.0)


def future_profiles() -> list[dict]:
    """等权只表达用户的储备偏好；分数仍由公共评分器计算，不冒充未来角色模型。"""
    profiles = []
    for key, primary, mains in (
        ("attack", ATK, OFFENSIVE_MAINS),
        ("health", HP, {"生命值%", *OFFENSIVE_MAINS}),
        ("defense", DEF, {"防御力%", *OFFENSIVE_MAINS}),
        ("health_heal", HP, {"治疗加成", "生命值%"}),
        ("defense_heal", DEF, {"治疗加成", "防御力%"}),
    ):
        stats = primary | CRIT | SUPPORT
        profiles.append(
            {
                "key": FUTURE_PREFIX + key,
                "weights": {k: 1.0 for k in sorted(stats)},
                "main_weights": {k: 1.0 for k in sorted(mains)},
                "max_weight": None,
            }
        )
    return profiles


def future_eligible(item: WarehouseFact, profile: Mapping) -> bool:
    # 未来假设不足以撤销用户已作出的弃置决定；明确规则、当前配装和原装备保护独立处理。
    if item.discarded:
        return False
    stats = {k for k, v in item.stats if isfinite(v) and v > 0}
    key = profile["key"].removeprefix(FUTURE_PREFIX)
    scaling = HP if key.startswith("health") else DEF if key.startswith("defense") else ATK
    # 驱动和非明确保护组合都须有实际同类副属性；三个有效词条避免只因主属性合适而储备。
    if not stats & scaling or len(stats & profile["weights"].keys()) < 3:
        return False
    if item.kind == "tape":
        main_ok = item.main_stat in profile["main_weights"]
        elemental = "属性异能伤害增强" in item.main_stat or item.main_stat == "心灵伤害增强%"
        if not item.eligible_roles:
            if elemental and SUIT_ELEMENT_MAINS.get(item.suit_id) != item.main_stat:
                return False
            if item.main_stat == '治疗加成':
                supported = {'health_heal': {'Suit9', 'Suit10'}, 'defense_heal': {'Suit7'}}
                if item.suit_id not in supported.get(key, set()):
                    return False
        if not (main_ok or (elemental and not key.endswith("_heal"))):
            return False
    return True


def shape_demands(blueprints: Mapping[str, Sequence[Mapping]]) -> dict[str, dict[str, float]]:
    """输入已由公共组合签名去重；保留每角色平均形状需求，不把平均数当必需数量。"""
    result = {}
    for role, groups in blueprints.items():
        count = Counter()
        for group in groups:
            count.update((*group.get("set_pieces", ()), *group.get("extra_pieces", ())))
        result[role] = {s: n / len(groups) for s, n in count.items()} if groups else {}
    return result


def _rank(items, key):
    return sorted(items, key=lambda i: (-i.score(key), i.uid))


def _frontier(items, keys):
    """仅同资格分桶内按副属性比较取舍；非支配本身不构成锁定或弃置证明。"""
    vectors = {i.uid: tuple(dict(i.stats).get(k, 0.0) for k in keys) for i in items}
    result = []
    for item in items:
        a = vectors[item.uid]
        dominated = False
        for other in items:
            if other.uid == item.uid:
                continue
            b = vectors[other.uid]
            if all(y >= x for x, y in zip(a, b)) and any(y > x for x, y in zip(a, b)):
                dominated = True
                break
        if not dominated:
            result.append(item)
    return result


def _note_reason(text_catalog, reasons, reason_texts, uid, key, template, **values):
    """内部理由保留固定前缀供业务分支判断，展示文案单独按语言目录取词。"""
    canonical = template.format_map(defaultdict(str, values))
    reasons[uid].append(canonical)
    reason_texts[uid][canonical] = localized_text(text_catalog, key, template, **values)


def recommend(
    items: Sequence[WarehouseFact],
    *,
    selected_roles: Sequence[str],
    demands: Mapping[str, Mapping[str, float]],
    baseline_uids: frozenset[str],
    policy: Mapping,
    incomplete: bool = False,
    known_shape_multiplicity: Mapping[str, int] | None = None,
    rule_protection: Mapping | None = None,
    budget_protected_uids: frozenset[str] = frozenset(),
    target_inventory_size: int | None = None,
    diagnostics: dict[str, int] | None = None,
    text_catalog: TextCatalog | None = None,
) -> dict:
    """先保留可行见证和各用途最佳件，再产生同资格桶内的冗余建议。"""
    drive_keep_grade = policy.get('grade', {}).get('grade')
    if drive_keep_grade is not None and drive_keep_grade not in GRADE_MIN_RATIOS:
        raise ValueError("未知的驱动保留评级")
    if len({i.uid for i in items}) != len(items):
        raise ValueError("真实装备身份重复")
    all_ids = {i.uid for i in items}
    if not baseline_uids <= all_ids:
        raise ValueError("配装见证引用仓库外装备")
    reasons = defaultdict(list)
    reason_texts = defaultdict(dict)
    locks = set(baseline_uids) if policy.get('baseline', {}).get('action') == 'lock' else set()
    current_locks = set()
    eligible = set()
    coverage = []
    replacements = defaultdict(set)
    guards = defaultdict(list)
    for item in items:
        for action, name in (rule_protection or {}).get(item.uid, ()):
            if action == 'lock':
                locks.add(item.uid)
            if action in {'keep', 'lock'}:
                _note_reason(text_catalog, reasons, reason_texts, item.uid,
                             "warehouse.recommendation.reason.rule_guard", _RULE_GUARD_TEXT, name=name)
                guards[item.uid].append(name)
        if item.uid in baseline_uids:
            _note_reason(text_catalog, reasons, reason_texts, item.uid,
                         "warehouse.recommendation.reason.baseline_witness", _BASELINE_TEXT)
        if item.equipped:
            _note_reason(text_catalog, reasons, reason_texts, item.uid,
                         "warehouse.recommendation.reason.equipped_kept", _EQUIPPED_TEXT)
        if (
            item.kind == "drive"
            and drive_keep_grade is not None
            and item.eligible_roles
            and max(item.score(r) for r in item.eligible_roles) >= item.area * 10 * GRADE_MIN_RATIOS[drive_keep_grade]
        ):
            _note_reason(text_catalog, reasons, reason_texts, item.uid,
                         "warehouse.recommendation.reason.grade_keep", _GRADE_TEXT, grade=drive_keep_grade)
    # 空幕对所有正式角色评分；按套装与主属性分桶，不用其他角色的低分稀释专用价值。
    buckets = defaultdict(list)
    for item in items:
        if item.kind == "tape" and 'current_tape' in policy:
            for role in item.eligible_roles:
                if item.score(role) > 0:
                    buckets[(role, item.suit_id, item.main_stat)].append(item)
        elif item.kind == "drive" and 'current_drive' in policy:
            for role in selected_roles:
                if (
                    role in item.eligible_roles
                    and demands.get(role, {}).get(item.shape_id, 0) > 0
                    and item.score(role) > 0
                ):
                    buckets[(role, "drive", item.shape_id)].append(item)
    for bucket, members in sorted(buckets.items()):
        if diagnostics is not None:
            diagnostics["max_current_bucket_count"] = max(diagnostics.get("max_current_bucket_count", 0), len(members))
        role = bucket[0]
        ranked = _rank(members, role)
        kind = 'current_tape' if ranked[0].kind == 'tape' else 'current_drive'
        reserve = policy[kind]['reserve']
        quota = reserve if kind == 'current_tape' else ceil(demands[role][bucket[2]]) + reserve
        kept = ranked[:quota]
        eligible.update(i.uid for i in members)
        for index, item in enumerate(kept):
            _note_reason(text_catalog, reasons, reason_texts, item.uid,
                         "warehouse.recommendation.reason.current_use_reserve", _CURRENT_USE_TEXT,
                         role=role, shape=bucket[2], index=index + 1)
            # 复用既有 SSS 比例；低分桶冠军只保留，不因排名第一就推荐锁定。
            if (index == 0 and item.score(role) >= item.area * 10 * GRADE_MIN_RATIOS["SSS"]
                    and policy[kind]['action'] == 'lock'):
                locks.add(item.uid)
                current_locks.add(item.uid)
        for item in members:
            replacements[item.uid].update(i.uid for i in kept if i.uid != item.uid)
        coverage.append(
            {
                "bucket": list(bucket),
                "before_best": ranked[0].score(role),
                "quota": min(quota, len(members)),
                "required_uids": [i.uid for i in kept],
            }
        )

    future_buckets = []
    if 'future' in policy:
        for profile in future_profiles():
            groups = defaultdict(list)
            for item in items:
                if future_eligible(item, profile):
                    # 相同总分的不同有效词条组合不是重复储备。先分签名，避免双暴挤掉攻击／生命取向。
                    useful = tuple(sorted(k for k, v in item.stats if v > 0 and k in profile["weights"]))
                    group = (
                        (item.kind, item.suit_id, item.main_stat) if item.kind == "tape" else (item.kind, item.shape_id)
                    ) + (useful,)
                    groups[group].append(item)
            for group, members in sorted(groups.items()):
                if diagnostics is not None:
                    diagnostics["max_future_bucket_count"] = max(diagnostics.get("max_future_bucket_count", 0), len(members))
                    diagnostics["frontier_bucket_count"] = diagnostics.get("frontier_bucket_count", 0) + 1
                # 未来假设只增加保护；没有当前用途证据的装备仍待判断，不被升级为可弃置。
                keys = sorted(profile["weights"])
                front = _frontier(members, keys)
                # 一个合法蓝图可能重复使用同一形状；两个备用件不能覆盖实际需要三件的图纸。
                reserve = policy['future']['reserve']
                quota = (
                    max(reserve, (known_shape_multiplicity or {}).get(members[0].shape_id, 0))
                    if members[0].kind == "drive"
                    else reserve
                )
                # 同词条签名的前沿不足所需件数时，沿用较低分副本补数量，不把“被支配”当作可删除证明。
                ordered = _rank(front, profile["key"])
                front_ids = {i.uid for i in front}
                ordered += _rank([i for i in members if i.uid not in front_ids], profile["key"])
                kept = ordered[:quota]
                for item in kept:
                    _note_reason(text_catalog, reasons, reason_texts, item.uid,
                                 "warehouse.recommendation.reason.future_candidate", _FUTURE_USE_TEXT,
                                 profile=profile["key"].removeprefix(FUTURE_PREFIX))
                future_buckets.append(
                    {
                        "profile": profile["key"],
                        "bucket": list(group),
                        "frontier_size": len(front),
                        "quota": min(quota, len(members)),
                        "required_uids": [i.uid for i in kept],
                    }
                )

    target = policy.get('future', {}).get('target_inventory_size', 0) if target_inventory_size is None else target_inventory_size
    # 已按启用规则确定的锁定首选同样是硬保护，数量压力不能撤销其动态规则命中。
    quantity_protected = budget_protected_uids | frozenset(locks)
    diversity = trim_similar_reserves(items, reasons, coverage, eligible,
        target if 'redundant' in policy else 0, protected_uids=quantity_protected, incomplete=incomplete)
    trimmed = set(diversity['trimmed_uids'])
    locks.difference_update(trimmed)
    current_locks.difference_update(trimmed)
    for uid, candidates in diversity['replacement_uids'].items():
        replacements[uid].update(candidates)
    future_candidates = {uid for bucket in future_buckets for uid in bucket['required_uids']}
    reserve_budget = apply_reserve_budget(items, reasons, future_buckets, eligible,
        target, incomplete=incomplete,
        protected_uids=quantity_protected)
    rows = []
    for item in items:
        canonical_reasons = list(dict.fromkeys(reasons[item.uid]))
        item_reasons = [reason_texts[item.uid].get(reason, reason) for reason in canonical_reasons]
        dynamic_matches = {}
        if item.uid in baseline_uids and 'baseline' in policy:
            dynamic_matches['baseline'] = policy['baseline']['action']
        current_kind = 'current_tape' if item.kind == 'tape' else 'current_drive'
        if any(r.startswith(_CURRENT_USE_PREFIX) for r in canonical_reasons):
            dynamic_matches[current_kind] = 'lock' if item.uid in current_locks else 'keep'
        if any(r.startswith(_GRADE_PREFIX) for r in canonical_reasons):
            dynamic_matches['grade'] = 'keep'
        if any(r.startswith(_FUTURE_USE_PREFIX) for r in canonical_reasons):
            dynamic_matches['future'] = 'keep'
        if item.uid in eligible and not incomplete and 'redundant' in policy:
            dynamic_matches['redundant'] = 'discard'
        if item.uid in locks:
            decision = "lock"
        elif item_reasons:
            decision = "keep"
        elif incomplete or item.uid not in eligible or 'redundant' not in policy:
            decision = "review"
            item_reasons.append(localized_text(
                text_catalog, "warehouse.recommendation.reason.review_incomplete", _REVIEW_INCOMPLETE_TEXT)
                if incomplete else localized_text(
                    text_catalog, "warehouse.recommendation.reason.review_unknown", _REVIEW_UNKNOWN_TEXT))
        else:
            decision = "discard"
            item_reasons.append(localized_text(
                text_catalog, "warehouse.recommendation.reason.discard_covered", _DISCARD_COVERED_TEXT))
        # 原锁状态是外部事实，不假装算法认可；另列可执行建议避免自动解锁。
        safe_decision = "keep" if item.locked and decision == "discard" else decision
        if item.locked:
            item_reasons.append(localized_text(
                text_catalog, "warehouse.recommendation.reason.locked_kept", _LOCKED_TEXT))
        rows.append(
            {
                "uid": item.uid,
                "decision": safe_decision,
                "model_decision": decision,
                "dynamic_rule_matches": dynamic_matches,
                "reasons": item_reasons,
                "future_guards": list(guards.get(item.uid, ())),
                "replacement_uids": sorted(replacements[item.uid]),
                "similar_reserve_trimmed": item.uid in trimmed,
                "reserve_budget_excluded": item.uid in future_candidates and not any(
                    reason.startswith(_FUTURE_USE_PREFIX) for reason in reasons[item.uid]),
            }
        )
    discarded = {r["uid"] for r in rows if r["decision"] == "discard"}
    violations = []
    if discarded & baseline_uids:
        violations.append("baseline_removed")
    for row in coverage:
        if discarded.intersection(row["required_uids"]):
            violations.append("current_use_coverage_lost")
    for row in future_buckets:
        if discarded.intersection(row["required_uids"]):
            violations.append("future_bucket_coverage_lost")
    if any(r["future_guards"] and r["decision"] == "discard" for r in rows):
        violations.append("explicit_future_guard_lost")
    return {
        "algorithm": "warehouse-recommendation-all-roles-v4",
        "rows": rows,
        "drive_keep_grade": drive_keep_grade,
        "current_buckets": coverage,
        "future_buckets": future_buckets,
        "reserve_budget": reserve_budget,
        "diversity_budget": diversity,
        "violations": violations,
        "counts": dict(Counter(r["decision"] for r in rows)),
        "executable": False,
    }
