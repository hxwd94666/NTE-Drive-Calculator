# 验证正式目标的显式证据、不可变预设、跨语言摘要和严格边界，不以契约结构冒充游戏机制合法性。
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import json
from pathlib import Path

import pytest

from src.domain.teams.graduation.axis_graduation_rules import StatReference, TheoryEquipmentRules, TheoryEquipmentSpec
from src.domain.teams.graduation.axis_graduation_target import (
    MAX_TARGET_BYTES,
    TargetFork,
    TargetWorldBonus,
    decode_graduation_target,
    encode_graduation_target,
)


NTE_TEST_TIER = "core"
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/team_axis_graduation_target_v1.json"


def payload():
    return json.loads(FIXTURE.read_text("utf-8"))


def target(value=None):
    return decode_graduation_target(json.dumps(payload() if value is None else value, ensure_ascii=False))


def rules():
    return TheoryEquipmentRules(
        dataset_id="fixture", static_schema=42, static_sha256="a" * 64,
        scoring_source_sha256="b" * 64, scoring_rule_sha256="c" * 64,
        quality_coefficients=(("Gold", 1.0), ("Purple", .8), ("Blue", .6)),
        drive_sub_stats=(StatReference("AtkUp", 1.25, True),),
        core_sub_stats=(StatReference("AtkUp", 12.5, True),),
        core_main_stats=(StatReference("CritBase", 30.0, True),),
        items=(TheoryEquipmentSpec("C", "core", "Gold", None, None, "S", 20, 1),),
    )


def test_target_roundtrip_has_explicit_null_fork_actual_base_skills_and_finite_presets():
    value = target()
    assert decode_graduation_target(encode_graduation_target(value)) == value
    assert len(value.presets) == 2
    assert value.presets[0].members[0].fork is None
    assert value.presets[0].members[0].skill_levels == (("GA_Test", 1),)
    value.verify_rules(rules())


def test_canonical_identity_uses_shared_golden_and_float_bits_not_json_number_spelling():
    value = target()
    assert value.sha256 == "034eb0f5bebe18cb1e59524e02054eea650d0b7bf764fea619982c9f0658f17c"
    # 两种JSON拼写、带符号零均不产生另一份目标；指数型小数也参与跨语言golden。
    assert target().sha256 == decode_graduation_target(encode_graduation_target(value).replace("1e-05", "0.00001")).sha256
    assert replace(value, world_bonus=TargetWorldBonus(-0.0, 0)).sha256 == replace(
        value, world_bonus=TargetWorldBonus(0, 0.0)).sha256


def test_semantic_sets_are_canonical_but_roster_order_and_extra_instance_count_are_preserved():
    raw = payload()
    raw["presets"].reverse()
    for preset in raw["presets"]:
        for layout in preset["layouts"]:
            layout["pieces"].reverse()
    assert target(raw).sha256 == target().sha256
    raw["presets"][0]["layouts"][0]["extra_shape_ids"] = ["Shape", "Shape"]
    changed = target(raw)
    assert changed.presets[1].layouts[0].extra_shape_ids == ("Shape", "Shape")
    assert changed.sha256 != target().sha256
    with pytest.raises(ValueError):
        replace(target(), character_ids=(2, 1, 3, 4))


def test_mutating_input_containers_or_returned_payload_does_not_mutate_frozen_target():
    raw = payload()
    value = target(raw)
    before = value.sha256
    raw["presets"][0]["members"][0]["character_level"] = 1
    projected = value.to_payload()
    projected["presets"][0]["layouts"][0]["pieces"].clear()
    assert value.sha256 == before
    with pytest.raises(FrozenInstanceError):
        value.revision = 99
    assert replace(value, revision=2).sha256 != before


@pytest.mark.parametrize("path", [
    ("world_bonus",), ("assumption_codes",), ("format",), ("presets",),
    ("presets", 0, "members", 0, "fork"),
    ("presets", 0, "members", 0, "likeability_level_10_enabled"),
    ("presets", 0, "layouts", 0, "minimum_set_count"),
])
def test_missing_fields_are_not_silently_defaulted(path):
    raw = payload()
    owner = raw
    for key in path[:-1]:
        owner = owner[key]
    del owner[path[-1]]
    with pytest.raises(ValueError):
        target(raw)


@pytest.mark.parametrize("path,key", [
    ((), "inventory"), (("world_bonus",), "guessed_bonus"),
    (("presets", 0), "current_inventory_max"),
    (("presets", 0, "members", 0), "effective_skill_levels"),
    (("presets", 0, "layouts", 0), "uid"),
    (("presets", 0, "layouts", 0, "pieces", 0), "uid_serial"),
])
def test_unknown_or_inventory_fields_do_not_survive_the_target_codec(path, key):
    raw = payload()
    owner = raw
    for part in path:
        owner = owner[part]
    owner[key] = "fixture"
    with pytest.raises(ValueError):
        target(raw)


@pytest.mark.parametrize("key,value", [
    ("revision", True), ("revision", 0), ("revision", 1.0),
    ("static_schema", 2 ** 32), ("static_schema", False),
    ("static_sha256", "A" * 64), ("format", "old_average"),
    ("equipment_policy", "unlimited_repeat_stats"), ("target_id", " x"),
    ("team_id", "bad\x85name"), ("dataset_id", "字" * 86),
    ("character_ids", [1, 2, 3, 3]),
])
def test_invalid_identity_versions_and_typed_integer_bounds_are_rejected(key, value):
    raw = payload()
    raw[key] = value
    with pytest.raises(ValueError):
        target(raw)


@pytest.mark.parametrize("value", [None, True, -1, 21, float("nan"), float("inf"), 10 ** 1000])
def test_world_bonus_requires_explicit_finite_values(value):
    with pytest.raises(ValueError):
        TargetWorldBonus(value, 0)


def test_presets_must_preserve_four_members_and_distinct_per_preset_piece_identity():
    for mutate in (
        lambda p: p["presets"].append(deepcopy(p["presets"][0])),
        lambda p: p["presets"][0]["members"].pop(),
        lambda p: p["presets"][0]["layouts"].reverse(),
        lambda p: p["presets"][0]["layouts"][1].update(core_piece_id="core-1"),
        lambda p: p["presets"][0]["members"][0]["skill_levels"].append({"skill_id": "GA_Test", "level": 2}),
    ):
        raw = payload()
        mutate(raw)
        with pytest.raises(ValueError):
            target(raw)


def test_explicit_fork_has_no_growth_defaults_and_static_legality_is_separate():
    value = target()
    member = replace(value.presets[0].members[0], fork=TargetFork("F", 80, 6, 5))
    assert member.to_payload()["fork"] == {"fork_id": "F", "level": 80, "breakthrough_stage": 6, "refinement_level": 5}
    raw = payload()
    raw["presets"][0]["members"][0]["fork"] = {"fork_id": "F", "level": 80, "breakthrough_stage": 6}
    with pytest.raises(ValueError):
        target(raw)
    # 纯结构契约允许正等级；是否存在此弧盘和升级路径仍交静态校验，不能称为合法毕业构筑。
    assert TargetFork("FixtureUnknown", 999, 6, 5).level == 999


def test_rule_identity_changes_invalidate_target_without_using_latest_rules_as_fallback():
    value = target()
    for change in ({"dataset_id": "other"}, {"static_sha256": "f" * 64}, {"scoring_rule_sha256": "e" * 64}):
        with pytest.raises(ValueError):
            value.verify_rules(replace(rules(), **change))


def test_duplicate_json_fields_nonfinite_and_oversized_body_never_become_a_target():
    raw = FIXTURE.read_text("utf-8")
    for invalid in (raw.replace('"revision": 1', '"revision": 1, "revision": 2'),
                    raw.replace('"fork": null', '"fork": null, "fork": null', 1),
                    raw + " " * MAX_TARGET_BYTES, "{" * 2000):
        with pytest.raises(ValueError):
            decode_graduation_target(invalid)


def test_plain_constructor_copies_mutable_skill_and_awakening_containers():
    member = target().presets[1].members[0]
    chosen = ["Effect1"]
    skills = [["GA_Test", 1]]
    copied = replace(member, selected_awaken_effect_ids=chosen, skill_levels=skills)
    chosen.clear()
    skills[0][1] = 99
    assert copied.selected_awaken_effect_ids == ("Effect1",)
    assert copied.skill_levels == (("GA_Test", 1),)


def test_plain_constructor_cannot_bypass_total_target_body_limit():
    value = target()
    preset = value.presets[0]
    skills = tuple((f"skill-{index}-" + "x" * 240, 1) for index in range(64))
    members = tuple(replace(member, skill_levels=skills) for member in preset.members)
    presets = tuple(replace(preset, preset_id=f"preset-{index}", members=members) for index in range(16))
    with pytest.raises(ValueError, match="超限"):
        replace(value, presets=presets)
