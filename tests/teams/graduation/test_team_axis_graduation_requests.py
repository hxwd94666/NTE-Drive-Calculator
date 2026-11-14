# 验证实际冻结来源到正式请求的窄适配，缺项不借模板补齐，零觉醒确认和账号/静态边界保持。
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from src.domain.teams.graduation.axis_graduation_inputs import CurrentBuild, CurrentMemberBuild
from src.domain.teams.models import Team
from src.domain.teams.overview import SourceMode, TeamSourceConfig
from src.integrations.analysis_core.nte_analysis_core import NativeAnalysisError
from src.integrations.battle_analysis.graduation.team_axis_graduation import _rules
from src.domain.teams.graduation.axis_graduation_target import decode_graduation_target
from src.services.teams.axis_graduation.requests import actual_component_input, build_benchmark_request
from tests.teams.graduation.test_team_axis_graduation_wire import wire_fixture


NTE_TEST_TIER = "core"


def prepared():
    r = wire_fixture()
    a = r["current"]
    for actor in a["build"]["characters"]:
        profile = actor["profile"]
        profile["field_sources"] = {key: "account" for key in profile}
        profile["skill_level_sources"] = {key: "account" for key in profile["skill_levels"]}
    members = tuple(CurrentMemberBuild(c["character_id"], None, None, 1, "a"*64,
        json.dumps({"account": c["profile"], "native": {}, "effective": c["profile"]}), json.dumps(c["equipment"]))
        for c in a["build"]["characters"])
    team = Team(a["team_id"], "合成队伍", tuple(a["character_ids"]), 1)
    frozen = CurrentBuild(a["account_id"], a["generation"], team, TeamSourceConfig(team.team_id, SourceMode.GAME, 1),
        a["dataset_id"], a["static_schema"], a["static_sha256"], members, json.dumps(a["world_bonus"]))
    target = decode_graduation_target(json.dumps(r["target"]))
    return frozen, {"frozen": frozen, "target": target, "rules": _rules(r["rules"]), "battle_selector": r["battle_request"],
        "source_input_digest": r["source_input_digest"], "seeds": r["seeds"], "request_id": r["request_id"],
        "max_evaluations": 2, "max_millis": 60000, "component_provenance": r["component_provenance"]}


def test_actual_request_keeps_level_zero_and_stat_values_and_frozen_source_digest():
    frozen, args = prepared()
    result = build_benchmark_request(**args)
    actual = result["current"]
    assert actual["source_sha256"] == frozen.sha256
    for source, actor in zip(frozen.members, actual["build"]["characters"]):
        assert actor["equipment"] == source.equipment
        assert actor["equipment_complete"] is True and actor["equipment"][0]["level"] == 0
        assert actor["profile"]["skill_levels"] == source.profile_evidence["effective"]["skill_levels"]
    args["battle_selector"]["generation"] = 999
    args["seeds"][0]["seeds"][0][0]["pieces"][0]["item_id"] = "changed"
    assert result["battle_request"]["generation"] == frozen.generation
    assert result["seeds"][0]["seeds"][0][0]["pieces"][0]["item_id"] == "C"


@pytest.mark.parametrize("change", [lambda f: replace(f, gaps=("world_bonus_unconfirmed",)),
    lambda f: replace(f, source_issues=("source_changed",)),
    lambda f: replace(f, members=(replace(f.members[0], missing_fields=("character_level",)), *f.members[1:])),
    lambda f: replace(f, members=(replace(f.members[0], gaps=("growth_model_missing",)), *f.members[1:]))])
def test_incomplete_actual_input_does_not_get_template_or_latest_inventory_defaults(change):
    frozen, _ = prepared()
    with pytest.raises(ValueError):
        actual_component_input(change(frozen))


def test_known_zero_awakening_implies_empty_choice_without_materializing_new_effects():
    frozen, _ = prepared()
    member = frozen.members[0]
    evidence = member.profile_evidence
    evidence["effective"]["awakening_selection_initialized"] = False
    updated = replace(member, profile_evidence_json=json.dumps(evidence))
    actual = actual_component_input(replace(frozen, members=(updated, *frozen.members[1:])))
    profile = actual["build"]["characters"][0]["profile"]
    assert profile["awakening_level"] == 0 and profile["awakening_selection_initialized"] is True
    assert profile["selected_awaken_effect_ids"] == []


@pytest.mark.parametrize("key,value", [("account_id", "other-synthetic"), ("generation", 2)])
def test_selector_account_or_generation_mismatch_stops_request_creation(key, value):
    _, args = prepared()
    args["battle_selector"][key] = value
    with pytest.raises(NativeAnalysisError):
        build_benchmark_request(**args)


def test_target_team_or_static_mismatch_does_not_rebind_the_frozen_actual_source():
    frozen, args = prepared()
    args["frozen"] = replace(frozen, static_sha256="0"*64)
    with pytest.raises(NativeAnalysisError):
        build_benchmark_request(**args)
    args["frozen"] = replace(frozen, team=replace(frozen.team, team_id="other-team"),
                              source=replace(frozen.source, team_id="other-team"))
    with pytest.raises(NativeAnalysisError):
        build_benchmark_request(**args)


def test_unknown_profile_field_is_not_filled_from_target_even_if_source_flags_were_cleared():
    frozen, _ = prepared()
    evidence = frozen.members[0].profile_evidence
    evidence["effective"].pop("character_level")
    member = replace(frozen.members[0], profile_evidence_json=json.dumps(evidence))
    with pytest.raises(ValueError):
        actual_component_input(replace(frozen, members=(member, *frozen.members[1:])))


def test_theory_and_source_objects_are_not_modified_by_request_creation():
    frozen, args = prepared()
    source = deepcopy(args["seeds"])
    target = args["target"]
    digest = frozen.sha256
    build_benchmark_request(**args)
    assert args["seeds"] == source and args["target"] == target and frozen.sha256 == digest

def test_existing_current_freeze_service_supplies_real_field_and_skill_provenance(tmp_path):
    from tests.teams.graduation.test_team_axis_graduation_inputs import prepared as service_case
    from tests.teams.graduation.test_team_axis_graduation_inputs import save_profile
    from src.storage.sqlite.user_data_dao import UserDataDao
    from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
    from src.services.teams.axis_graduation.requests import _provenance as provenance_payload
    from src.integrations.battle_analysis.graduation.team_axis_graduation import _provenance as validate_provenance
    service, team, _ = service_case(tmp_path)
    with StaticGameDataDao(service.static_database_path) as static, UserDataDao(service.database_path) as dao:
        for cid in team.character_ids:
            save_profile(dao, cid, skill_levels={s["skill_id"]: 1 for s in static.list_character_skills(cid)})
        dao.replace_application_setting_copy("world_bonus", {"yaodao_attack_add": 0, "quantao_crit_damage": 0})
    frozen = service.freeze_current(team)
    assert not frozen.has_gaps
    actual = actual_component_input(frozen)
    evidence = provenance_payload(frozen)
    validate_provenance(evidence, actual)
    assert all(m["field_sources"]["character_level"] == "account" for m in evidence["members"])
    assert all(m["skill_level_sources"] for m in evidence["members"])
    assert all(c["profile"]["character_level"] == 70 and c["equipment"][0]["level"] == 0
               for c in actual["build"]["characters"])