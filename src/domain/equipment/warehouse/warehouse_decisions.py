# 对冻结装备执行仓库手动与评分前序阶段；决定与状态差异分开，后序不得覆盖。
from dataclasses import dataclass
from enum import StrEnum
from copy import deepcopy

from src.domain.inventory.post_actions import preserve_rule_matches_item, evaluate_target_state_for_item


class DecisionStatus(StrEnum):
    UNDECIDED = "undecided"
    DECIDED = "decided"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class WarehouseDecision:
    uid: str
    status: DecisionStatus
    stage: str
    target: str | None = None
    reason: str = ""
    matches: tuple[dict, ...] = ()


def current_state(raw):
    return "locked" if raw.get("locked") else "discarded" if raw.get("discarded") else "normal"


def discard_is_protected(raw, *, locked_plan=False):
    return locked_plan or raw.get("equipped") is True


def manual_decision(item, raw, config, *, user_target=None, protected=False):
    known = all(raw.get(key) is not None for key in ("locked", "discarded", "equipped"))
    if not known:
        return WarehouseDecision(item.uid, DecisionStatus.UNKNOWN, "source", reason="state_unknown")
    if user_target is not None:
        if user_target not in {"normal", "locked", "discarded"}:
            raise ValueError("无效仓库手工状态")
        if user_target == "discarded" and discard_is_protected(raw, locked_plan=protected):
            return WarehouseDecision(item.uid, DecisionStatus.BLOCKED, "user", reason="state_protected")
        return WarehouseDecision(item.uid, DecisionStatus.DECIDED, "user", user_target)
    matches = tuple(
        {
            "rule_id": rule.get("rule_id", ""),
            "name": str(rule.get("name") or ""),
            "action": rule.get("action", "keep"),
            "conditions": {
                key: deepcopy(rule.get(key))
                for key in (
                    "item_type",
                    "quality_scope",
                    "main_stats",
                    "sub_stats",
                    "required_sub_stats",
                    "positive_values_only",
                    "sub_match",
                    "shape_ids",
                    "set_names",
                )
            },
        }
        for rule in config.get("preserve_rules", ())
        if not rule.get("intelligent") and not rule.get("smart_kind") and preserve_rule_matches_item(item, rule)
    )
    actions = {row["action"] for row in matches}
    if len(actions) > 1:
        return WarehouseDecision(
            item.uid, DecisionStatus.CONFLICT, "manual", reason="manual_actions_conflict", matches=matches
        )
    if not matches:
        return WarehouseDecision(item.uid, DecisionStatus.UNDECIDED, "manual")
    action = next(iter(actions))
    state = current_state(raw)
    target = {"lock": "locked", "discard": "discarded", "keep": "normal" if state == "discarded" else state}[action]
    if target == "discarded" and (
        discard_is_protected(raw, locked_plan=protected)
        or state == "locked"
        and config["discard"].get("on_locked") == "skip"
        or state == "discarded"
        and config["discard"].get("on_discarded") == "skip"
    ):
        return WarehouseDecision(item.uid, DecisionStatus.BLOCKED, "manual", reason="state_protected", matches=matches)
    return WarehouseDecision(item.uid, DecisionStatus.DECIDED, "manual", target, matches=matches)


def scoring_decision(item, raw, config, scorer, context, roles, *, incomplete_scopes=frozenset(), protected=False):
    # 只复用公共逐模块评分判断；前序规则独立仲裁，完整未匹配采用两管理的固定策略。
    scoring_config = deepcopy(config)
    scoring_config["preserve_rules"] = []
    target, detail = evaluate_target_state_for_item(
        item, current_state(raw), scoring_config, scorer, roles, context, incomplete_scopes=incomplete_scopes
    )
    explicit = any(
        part.get("result") in {"normal", "locked", "discarded"}
        for name, part in detail.items()
        if name in {"lock", "discard"}
    )
    if explicit:
        if target == "discarded" and discard_is_protected(raw, locked_plan=protected):
            return WarehouseDecision(item.uid, DecisionStatus.BLOCKED, "scoring", reason="state_protected")
        return WarehouseDecision(item.uid, DecisionStatus.DECIDED, "scoring", target)
    incomplete = any(
        part.get("reason") in {"evaluation_incomplete", "no_scope_roles"}
        for name, part in detail.items()
        if name in {"lock", "discard"}
    )
    return WarehouseDecision(
        item.uid,
        DecisionStatus.UNKNOWN if incomplete else DecisionStatus.UNDECIDED,
        "scoring",
        reason="evaluation_incomplete" if incomplete else "",
    )


def predecessor_context(raw_by_uid, decisions=None):
    """分开后序决策集合与固定计算上下文；未知标志不被补造成正常状态。"""
    if decisions is not None and (
        set(decisions) != set(raw_by_uid) or any(uid != decision.uid for uid, decision in decisions.items())
    ):
        raise ValueError("前序决定与智能库存身份不一致")
    effective = deepcopy(raw_by_uid)
    fixed = {
        uid: decision for uid, decision in (decisions or {}).items() if decision.status != DecisionStatus.UNDECIDED
    }
    excluded = set()
    for uid, decision in fixed.items():
        if decision.target is not None:
            effective[uid].update(locked=decision.target == "locked", discarded=decision.target == "discarded")
        if effective[uid].get("discarded"):
            excluded.add(uid)
    return effective, fixed, frozenset(excluded), frozenset(set(fixed) - excluded)


def decision_row(decision, raw):
    target = decision.target
    return {
        "uid": decision.uid,
        "decision": {"normal": "keep", "locked": "lock", "discarded": "discard"}.get(target, "review"),
        "decision_status": decision.status.value,
        "decision_stage": decision.stage,
        "predecessor_fixed": decision.status != DecisionStatus.UNDECIDED,
        "fixed_state_target": target,
        "reasons": [decision.reason] if decision.reason else [],
        "conflicts": list(decision.matches) if decision.status == DecisionStatus.CONFLICT else [],
        "original_locked": raw.get("locked"),
        "original_discarded": raw.get("discarded"),
        "original_equipped": raw.get("equipped"),
        "state_known": all(raw.get(key) is not None for key in ("locked", "discarded", "equipped")),
    }
