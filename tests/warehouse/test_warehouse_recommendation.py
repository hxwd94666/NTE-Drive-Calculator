# 覆盖跨套装、同类主副词条储备、替代数量及未知数据边界。
from dataclasses import replace

import pytest

from src.domain.equipment.warehouse.warehouse_recommendation import WarehouseFact, recommend
from src.domain.equipment.warehouse.intelligent_rules import generated_rules, policy_from_rules
from src.domain.inventory.post_actions import preserve_rule_matches_item
from src.models.equipment import Tape


def item(uid="a", main="生命值%", suit="Suit6", stats=None, score=120):
    return WarehouseFact(
        uid,
        "tape",
        suit,
        "TAPE_15",
        main,
        15,
        "Gold",
        tuple((stats or {"暴击率%": 8, "暴击伤害%": 16, "伤害增加%": 8, "生命值%": 10}).items()),
        (("残虹", score),),
        ("残虹",),
    )


def combination_rules():
    return generated_rules({'grade': 'SSS'}, strong_set_name='失落光芒', all_sets=['失落光芒', '普通套装'])


def combination_protection(value):
    if value.kind != 'tape':
        return []
    tape = Tape(uid=value.uid, area=value.area, quality=value.quality,
                set_name='失落光芒' if value.suit_id == 'Suit6' else '普通套装',
                main_stats=value.main_stat, sub_stats=dict(value.stats))
    return [(r['action'], r['name']) for r in combination_rules() if preserve_rule_matches_item(tape, r)]


def run(items, *, without_future=False, grade='SSS', **kw):
    policy = policy_from_rules(combination_rules())
    if grade is None:
        policy.pop('grade')
    else:
        policy['grade']['grade'] = grade
    if without_future:
        policy.pop('future')
    args = dict(selected_roles=("残虹",), demands={}, baseline_uids=frozenset(), policy=policy,
                rule_protection={} if without_future else {i.uid: combination_protection(i) for i in items})
    args.update(kw)
    return recommend(items, **args)


@pytest.mark.parametrize("main", ["生命值%", "防御力%", "治疗加成", "咒属性异能伤害增强%", "攻击力%"])
def test_strong_set_all_valid_mains_keep_dual_crit(main):
    value = replace(item(main=main), eligible_roles=())
    assert combination_protection(value)
    assert run([value])["rows"][0]["decision"] == "keep"


@pytest.mark.parametrize("main,anchor", [("生命值%", "生命值%"), ("防御力%", "防御力%")])
def test_scaling_main_sub_crit_and_mag_are_protected(main, anchor):
    value = item(main=main, suit="Other", stats={anchor: 10, "暴击率%": 6, "暴击伤害%": 12, "环合强度": 40})
    assert combination_protection(value)


def test_no_invented_heal_substat_and_no_dual_crit_from_main():
    value = item(main="暴击率%", stats={"暴击伤害%": 12, "生命值%": 10, "环合强度": 40, "防御力%": 10})
    assert not combination_protection(value)


def test_low_bucket_winner_is_kept_not_locked():
    value = item(suit="Other", stats={"攻击力": 1}, score=5)
    assert run([value])["rows"][0]["decision"] == "keep"


def test_redundant_items_do_not_remove_required_alternatives():
    values = [item(str(n), suit="Other", stats={"攻击力": 1}, score=100 - n) for n in range(5)]
    result = run(values, without_future=True)
    assert [r["decision"] for r in result["rows"]] == ["keep", "keep", "discard", "discard", "discard"]
    assert result["violations"] == []


def test_future_guards_not_truncated_by_reserve_quota():
    values = [item(str(n), score=120 - n) for n in range(8)]
    assert all(r["decision"] != "discard" for r in run(values)["rows"])


def test_nonselected_role_still_protects_tape():
    value = replace(item(main="咒属性异能伤害增强%"), eligible_roles=("残虹",))
    assert run([value], selected_roles=("其他角色",))["rows"][0]["decision"] == "lock"


def test_baseline_and_existing_locks_never_auto_discard():
    values = [item(str(n), suit="Other", stats={"攻击力": 1}, score=100 - n) for n in range(4)]
    values[-1] = replace(values[-1], locked=True)
    result = run(values, without_future=True, baseline_uids=frozenset({"2"}))
    assert result["rows"][2]["decision"] == "lock"
    assert result["rows"][3]["decision"] == "keep"
    assert result["rows"][3]["model_decision"] == "discard"


def test_missing_coverage_does_not_become_discard():
    value = replace(item(suit="Other", stats={"攻击力": 1}), eligible_roles=())
    assert run([value])["rows"][0]["decision"] == "review"
    values = [item(str(n), suit="Other", stats={"攻击力": 1}, score=100 - n) for n in range(4)]
    assert run(values, incomplete=True)["rows"][-1]["decision"] == "review"


def test_repeated_uid_rejected():
    with pytest.raises(ValueError):
        run([item(), item()])


def test_future_reserve_never_turns_review_into_discard():
    values = [
        replace(
            item(str(n), suit="Other", main="攻击力%", stats={"攻击力%": 12.5, "暴击伤害%": 20, "攻击力": 80}),
            eligible_roles=(),
        )
        for n in range(5)
    ]
    without = {r["uid"]: r["decision"] for r in run(values, without_future=True)["rows"]}
    with_future = run(values)["rows"]
    assert all(r["decision"] != "discard" or without[r["uid"]] == "discard" for r in with_future)


def test_distinct_useful_stat_signatures_have_separate_reserves():
    values = [
        replace(
            item(str(n), suit="Other", main="暴击率%", stats=stats),
            eligible_roles=(),
            scores=(("future:attack", 100 - n),),
        )
        for n, stats in enumerate(
            [
                {"攻击力%": 12.5, "攻击力": 80, "暴击伤害%": 20},
                {"攻击力%": 12.5, "攻击力": 80, "伤害增加%": 10},
                {"攻击力%": 12.5, "攻击力": 80, "环合强度": 60},
            ]
        )
    ]
    assert all(r["decision"] == "keep" for r in run(values)["rows"])


def test_dominated_copies_still_fill_blueprint_shape_multiplicity():
    values = [
        replace(
            item(str(n), stats={"生命值%": 5 - n * 0.5, "暴击率%": 4, "暴击伤害%": 8}),
            kind="drive",
            area=4,
            shape_id="H_4",
            main_stat="",
            eligible_roles=(),
            scores=(("future:health", 30 - n),),
        )
        for n in range(5)
    ]
    result = run(values, known_shape_multiplicity={"H_4": 4})
    assert sum(r["decision"] == "keep" for r in result["rows"]) == 4
    assert result["rows"][-1]["decision"] == "review"


def test_health_main_health_crit_attack_is_explicit_reserve():
    value = item(suit="Other", stats={"生命值%": 12.5, "暴击率%": 10, "暴击伤害%": 20, "攻击力%": 12.5})
    assert combination_protection(value)


def test_user_grade_option_protects_drives_and_can_be_disabled():
    values = [
        replace(
            item(str(n), stats={"倾陷强度": 20}),
            kind="drive",
            shape_id="V_4",
            main_stat="",
            area=4,
            scores=(("残虹", 32 - n),),
        )
        for n in range(4)
    ]
    result = run(values, without_future=True, demands={"残虹": {"V_4": 1}})
    assert result["rows"][-1]["decision"] == "keep"
    disabled = run(values, without_future=True, demands={"残虹": {"V_4": 1}}, grade=None)
    assert disabled["rows"][-1]["decision"] == "discard"
    assert all(r["decision"] in ("keep", "lock") for r in run(values, grade="S")["rows"])


def test_unknown_grade_rejected_instead_of_defaulting():
    with pytest.raises(ValueError):
        run([item()], grade="CUSTOM")
