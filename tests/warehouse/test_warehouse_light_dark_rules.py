# 对照光暗与普通套装的智能弃置、前置保留及光套显式组合保护，不访问账号或游戏。
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.domain.equipment.eligibility.management_settings import normalize_warehouse_config
from src.domain.equipment.warehouse.intelligent_rules import (
    automatic_main_supported, generated_rules, policy_from_rules,
)
from src.domain.equipment.warehouse.warehouse_decisions import (
    DecisionStatus, decision_row, manual_decision, scoring_decision,
)
from src.domain.equipment.warehouse.warehouse_recommendation import (
    SUIT_ELEMENT_MAINS, WarehouseFact, recommend,
)
from src.domain.equipment.warehouse.warehouse_rule_order import apply_ordered_rules
from src.domain.inventory.post_actions import preserve_rule_matches_item
from src.models.equipment import Tape


SUITS = {"Suit1": "暗套测试", "Suit6": "光套测试", "Suit2": "普通元素套测试"}


def batch(suit, stats=None, main=None):
    facts, tapes = [], []
    for index in range(6):
        fact = WarehouseFact(
            f"{suit}-{index}", "tape", suit, "TAPE_15",
            main or SUIT_ELEMENT_MAINS[suit], 15, "Gold",
            tuple((stats or {"攻击力": 1}).items()), (("测试角色", 70 - index),), ("测试角色",),
        )
        facts.append(fact)
        tapes.append(Tape(uid=fact.uid, suit_id=suit, set_name=SUITS[suit], quality="Gold", area=15,
                          main_stats=fact.main_stat, sub_stats=dict(fact.stats), role_scores=dict(fact.scores)))
    return facts, tapes


def calculate(facts, tapes, rules, fixed=None, *, incomplete=False):
    matches = {t.uid: [r for r in rules if preserve_rule_matches_item(t, r)
                       and automatic_main_supported(r, f)] for t, f in zip(tapes, facts)}
    result = recommend(
        facts, selected_roles=["测试角色"], demands={}, baseline_uids=frozenset(),
        policy=policy_from_rules(rules), incomplete=incomplete,
        rule_protection={uid: [(r["action"], r["name"]) for r in rows[:1]] for uid, rows in matches.items()},
    )
    for row in result["rows"]:
        if fixed and row["uid"] in fixed:
            row.update(decision_row(fixed[row["uid"]], {"locked": False, "discarded": False, "equipped": False}))
    apply_ordered_rules(result, tapes, rules, matched_by_uid=matches, incomplete=incomplete,
                        protected_uids=frozenset(f.uid for f in facts if f.locked or f.equipped))
    return result["rows"]


def rules():
    return generated_rules({"grade": "SSS"}, strong_set_name=SUITS["Suit6"], all_sets=list(SUITS.values()))


@pytest.mark.parametrize("suit", SUITS)
def test_low_quality_redundant_items_can_be_discarded_for_every_suit(suit):
    facts, tapes = batch(suit)
    rows = calculate(facts, tapes, rules())
    assert [r["decision"] for r in rows] == ["keep", "keep", "discard", "discard", "discard", "discard"]
    assert all(r["winning_rule"]["smart_kind"] == "redundant" for r in rows[2:])


@pytest.mark.parametrize("suit", ["Suit1", "Suit6"])
@pytest.mark.parametrize("stage", ["manual", "scoring"])
def test_predecessor_retention_stops_smart_discard_for_light_and_dark(suit, stage):
    facts, tapes = batch(suit)
    raw = {"locked": False, "discarded": False, "equipped": False}
    config = normalize_warehouse_config({"preserve_rules": [{
        "name": "前置保留", "enabled": True, "item_type": "tape", "action": "keep", "set_names": [SUITS[suit]],
        "main_stats": [SUIT_ELEMENT_MAINS[suit]],
    }]})
    scorer = SimpleNamespace(roles_db={"测试角色": {}}, get_grade_tag=lambda score, area: "SSS")
    context = SimpleNamespace(missing_character_ids=(), errors_for_roles=lambda _: (),
                              for_item=lambda _: [SimpleNamespace(role="测试角色")])
    fixed = {}
    for tape in tapes:
        if stage == "manual":
            decision = manual_decision(tape, raw, config)
            assert decision.target == "normal"
        else:
            config["lock"].update(enabled=True, grade="SSS", role_scope="selected")
            decision = scoring_decision(tape, raw, config, scorer, context, {"lock": ["测试角色"], "discard": []})
            assert decision.target == "locked"
        assert decision.status == DecisionStatus.DECIDED
        fixed[tape.uid] = decision
    rows = calculate(facts, tapes, rules(), fixed)
    assert not any(r["decision"] == "discard" for r in rows)
    assert all(r["predecessor_fixed"] and r["decision_stage"] == stage for r in rows)
    assert not any("winning_rule" in r for r in rows)


def test_light_combo_is_explicit_protection_not_blanket_immunity():
    facts, tapes = batch("Suit6", {"暴击率%": 1, "暴击伤害%": 1, "环合强度": 1}, "生命值%")
    # 排除当前角色和未来假设，只检验持久化的光套双暴＋有效词条保留规则。
    facts = [replace(f, eligible_roles=()) for f in facts]
    selected = [r for r in rules() if r.get("intelligent", {}).get("key") == "combo:light"
                or r.get("smart_kind") == "redundant"]
    rows = calculate(facts, tapes, selected)
    assert all(r["decision"] == "keep" for r in rows)
    assert all(r["winning_rule"]["name"] == selected[0]["name"] for r in rows)


@pytest.mark.parametrize("suit", ["Suit1", "Suit6"])
@pytest.mark.parametrize("guard", ["unknown_use", "incomplete", "locked", "equipped"])
def test_missing_evidence_and_existing_protection_do_not_turn_into_discard(suit, guard):
    facts, tapes = batch(suit)
    if guard == "unknown_use":
        facts = [replace(f, eligible_roles=()) for f in facts]
    elif guard in {"locked", "equipped"}:
        facts = [replace(f, **{guard: True}) for f in facts]
    rows = calculate(facts, tapes, rules(), incomplete=guard == "incomplete")
    assert not any(r["decision"] == "discard" for r in rows)
