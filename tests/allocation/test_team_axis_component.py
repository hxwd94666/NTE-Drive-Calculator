# 在临时账号中用两份独立四人战报验证实际原生作业、归一收益和冻结保存。
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.allocation.test_axis_postprocess_component import axis_facade_case, run_axis_facade
from tests.allocation.axis_team_fixture import attach_native_team, attach_fixed_builds
from tests.battle_report.analysis.axis_evaluation_catalog_fixture import create_axis_catalog_case
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.services.allocation.axis.axis_selection import AxisAllocationSelectionService
from src.services.allocation.inputs.inventory import SqliteAllocationInventory
from src.services.official_role.page import load_official_role_detail
from src.services.official_role.panel.attributes import calculate_official_role_combat_stat_components
from src.storage.sqlite.user_data_dao import UserDataDao


def two_team_case(tmp_path):
    executable = os.environ.get("NTE_BATTLE_AXIS_TEST_EXE")
    if not executable or not Path(executable).is_file():
        pytest.skip("双队协议专项需显式指定本地候选分析程序")
    first = axis_facade_case(tmp_path, joint=True)
    with static_catalog_read_unit(first["dependencies"].static_database_path) as unit:
        ids = [int(row["character_id"]) for row in unit.dao.list_role_template_characters()
               if int(row["character_id"]) not in (1072, 1003) and unit.dao.get_equipment_plan(int(row["character_id"]))]
    first_ids, second_ids = (1072, *ids[:3]), (1003, *ids[3:6])
    attach_native_team(first, first_ids)
    attach_fixed_builds(first, ids=first_ids)
    second = create_axis_catalog_case(tmp_path, core_suit_id="Suit1", main_character_id=1003,
                                     capture_suffix="-second", merge_inventory=True)
    attach_native_team(second, second_ids)
    attach_fixed_builds(second, ids=second_ids)
    return first, second, first_ids, second_ids


@pytest.mark.parametrize("search_mode", ["fast", "deep"])
def test_two_real_report_jobs_use_one_inventory_and_preserve_two_objectives(tmp_path, search_mode):
    first, second, _, _ = two_team_case(tmp_path)
    service = AxisAllocationSelectionService(dependencies=first["dependencies"], context_is_current=lambda _: True)
    choices = service.choices(["灵可", "早雾"])
    axes = []
    for case, role in ((first, "灵可"), (second, "早雾")):
        selected = service.select(choices, battle_record_id=case["battle_record_id"], selected_role="__all_groups__",
                                  detail_scope="all", anchor_mode="original")
        axes.append((replace(selected, rank_baseline=True, search_mode=search_mode), (role,)))
    with static_catalog_read_unit(first["dependencies"].static_database_path) as unit, UserDataDao(first["dependencies"].user_database_path) as dao:
        first["source_snapshot_id"] = dao.current_inventory_snapshot_id()
        first["inventory"] = list(SqliteAllocationInventory(dao, unit.dao).build(first["source_snapshot_id"]).items)
        detail = load_official_role_detail(first["dependencies"].user_database_path, 1003, include_inventory_contexts=False,
            include_replacement_candidates=False, static_database_path=unit.release.database_path, static_read_unit=unit)
        fixed = {row.key: row.value for row in calculate_official_role_combat_stat_components(detail, [])}
        first["crit_baselines"]["早雾"] = round((fixed["CritBase"]-.05)*100, 4)
    first["selected_roles"] = ["灵可", "早雾"]
    first["selection"] = tuple(axes)
    captured = []
    sent_budgets = []
    import src.integrations.allocation.damage_allocation_job as job
    decode = job._decode

    def capture(*args, **kwargs):
        sent_budgets.append(args[3]["max_millis"])
        captured.append(args[4])
        return decode(*args, **kwargs)

    with patch.object(job, "_decode", side_effect=capture):
        plans, client = run_axis_facade(first, budget={"max_evaluations": 12, "max_rounds": 1})
    assert client.supports_battle_axis_joint_teams
    assert sent_budgets == [600_000 if search_mode == "deep" else 120_000]
    assert client.timeout == (601 if search_mode == "deep" else 121)
    assert captured and captured[0]["result"] is not None
    result = captured[0]["result"]
    assert result["version"] == 3, result.get("optimization")
    assert len(result["team_reports"]) == 2, result.get("optimization")
    baseline = result["baseline"]["candidate_value"]
    assert baseline == 1
    ratios = [row["selected_result"]["candidate_value"] / row["baseline"]["candidate_value"]
              for row in result["team_reports"]]
    assert result["selected_result"]["candidate_value"] == pytest.approx(sum(ratios)/2)
    assert set(plans) == {"灵可", "早雾"}
    uids = [item.uid for plan in plans.values() for item in
            [plan["assigned_tape"], *plan["assigned_set_drives"], *plan["assigned_extra_drives"]]]
    assert len(uids) == len(set(uids))
    assert all(plan["damage_optimization"]["status"] == "completed" for plan in plans.values()), {
        role: plan["damage_optimization"] for role, plan in plans.items()}
    from src.services.allocation.inputs.locks import build_allocation_lock_snapshot
    from src.services.allocation.results.plan_save import save_allocation_plans, legacy_plan_tape_main_values
    from src.services.allocation.results.postprocess_facts import allocation_role_state_from_plan
    from src.services.allocation.direct.direct_postprocess import optimization_payload
    with UserDataDao(first["dependencies"].user_database_path) as dao:
        locks = build_allocation_lock_snapshot(dao, inventory_snapshot_id=first["source_snapshot_id"])
    with static_catalog_read_unit(first["dependencies"].static_database_path) as unit:
        release = unit.release
    rows = []
    for role, cid in (("灵可", 1072), ("早雾", 1003)):
        plan = plans[role]
        items = [plan["assigned_tape"], *plan["assigned_set_drives"], *plan["assigned_extra_drives"]]
        rows.append(dict(role_name=role, character_id=cid, snapshot_id=first["source_snapshot_id"], slot_id=None,
            name="双队联合专项", score=plan["score"], role_state=allocation_role_state_from_plan(plan),
            axis_preview_source=plan["axis_preview_source"], payload={"source_role_name": role,
                "assignment_scores": {item.uid: item.role_scores[role] for item in items},
                "tape_main_values": legacy_plan_tape_main_values(plan), **optimization_payload(plan)}))
    arguments = dict(database_path=first["dependencies"].user_database_path, static_database_path=release.database_path,
        static_identity=(release.database_path, release.dataset_id), static_release=release, lock_snapshot=locks,
        checkpoint=lambda: None, progress=lambda _: None, axis_analysis_client=client)
    with pytest.raises(RuntimeError, match="一起保存|全部成员|完整"):
        save_allocation_plans(rows=rows[:1], **arguments)
    assert save_allocation_plans(rows=rows, **arguments) == 2
    with UserDataDao(first["dependencies"].user_database_path) as dao:
        saved = dao.list_loadout_slots(1003)[0]["current_plan"]
    assert saved["payload"]["damage_optimization"]["team_reports"] == rows[1]["payload"]["damage_optimization"]["team_reports"]
    assert saved["payload"]["damage_optimization"]["baseline_priority"]["policy"] == "within_team_damage_rank_v1"

    # 旧组件没有双队能力时保留八人/所选成员的共同 B0，不伪造联合收益。
    from src.integrations.analysis_core.nte_analysis_core import NteAnalysisCoreClient
    client.supports_battle_axis_joint_teams = False
    with patch.object(NteAnalysisCoreClient, "from_executable", return_value=client):
        baseline_plans, _ = run_axis_facade(first, budget={"max_evaluations": 2, "max_rounds": 1})
    assert all(plan["damage_optimization"]["result_kind"] == "baseline" and plan.get("baseline_preview_source")
               for plan in baseline_plans.values())


def test_eight_members_use_ranked_current_inventory_baseline(tmp_path):
    from src.domain.teams.models import TeamDraft
    from src.services.allocation.axis.team_axis_selection import select_team_axes
    first, second, first_ids, second_ids = two_team_case(tmp_path)
    with static_catalog_read_unit(first["dependencies"].static_database_path) as unit, UserDataDao(first["dependencies"].user_database_path) as dao:
        from src.services.official_role.page import load_official_role_index
        names = {int(row["character_id"]): row["name_zh"] for row in load_official_role_index(
            first["dependencies"].user_database_path, static_database_path=unit.release.database_path)}
        # 当前库存比两场历史装备多一张卡带；B0 只能消费这个最新的完整冻结快照。
        _, raw = dao.export_inventory_snapshot(dao.current_inventory_snapshot_id())
        native = [{**row, "uid": {"slot": row["uid_slot"], "serial": row["uid_serial"]}, "grid": row["grid_count"]} for row in raw]
        core = next(row for row in native if row["kind"] == "core")
        native.append({**core, "uid": {"slot": 4, "serial": 999999}, "uid_slot": 4, "uid_serial": 999999,
                       "equipped": False, "equipped_character_id": None, "equipped_placement": None})
        snapshot = dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
            "complete": True, "generation": 1, "sequence": 9, "observed_at_unix_ms": 1_787_000_000_009,
            "item_count": len(native), "items": native}})
        first["inventory"] = list(SqliteAllocationInventory(dao, unit.dao).build(snapshot).items)
        first["source_snapshot_id"] = snapshot
        teams = [dao.save_team(TeamDraft(label, tuple(reversed(ids)))) for label, ids in
                 (("第一队", first_ids), ("另一队", second_ids))]
        first["selected_roles"] = [names[cid] for team in teams for cid in team.character_ids]
        for cid in (*first_ids, *second_ids):
            detail = load_official_role_detail(first["dependencies"].user_database_path, cid, include_inventory_contexts=False,
                include_replacement_candidates=False, static_database_path=unit.release.database_path, static_read_unit=unit)
            fixed = {row.key: row.value for row in calculate_official_role_combat_stat_components(detail, [])}
            first["crit_baselines"][names[cid]] = round((fixed["CritBase"]-.05)*100, 4)
    service = AxisAllocationSelectionService(dependencies=first["dependencies"], context_is_current=lambda _: True)
    choices = service.choices(first["selected_roles"])
    first["selection"] = select_team_axes(service, choices, [
        (teams[0], first["battle_record_id"], "all", "original"),
        (teams[1], second["battle_record_id"], "all", "original")])
    observed = []
    plans, _ = run_axis_facade(first, budget={"max_evaluations": 2, "max_rounds": 1},
                               observer=lambda request, scorer: observed.append(request))
    assert len(observed) == 1
    groups = observed[0].priority_groups
    assert set(groups[0]) == {names[1072], names[1003]}
    assert len(groups) == 2 and len(groups[1]) == 6
    assert len(plans) == 8 and all(plan["valid"] for plan in plans.values())
    assert all(plan["damage_optimization"]["source_snapshot_id"] == snapshot for plan in plans.values())
    assert all(plan["damage_optimization"]["baseline_priority"]["policy"] == "within_team_damage_rank_v1" for plan in plans.values())
    uids = [item.uid for plan in plans.values() for item in
            [plan["assigned_tape"], *plan["assigned_set_drives"], *plan["assigned_extra_drives"]]]
    assert len(uids) == len(set(uids))
    # 两队增强的轻量记录与八人成员方案一起提交；一队子集不能冒充联合作业完成。
    from tests.teams.enhancement.team_enhancement_fixture import enhancement_rows
    from src.services.allocation.inputs.locks import build_allocation_lock_snapshot
    from src.services.allocation.results.plan_save import save_allocation_plans
    pairs = tuple((name, key) for binding in first["selection"].teams
                  for name, key in zip(binding.roles, binding.team.character_ids))
    rows = enhancement_rows(plans, pairs, snapshot)
    assert all(row["team_enhancement_preview"] is not None for row in rows)
    with static_catalog_read_unit(first["dependencies"].static_database_path) as unit, UserDataDao(first["dependencies"].user_database_path) as dao:
        release = unit.release
        locks = build_allocation_lock_snapshot(dao, inventory_snapshot_id=snapshot)
    arguments = dict(database_path=first["dependencies"].user_database_path, static_database_path=release.database_path,
        static_identity=(release.database_path, release.dataset_id), static_release=release, lock_snapshot=locks,
        checkpoint=lambda: None, progress=lambda _: None)
    with pytest.raises(ValueError, match="完整构筑"):
        save_allocation_plans(rows=rows[:4], **arguments)
    assert save_allocation_plans(rows=rows, **arguments) == 8
    with UserDataDao(first["dependencies"].user_database_path) as dao:
        saved = [dao.list_team_enhancements(team.team_id)[0] for team in teams]
        assert saved[0].run_id == saved[1].run_id
        assert all(len(value.members) == 4 and value.joint_summary_json for value in saved)
        assert {value.payload["scope"]["record_id"] for value in saved} == {first["battle_record_id"], second["battle_record_id"]}
