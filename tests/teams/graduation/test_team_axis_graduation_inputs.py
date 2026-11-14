# 验证当前构筑一次冻结实际养成与来源装备，未知不借模板补齐，账号和来源变化阻止旧输入派发。
from concurrent.futures import CancelledError
from dataclasses import replace
import json

import pytest

from src.domain.teams.overview import SourceMode
from src.services.teams.axis_graduation.inputs import TeamAxisCurrentInputService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.overview.team_overview_fixture import overview_case


NTE_TEST_TIER = "core"


def prepared(tmp_path):
    overview, team, slots = overview_case(tmp_path)
    service = TeamAxisCurrentInputService(
        account_id=overview.account_id, generation=overview.generation,
        database_path=overview.database_path, static_database_path=overview.static_database_path,
        context_is_current=lambda: True,
    )
    return service, team, slots


def save_profile(dao, character_id, *, level=70, stage=5, skill_levels=None):
    return dao.save_character_profile(
        character_id=character_id, character_level=level, breakthrough_stage=stage,
        awakening_level=0, selected_awaken_effect_ids=(), awakening_selection_initialized=True,
        fork_id=None, fork_level=None, fork_breakthrough_stage=None, fork_refinement_level=None,
        likeability_level_10_enabled=False, skill_levels=skill_levels or {},
    )


def test_freeze_retains_actual_equipment_and_unknown_cultivation_without_personal_templates(tmp_path, monkeypatch):
    from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
    service, team, _ = prepared(tmp_path)
    monkeypatch.setattr(StaticGameDataDao, "get_character_graduation_template",
                        lambda *_args: pytest.fail("当前轴伤输入不读取个人毕业模板"))
    build = service.freeze_current(team)
    assert build.source_ready and build.has_gaps
    assert build.world_bonus is None and "world_bonus_unconfirmed" in build.gaps
    assert len(build.members) == 4
    for member in build.members:
        assert member.profile_evidence == {"account": {}, "native": {}, "effective": None}
        assert "character_level" in member.missing_fields
        assert "fork_id" in member.missing_fields
        assert all(item["level"] == 0 for item in member.equipment)
        core, = (item for item in member.equipment if item["kind"] == "core")
        assert any(stat["property_id"] == "AtkUp" and stat["value"] == .05 for stat in core["stats"])


def test_actual_account_growth_and_sparse_native_observation_keep_their_provenance(tmp_path):
    service, team, _ = prepared(tmp_path)
    character_id = team.character_ids[0]
    with UserDataDao(service.database_path) as dao:
        save_profile(dao, character_id)
        dao.patch_native_character_profiles(
            [{"character_id": character_id, "character_level": 60, "breakthrough_stage": 4}],
            growth_defaults={character_id: {"character_level": 80, "breakthrough_stage": 6}}, check=lambda: None,
        )
        dao.replace_application_setting_copy("world_bonus", {"yaodao_attack_add": 0, "quantao_crit_damage": 0})
    build = service.freeze_current(team)
    member = build.members[0]
    profile = member.profile_evidence["effective"]
    assert profile["character_level"] == 60 and profile["breakthrough_stage"] == 4
    assert profile["field_sources"]["character_level"] == "native_observed"
    assert profile["fork_id"] is None and "fork_id" not in member.missing_fields
    assert build.world_bonus == {"yaodao_attack_add": 0, "quantao_crit_damage": 0, "kaijia_defense_add": 30.0}


def test_single_native_field_does_not_materialize_a_template_breakthrough_or_max_profile(tmp_path):
    service, team, _ = prepared(tmp_path)
    character_id = team.character_ids[0]
    with UserDataDao(service.database_path) as dao:
        dao.patch_native_character_profiles(
            [{"character_id": character_id, "character_level": 70}],
            growth_defaults={character_id: {"character_level": 80, "breakthrough_stage": 6}}, check=lambda: None,
        )
    member = service.freeze_current(team).members[0]
    assert member.profile_evidence["account"] == {}
    assert member.profile_evidence["native"]["character_level"] == 70
    assert member.profile_evidence["effective"] is None
    assert "breakthrough_stage" in member.missing_fields


def test_frozen_inputs_are_detached_and_unrelated_account_writes_do_not_invalidate(tmp_path):
    service, team, _ = prepared(tmp_path)
    build = service.freeze_current(team)
    digest = build.sha256
    equipment = build.members[0].equipment
    equipment[0]["level"] = 20
    assert build.members[0].equipment[0]["level"] == 0 and build.sha256 == digest
    with UserDataDao(service.database_path) as dao:
        dao.replace_application_setting_copy("unrelated", {"value": 1})
    service.verify_current(build)
    assert service.freeze_current(team).sha256 == digest
    another_generation = replace(build, generation=build.generation + 1)
    assert another_generation.sha256 == digest
    with pytest.raises(CancelledError):
        service.verify_current(another_generation)


def test_saved_growth_and_source_change_invalidate_only_this_current_input(tmp_path):
    service, team, _ = prepared(tmp_path)
    build = service.freeze_current(team)
    with UserDataDao(service.database_path) as dao:
        save_profile(dao, team.character_ids[0])
    with pytest.raises(ValueError, match="当前构筑"):
        service.verify_current(build)
    build = service.freeze_current(team)
    with UserDataDao(service.database_path) as dao:
        dao.save_team_source(team, dao.get_team_source(team.team_id), mode=SourceMode.GAME)
    with pytest.raises(ValueError, match="当前构筑"):
        service.verify_current(build)


def test_game_source_and_input_freeze_are_read_only(tmp_path):
    service, team, _ = prepared(tmp_path)
    with UserDataDao(service.database_path) as dao:
        dao.save_team_source(team, dao.get_team_source(team.team_id), mode=SourceMode.GAME)
        before = tuple(dao._db().iterdump())
    build = service.freeze_current(team)
    assert build.source.mode == SourceMode.GAME
    assert len({member.snapshot_id for member in build.members}) == 1
    with UserDataDao(service.database_path) as dao:
        assert tuple(dao._db().iterdump()) == before


def test_missing_source_and_expired_account_do_not_create_or_dispatch_replacements(tmp_path):
    service, team, slots = prepared(tmp_path)
    with UserDataDao(service.database_path) as dao:
        dao.deactivate_loadout_plan(dao.get_loadout_slot(slots[team.character_ids[0]])["current_plan_id"])
    build = service.freeze_current(team)
    assert not build.source_ready and not build.members[0].equipment
    service._current = lambda: False
    with pytest.raises(CancelledError):
        service.freeze_current(team)


def test_partial_furniture_values_are_not_completed_with_full_defaults(tmp_path):
    service, team, _ = prepared(tmp_path)
    with UserDataDao(service.database_path) as dao:
        dao.replace_application_setting_copy("world_bonus", {"yaodao_attack_add": 0})
    build = service.freeze_current(team)
    assert build.world_bonus == {"yaodao_attack_add": 0}
    assert "quantao_crit_damage" not in build.world_bonus
    assert "world_bonus_unconfirmed" in build.gaps


def test_current_input_rejects_another_account_even_when_the_team_id_matches(tmp_path):
    service, team, _ = prepared(tmp_path)
    build = service.freeze_current(team)
    with pytest.raises(CancelledError):
        service.verify_current(replace(build, account_id="other-account"))
    service.account_id = "other-account"
    with pytest.raises(CancelledError, match="账号身份"):
        service.freeze_current(team)


@pytest.mark.parametrize("bad", [{"character_level": True}, {"skill_levels": {"skill": float("inf")}},
                                  {"field_sources": {"character_level": "template"}}])
def test_current_member_never_accepts_template_provenance_or_nonfinite_progress(tmp_path, bad):
    service, team, _ = prepared(tmp_path)
    member = service.freeze_current(team).members[0]
    evidence = member.profile_evidence
    evidence["effective"] = bad
    with pytest.raises(ValueError):
        replace(member, profile_evidence_json=json.dumps(evidence))


def test_sparse_skill_patch_records_mixed_sources_per_formal_skill_id(tmp_path):
    from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
    service, team, _ = prepared(tmp_path)
    character_id = team.character_ids[0]
    with static_catalog_read_unit(service.static_database_path) as unit:
        first, second = [row["skill_id"] for row in unit.dao.list_character_skills(character_id)][:2]
    with UserDataDao(service.database_path) as dao:
        save_profile(dao, character_id, skill_levels={first: 3, second: 4})
        dao.patch_native_character_profiles(
            [{"character_id": character_id, "skill_levels": {first: 5}}],
            growth_defaults={character_id: {"character_level": 80, "breakthrough_stage": 6}}, check=lambda: None,
        )
    profile = service.freeze_current(team).members[0].profile_evidence["effective"]
    assert profile["skill_levels"] == {first: 5, second: 4}
    assert profile["skill_level_sources"] == {first: "native_observed", second: "account"}
    assert profile["field_sources"]["skill_levels"] == "mixed"
