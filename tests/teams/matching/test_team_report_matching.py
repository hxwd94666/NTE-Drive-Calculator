# 验证正式四人阵容匹配、半场候选、无库存发现和过期证据隔离。
from concurrent.futures import CancelledError
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.domain.teams.models import Team, TeamDraft
from src.domain.teams.report_matching import (
    MatchStatus, ReportRange, classify_evidence, discover_candidates,
)
from src.domain.battle_report.history.records import BattleReportHistoryEntry


def entry(record_id=1, *, halves=False):
    return BattleReportHistoryEntry(record_id, "manual", "2026-10-19T00:00:00+00:00", "abyss" if halves else "non_abyss",
        None, halves, halves, (1,), 100, 10, 10, 5, "complete", "nte_core", native_capture=True)


def evidence(ids=(1, 2, 3, 4), *, reason=None):
    return dict(ready=reason is None, reason=reason, team_character_ids=list(ids), source_input_digest="a" * 64)


def test_candidates_split_halves_and_never_filter_by_damage_participants():
    candidates = discover_candidates((entry(), entry(2, halves=True)))
    assert [(item.range.record_id, item.range.scope) for item in candidates] == [(2, "first"), (2, "second"), (1, "all")]
    # 摘要只有一人出伤；正式队伍中的其余三人不能提前被排除。
    team = Team("team", "正式队伍", (4, 3, 2, 1), 1)
    result = classify_evidence(team, candidates[-1], evidence())
    assert result.status == MatchStatus.MATCHED
    assert result.report_character_ids == (1, 2, 3, 4)


@pytest.mark.parametrize("ids,reason,status", [
    ((1, 2, 3, 5), "axis_selected_outside_team", MatchStatus.DIFFERENT),
    ((), "axis_team_members_incomplete", MatchStatus.INSUFFICIENT),
    ((), "axis_team_changed", MatchStatus.CONFLICT),
    ((), "axis_team_scope_conflict", MatchStatus.CONFLICT),
])
def test_incomplete_and_different_teams_are_not_exact_matches(ids, reason, status):
    team = Team("team", "队伍", (1, 2, 3, 4), 1)
    result = classify_evidence(team, discover_candidates((entry(),))[0], evidence(ids, reason=reason))
    assert result.status == status


def test_range_is_an_explicit_half_open_raw_microsecond_selector():
    chosen = ReportRange(2, "range", 500_000, 1_000_001)
    assert chosen.selector_fields() == dict(battle_record_id=2, start_us=500_000, end_us=1_000_001, detail_scope=None)
    for args in ((2, "range", 3, 3), (2, "first", 1, 2), (2, "all", None, 2)):
        with pytest.raises(ValueError):
            ReportRange(*args)


@pytest.fixture
def matching_case(tmp_path):
    from tests.battle_report.analysis.axis_evaluation_catalog_fixture import create_axis_catalog_case
    from src.services.teams.report_matching import TeamReportMatchingService
    from src.storage.sqlite.user_data_dao import UserDataDao
    case = create_axis_catalog_case(tmp_path)
    deps = case["dependencies"]
    with UserDataDao(deps.user_database_path) as dao:
        team = dao.save_team(TeamDraft("冻结队伍", (1072, 1003, 1004, 1008)))
    state = {"current": True, "digest": "a" * 64, "calls": 0}

    def resolve(selector, ids, **kwargs):
        kwargs["checkpoint"]()
        state["calls"] += 1
        return evidence(tuple(sorted(ids))) | {"source_input_digest": state["digest"]}

    client = SimpleNamespace(supports_battle_axis_team=True, supports_battle_page_identity=True,
        dataset_version=json.loads(deps.static_database_path.with_name("manifest.json").read_text(encoding="utf-8"))["database"]["dataset_id"],
        engine_version="test", component_provenance={}, timeout=1,
        resolve_battle_axis_team=resolve,
        load_battle_page_identity=lambda selector, **kwargs: state["digest"])
    service = TeamReportMatchingService(dependencies=deps, context_is_current=lambda _: state["current"],
        client_factory=lambda **kwargs: client)
    return case, service, team, state, client


def test_discovery_and_matching_do_not_require_current_inventory(matching_case, monkeypatch):
    from src.storage.sqlite.user_data_dao import UserDataDao
    _, service, team, _, _ = matching_case
    monkeypatch.setattr(UserDataDao, "current_inventory_snapshot_id", lambda self: pytest.fail("匹配不应读取当前库存"))
    discovery = service.discover()
    assert team in discovery.teams and discovery.candidates
    page = service.match_page(team, discovery.candidates, offset=0, limit=10)
    assert page.results[0].status == MatchStatus.MATCHED


def test_cache_rechecks_formal_digest_and_ignores_unrelated_settings(matching_case):
    from src.storage.sqlite.user_data_dao import UserDataDao
    case, service, team, state, _ = matching_case
    candidates = service.discover().candidates
    first = service.match_page(team, candidates)
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        dao.replace_application_setting_copy("unrelated", {"value": True})
    second = service.match_page(team, candidates)
    assert first.results == second.results and state["calls"] == 1
    state["digest"] = "b" * 64
    service.match_page(team, candidates)
    assert state["calls"] == 2


def test_generation_team_revision_and_cancel_revoke_results(matching_case):
    from src.storage.sqlite.user_data_dao import UserDataDao
    case, service, team, state, _ = matching_case
    candidates = service.discover().candidates
    with pytest.raises(CancelledError):
        service.match_page(team, candidates, cancelled=lambda: True)
    state["current"] = False
    with pytest.raises(CancelledError):
        service.discover()
    state["current"] = True
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        dao.save_team(TeamDraft("新名称", team.character_ids), existing=team)
    with pytest.raises(ValueError, match="队伍"):
        service.match_page(team, candidates)


def test_candidate_failure_is_isolated_and_does_not_poison_retry(matching_case):
    _, service, team, state, client = matching_case
    candidates = service.discover().candidates
    resolve = client.resolve_battle_axis_team
    client.resolve_battle_axis_team = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("查询故障"))
    assert service.match_page(team, candidates).results[0].status == MatchStatus.ERROR
    client.resolve_battle_axis_team = resolve
    assert service.match_page(team, candidates).results[0].status == MatchStatus.MATCHED


def test_deleted_report_and_missing_component_do_not_become_zero_matches(matching_case):
    from src.storage.sqlite.user_data_dao import UserDataDao
    case, service, team, _, client = matching_case
    candidates = service.discover().candidates
    client.supports_battle_axis_team = False
    assert service.match_page(team, candidates).results[0].status == MatchStatus.UNAVAILABLE
    client.supports_battle_axis_team = True
    with UserDataDao(case["dependencies"].user_database_path) as dao:
        dao.delete_battle_record(case["battle_record_id"])
    assert service.match_page(team, candidates).results[0].status == MatchStatus.INSUFFICIENT


def test_no_inventory_allows_axis_configuration_but_formal_computation_still_requires_it(matching_case, monkeypatch):
    from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
    from src.services.allocation.axis.axis_selection import AxisAllocationSelectionService
    from src.services.allocation.axis.axis_team_preflight import prepare_axis_team_selection
    from src.storage.sqlite.user_data_dao import UserDataDao
    case, matcher, _, _, _ = matching_case
    monkeypatch.setattr(UserDataDao, "current_inventory_snapshot_id", lambda self: None)
    selection_service = AxisAllocationSelectionService(dependencies=matcher.dependencies, context_is_current=lambda _: True)
    choices = selection_service.choices(["灵可"])
    assert choices.role_character_ids == (("灵可", 1072),)
    selected = selection_service.select(choices, battle_record_id=case["battle_record_id"],
        selected_role="__all_groups__", detail_scope="all", anchor_mode="original")
    with static_catalog_read_unit(matcher.dependencies.static_database_path) as unit:
        with pytest.raises(ValueError, match="完整稳定库存"):
            prepare_axis_team_selection(None, selected, ["灵可"], user_database_path=matcher.dependencies.user_database_path,
                static_read_unit=unit, semantics_path=matcher.semantics_path)


def test_cold_catalog_discovery_has_formal_names_but_does_not_auto_select_roles(matching_case):
    from src.services.allocation.axis.axis_selection import AxisAllocationSelectionService
    case, matcher, _, _, _ = matching_case
    service = AxisAllocationSelectionService(dependencies=matcher.dependencies, context_is_current=lambda _: True)
    choices = service.choices(())
    assert ("灵可", 1072) in choices.role_character_ids and choices.selected_roles == ()
    with pytest.raises(ValueError):
        service.select(choices, battle_record_id=case["battle_record_id"], selected_role="__all_groups__",
                       detail_scope="all", anchor_mode="original")


def test_resource_identity_change_invalidates_cached_team_evidence(matching_case, tmp_path):
    _, service, team, state, client = matching_case
    path = tmp_path / "semantics.json"
    path.write_text("{}", encoding="utf-8")
    service.semantics_path = path
    candidates = service.discover().candidates
    service.match_page(team, candidates)
    path.write_text("{}\n", encoding="utf-8")
    service.match_page(team, candidates)
    assert state["calls"] == 2
    client.component_provenance["component_manifest_sha256"] = "b" * 64
    service.match_page(team, candidates)
    assert state["calls"] == 3


def test_context_changes_during_native_call_publish_no_result(matching_case):
    _, service, team, state, client = matching_case
    candidates = service.discover().candidates
    original = client.resolve_battle_axis_team
    def resolve(*args, **kwargs):
        result = original(*args, **kwargs)
        state["current"] = False
        return result
    client.resolve_battle_axis_team = resolve
    published = []
    with pytest.raises(CancelledError):
        service.match_page(team, candidates, publish=published.append)
    assert published == []


def test_missing_static_identity_keeps_history_browsable_without_implicit_default(matching_case):
    _, service, team, _, _ = matching_case
    service.dependencies = replace(service.dependencies, static_database_path=None)
    discovery = service.discover()
    result = service.match_page(team, discovery.candidates).results[0]
    assert result.status == MatchStatus.UNAVAILABLE and "静态" in result.reason


def test_confirmed_report_digest_change_stops_before_allocation_preflight(matching_case):
    from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
    from src.services.allocation.axis.axis_selection import AxisAllocationSelectionService
    from src.services.allocation.axis.axis_team_preflight import prepare_axis_team_selection
    case, matcher, _, _, _ = matching_case
    service = AxisAllocationSelectionService(dependencies=matcher.dependencies, context_is_current=lambda _: True)
    selected = service.select(service.choices(["灵可"]), battle_record_id=case["battle_record_id"],
        selected_role="__all_groups__", detail_scope="all", anchor_mode="original")
    selected = replace(selected, expected_source_input_digest="a" * 64)
    client = SimpleNamespace(resolve_battle_axis_fixed_team=lambda *args, **kwargs: {"source_input_digest": "b" * 64})
    with static_catalog_read_unit(matcher.dependencies.static_database_path) as unit:
        with pytest.raises(ValueError, match="确认后已变化"):
            prepare_axis_team_selection(client, selected, ["灵可"], user_database_path=matcher.dependencies.user_database_path,
                static_read_unit=unit, semantics_path=matcher.semantics_path)
