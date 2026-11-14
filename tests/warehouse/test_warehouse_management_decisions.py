# 验证仓库阶段决定、同阶段冲突、锁定优先及恢复正常，不改变扫描旧仲裁。
from copy import deepcopy
from itertools import permutations
from types import SimpleNamespace

import pytest

from src.domain.equipment.eligibility.management_settings import normalize_warehouse_config
from src.domain.equipment.warehouse.warehouse_decisions import (
    DecisionStatus,
    WarehouseDecision,
    manual_decision,
    scoring_decision,
    decision_row,
    predecessor_context,
)
from src.domain.equipment.warehouse.warehouse_targets import recommendation_targets
from src.models.equipment import Tape


def item():
    return Tape(
        uid="test",
        quality="Gold",
        area=15,
        set_name="套装",
        main_stats="生命值%",
        sub_stats={},
        role_scores={"A": 0, "B": 100},
    )


def raw(**kwargs):
    return {"locked": False, "discarded": False, "equipped": False, **kwargs}


def rule(action, identity):
    return {
        "enabled": True,
        "rule_id": identity,
        "name": identity,
        "item_type": "tape",
        "action": action,
        "main_stats": ["生命值%"],
    }


def scoring():
    scorer = SimpleNamespace(
        roles_db={"A": {}, "B": {}}, get_grade_tag=lambda score, area: "SSS" if score >= 80 else "D"
    )
    context = SimpleNamespace(
        missing_character_ids=(),
        errors_for_roles=lambda _: (),
        for_item=lambda _: [SimpleNamespace(role="A"), SimpleNamespace(role="B")],
    )
    return scorer, context


def test_manual_conflicts_are_order_independent_and_do_not_become_scoring_candidates():
    rules = [rule("keep", "keep"), rule("discard", "discard"), rule("lock", "lock")]
    for ordered in permutations(rules):
        config = normalize_warehouse_config({"preserve_rules": list(ordered)})
        result = manual_decision(item(), raw(), config)
        assert result.status == DecisionStatus.CONFLICT and result.target is None
        assert {row["rule_id"] for row in result.matches} == {"keep", "discard", "lock"}
        assert recommendation_targets([decision_row(result, raw())]) == {}


@pytest.mark.parametrize("action,target", [("keep", "normal"), ("lock", "locked"), ("discard", "discarded")])
def test_manual_decisions_are_terminal_even_when_the_state_does_not_change(action, target):
    config = normalize_warehouse_config({"preserve_rules": [rule(action, "1"), rule(action, "2")]})
    result = manual_decision(item(), raw(), config)
    assert result.status == DecisionStatus.DECIDED and result.target == target
    targets = recommendation_targets([decision_row(result, raw())])
    assert targets == ({} if target == "normal" else {"test": target})


def test_scoring_keeps_locking_precedence_across_distinct_role_scopes():
    config = normalize_warehouse_config()
    config["lock"].update(enabled=True, role_scope="selected", selected_character_ids=[2], grade="SSS")
    config["discard"].update(enabled=True, role_scope="selected", selected_character_ids=[1], grade="B")
    scorer, context = scoring()
    result = scoring_decision(item(), raw(), config, scorer, context, {"lock": ["B"], "discard": ["A"]})
    assert result.status == DecisionStatus.DECIDED and result.target == "locked"


def test_restore_normal_is_not_the_same_as_undecided():
    config = normalize_warehouse_config()
    config["discard"].update(enabled=True, role_scope="selected", grade="B")
    scorer, context = scoring()
    roles = {"lock": [], "discard": ["B"]}
    restored = scoring_decision(item(), raw(discarded=True), config, scorer, context, roles)
    untouched = scoring_decision(item(), raw(), config, scorer, context, roles)
    assert restored.status == DecisionStatus.DECIDED and restored.target == "normal"
    assert recommendation_targets([decision_row(restored, raw(discarded=True))]) == {"test": "normal"}
    assert untouched.status == DecisionStatus.UNDECIDED and untouched.target is None


def test_explicit_unlock_can_be_previewed_but_unknown_state_never_has_a_target():
    config = normalize_warehouse_config()
    config["lock"].update(enabled=True, grade="SSS", on_locked="normal", role_scope="selected")
    scorer, context = scoring()
    result = scoring_decision(item(), raw(locked=True), config, scorer, context, {"lock": ["A"], "discard": []})
    assert result.status == DecisionStatus.DECIDED and result.target == "normal"
    assert recommendation_targets([decision_row(result, raw(locked=True))]) == {"test": "normal"}
    unknown = manual_decision(item(), raw(locked=None), config)
    assert unknown.status == DecisionStatus.UNKNOWN
    assert recommendation_targets([decision_row(unknown, raw(locked=None))]) == {}


def test_rule_identity_survives_edits_and_reorder_and_input_is_not_mutated():
    original = {"preserve_rules": [rule("keep", "stable"), rule("lock", "other")]}
    before = deepcopy(original)
    normalized = normalize_warehouse_config(original)
    normalized["preserve_rules"].reverse()
    normalized["preserve_rules"][1]["name"] = "改名"
    again = normalize_warehouse_config(normalized)
    assert [row["rule_id"] for row in again["preserve_rules"]] == ["other", "stable"]
    assert original == before
    with pytest.raises(ValueError, match="身份重复"):
        normalize_warehouse_config({"preserve_rules": [rule("keep", "same"), rule("lock", "same")]})


def test_smart_context_excludes_predecessor_discards_preserves_unknown_and_freezes_retained_items():
    before = {
        "discard": raw(),
        "restore": raw(discarded=True),
        "unknown": raw(locked=None),
        "conflict": raw(discarded=True),
        "open": raw(),
    }
    decisions = {
        "discard": WarehouseDecision("discard", DecisionStatus.DECIDED, "manual", "discarded"),
        "restore": WarehouseDecision("restore", DecisionStatus.DECIDED, "scoring", "normal"),
        "unknown": WarehouseDecision("unknown", DecisionStatus.UNKNOWN, "source"),
        "conflict": WarehouseDecision("conflict", DecisionStatus.CONFLICT, "manual"),
        "open": WarehouseDecision("open", DecisionStatus.UNDECIDED, "scoring"),
    }
    frozen = deepcopy(before)
    effective, fixed, excluded, retained = predecessor_context(before, decisions)
    assert excluded == {"discard", "conflict"} and retained == {"restore", "unknown"}
    assert set(fixed) == set(before) - {"open"}
    assert effective["restore"]["discarded"] is False and effective["unknown"]["locked"] is None
    assert before == frozen
    with pytest.raises(ValueError, match="身份不一致"):
        predecessor_context(before, {uid: value for uid, value in decisions.items() if uid != "open"})
