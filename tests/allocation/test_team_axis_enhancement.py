# 验证队伍卡片互斥选择、独立战报绑定、库存先后分配及双战报归一收益口径。
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.domain.teams.models import Team, TeamDraft
from src.domain.battle_report.history.records import BattleReportHistoryEntry
from src.services.allocation.axis.axis_selection import AxisAllocationChoices, AxisAllocationSelection
from src.services.allocation.axis.team_axis_selection import TeamAxisBinding, TeamAxisSelection, validate_team_group, verify_team_selection
from src.services.allocation.axis.team_enhancement import execute_team_enhancement
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.teams.matching.team_report_fixture import MatchingFixture
from src.domain.teams.report_matching import ReportCandidate, ReportRange


def teams():
    return (Team("a", "常用第一队", (1, 2, 3, 4), 1), Team("b", "另一玩法队伍", (5, 6, 7, 8), 1),
            Team("c", "重复成员队伍", (1, 9, 10, 11), 1), Team("d", "第三队", (12, 13, 14, 15), 1))


@pytest.mark.parametrize("selection", [(), teams()[:3], (teams()[0], teams()[2]),
                                      (replace(teams()[0], character_ids=(1, 2)),)])
def test_enhancement_rejects_missing_overlapping_or_more_than_two_teams(selection):
    with pytest.raises(ValueError):
        validate_team_group(selection)


def choices():
    entry = BattleReportHistoryEntry(1, "manual", "2026-10-16T10:00:00+00:00", "non_abyss", None,
        False, False, tuple(range(1, 9)), 1000, 10, 100, 30, "complete", "nte_core", "示例场景")
    return AxisAllocationChoices((entry, replace(entry, battle_record_id=2)),
                                 tuple((f"角色{i}", i) for i in range(1, 16)), frozenset(), "test", ())


def test_team_cards_show_names_members_and_independent_report_fields():
    from tests.support.auto_sync_ui_fixture import application
    from tests.support.auto_sync_ui_fixture import dispose
    from src.features.allocation.team_axis.team_axis_dialog import TeamAxisDialog
    application()
    dialog = TeamAxisDialog(service=None, matching_service=MatchingFixture(), teams=teams(), choices=choices())
    try:
        dialog.show()
        a, b, overlap, third = dialog._rows
        assert a["card"].title.text() == "常用第一队"
        assert "常用第一队" in a["check"].accessibleName()
        assert [button.text() for button in a["slots"].buttons] == [f"角色{i}" for i in range(1, 5)]
        a["check"].click()
        assert not overlap["check"].isEnabled() and "角色1" in overlap["reason"].text()
        b["check"].click()
        assert not third["check"].isEnabled()
        for row, entry in zip((a, b), choices().entries):
            candidate = ReportCandidate(entry, ReportRange(entry.battle_record_id))
            result = MatchingFixture().match_page(row["team"], (candidate,)).results[0]
            dialog.set_report(row["team"].team_id, result)
        assert (a["match"].candidate.range.record_id, b["match"].candidate.range.record_id) == (1, 2)
        assert "示例场景" in a["report"].text() and "整场" in a["report"].text()
        a["check"].click()
        assert overlap["check"].isEnabled()
    finally:
        dispose(dialog)


def selection_for_database(tmp_path):
    from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
    static_path = Path(__file__).resolve().parents[2] / "data/game_static.sqlite3"
    with static_catalog_read_unit(static_path) as unit:
        dataset = unit.release.dataset_id
    path = tmp_path / "account.sqlite3"
    with UserDataDao(path, account_id="teams") as dao:
        saved = tuple(dao.save_team(TeamDraft(team.name, team.character_ids)) for team in teams()[:2])
    bindings = tuple(TeamAxisBinding(team, tuple(f"角色{i}" for i in team.character_ids),
        AxisAllocationSelection("teams", 1, path.resolve(), static_path, dataset, None, None,
                                SimpleNamespace(battle_record_id=index + 1), "original", team.character_ids, True))
        for index, team in enumerate(saved))
    return path, TeamAxisSelection(bindings)


def test_joint_teams_run_before_remaining_roles_without_reusing_their_uids(tmp_path):
    path, selection = selection_for_database(tmp_path)
    calls = []

    def execute(inventory, roles, **options):
        calls.append((roles, options))
        return {role: {"valid": True, "assigned_tape": {"uid": role}, "assigned_set_drives": [],
                       "assigned_extra_drives": []} for role in roles}, None

    facade = SimpleNamespace(user_database_path=path, execute_allocation_inventory=execute)
    selected = ["其余角色", *selection.roles]
    result, _ = execute_team_enhancement(facade, [], selected, selection, mode="role_priority",
                                         options={"priority_groups": [["其余角色"], list(selection.roles)]})
    assert calls[0][0] == list(selection.roles)
    assert len(calls[0][1]["axis_selection"]) == 2
    assert calls[1][0] == ["其余角色"]
    assert set(selection.roles) <= calls[1][1]["locked_uids"]
    assert set(result) == set(selected)


def test_changed_team_revision_invalidates_configuration(tmp_path):
    path, selection = selection_for_database(tmp_path)
    with UserDataDao(path) as dao:
        dao.save_team(TeamDraft("更名后队伍", (1, 2, 3, 4)), existing=selection.teams[0].team)
    with pytest.raises(ValueError, match="已变更"):
        verify_team_selection(selection, path)


def test_baseline_merges_within_team_ranks_not_global_damage_share(tmp_path):
    from src.services.allocation.axis.team_enhancement import ranked_team_baseline
    _, selection = selection_for_database(tmp_path)
    axes = tuple((replace(binding.axis, selected_team_character_ids=binding.team.character_ids,
                          damage_priority_groups=tuple((cid,) for cid in binding.team.character_ids),
                          rank_baseline=True), binding.roles) for binding in selection.teams)
    order, groups, frozen = ranked_team_baseline(axes, list(selection.roles))
    assert groups == [["角色1", "角色5"], ["角色2", "角色6"], ["角色3", "角色7"], ["角色4", "角色8"]]
    assert order == [role for group in groups for role in group]
    assert frozen[0][0].selected_team_character_ids == (1, 2, 3, 4)
    # 一个已锁定角色不挤掉本队其他成员的原始名次；相同伤害仍同级。
    axes = ((replace(axes[0][0], selected_team_character_ids=(2, 3, 4),
                     damage_priority_groups=((1,), (2, 3), (), (4,))), axes[0][1][1:]), axes[1])
    _, groups, _ = ranked_team_baseline(axes, list(selection.roles)[1:])
    assert groups == [["角色5"], ["角色2", "角色3", "角色6"], ["角色7"], ["角色4", "角色8"]]


def test_missing_rank_evidence_falls_back_without_fabricating_damage(tmp_path):
    from src.services.allocation.axis.team_enhancement import ranked_team_baseline
    _, selection = selection_for_database(tmp_path)
    axes = tuple((replace(binding.axis, rank_baseline=True), binding.roles) for binding in selection.teams)
    order, groups, _ = ranked_team_baseline(axes, list(selection.roles))
    assert groups == [list(selection.roles)] and order == list(selection.roles)


def joint_wire():
    from tests.allocation.test_group_postprocess_wire import group_request, group_response
    request = group_request(axis=True)
    original = group_response(axis=True)
    request["role_character_ids"] = {"A": 1072, "B": 1003}
    specs, reports = [], []
    for key, scale, gain in ((1072, 1, -.02), (1003, 100, .08)):
        spec = dict(character_ids=[key], battle_request=deepcopy(request["battle_request"]),
                    anchor_mode="original", team_preflight=deepcopy(request["team_preflight"]))
        spec["team_preflight"]["source_input_digest"] = original["source_input_digest"]
        specs.append(spec)
        report = {key: deepcopy(original[key]) for key in ("source_input_digest", "frozen_identity", "scope", "baseline", "selected_result")}
        report["scope"]["selected_character_ids"] = [key]
        base = report["baseline"]["candidate_value"] * scale
        for name, amount in (("baseline", base), ("selected_result", base * (1 + gain))):
            row = report[name]
            anchor = row["anchor_value"] * scale
            row.update(anchor_value=anchor, candidate_value=amount, delta_from_anchor=amount-anchor,
                       delta_from_baseline=amount-base)
            quant = row["quantification"]
            for field in ("basis_damage", "fully_quantified_damage", "proven_unchanged_damage", "partially_quantified_damage", "unavailable_damage", "not_applicable_damage"):
                if field in quant:
                    quant[field] *= scale
            quant["quantified_increment"] = amount-anchor
        reports.append(report)
    request["team_axes"] = specs
    result = {**original, "team_reports": reports, "baseline": {"candidate_value": 1.0},
              "selected_result": {"candidate_value": 1.03}}
    return request, result


def test_two_report_wire_and_display_use_equal_relative_gain_with_tradeoff():
    from src.integrations.battle_analysis.team_axis_response import decode_team_facts
    from src.features.allocation.results.damage_result_summary import damage_optimization_presentation
    request, result = joint_wire()
    facts, reports = decode_team_facts(result, request)
    assert facts["candidates"][0]["candidate_value"] == pytest.approx(1.03)
    value = dict(objective="frozen_axis_expected_v1", status="completed", baseline_value=1.0,
                 candidate_value=1.03, team_reports=reports)
    presentation = damage_optimization_presentation(value)
    assert "双队综合增幅 +3.00%" in presentation.headline
    assert "-2.00%" in presentation.note and "+8.00%" in presentation.note
    result["selected_result"]["candidate_value"] = 1.08
    with pytest.raises(RuntimeError):
        decode_team_facts(result, request)
