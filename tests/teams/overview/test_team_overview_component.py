# 用随附分析程序和合成完整库存贯通四人增强、预览信封、原子保存与重复确认，不连接游戏。
from copy import deepcopy

from src.app.facade import NTEAppFacade
from src.integrations.analysis_core.analysis_core_release import create_bundled_analysis_client
from src.integrations.static_catalog.static_catalog_release import static_catalog_read_unit
from src.services.allocation.axis.axis_selection import AxisAllocationSelectionService
from src.services.allocation.direct.direct_postprocess import optimization_payload
from src.services.allocation.inputs.inventory import SqliteAllocationInventory
from src.services.allocation.inputs.locks import build_allocation_lock_snapshot
from src.services.allocation.results.plan_save import legacy_plan_tape_main_values, save_allocation_plans
from src.services.allocation.results.postprocess_facts import allocation_role_state_from_plan
from src.services.allocation.axis.team_axis_selection import select_team_axes
from src.services.official_role.panel.attributes import calculate_official_role_combat_stat_components
from src.services.official_role.page import load_official_role_detail
from src.domain.teams.models import TeamDraft
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.allocation.test_axis_postprocess_component import axis_facade_case
from tests.battle_report.analysis.axis_evaluation_catalog_fixture import ROOT


def test_actual_four_person_run_saves_one_team_result_and_same_run_does_not_resave(tmp_path):
    case = axis_facade_case(tmp_path, joint=True)
    deps = case["dependencies"]
    ids = (1072, 1003, 1004, 1008)
    with static_catalog_read_unit(deps.static_database_path) as unit, UserDataDao(deps.user_database_path) as dao:
        _, raw = dao.export_inventory_snapshot(case["source_snapshot_id"])
        native = [{**row, "uid": {"slot": row["uid_slot"], "serial": row["uid_serial"]}, "grid": row["grid_count"]} for row in raw]
        core = next(row for row in native if row["kind"] == "core")
        for index in range(3):
            spare = deepcopy(core)
            spare["uid"] = {"slot": 4, "serial": 10000 + index}
            native.append(spare)
        snapshot = dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
            "complete": True, "generation": 3, "sequence": 3, "observed_at_unix_ms": 1787000003000,
            "item_count": len(native), "items": native}})
        team = dao.save_team(TeamDraft("实际四人增强", ids))
        names = tuple(unit.dao.get_character(key)["name_zh"] for key in ids)
        inventory = list(SqliteAllocationInventory(dao, unit.dao).build(snapshot).items)
        baselines = {}
        for key, name in zip(ids, names):
            detail = load_official_role_detail(deps.user_database_path, key, include_inventory_contexts=False,
                include_replacement_candidates=False, static_database_path=unit.release.database_path, static_read_unit=unit)
            fixed = {row.key: row.value for row in calculate_official_role_combat_stat_components(detail, [])}
            baselines[name] = round((fixed["CritBase"]-.05)*100, 4)
        release = unit.release
    service = AxisAllocationSelectionService(dependencies=deps, context_is_current=lambda _: True)
    selection = select_team_axes(service, service.choices(names), [(team, case["battle_record_id"], "all", "original")])
    facade = NTEAppFacade(config_dir=ROOT / "config", user_config_dir=ROOT / "config",
        user_database_path=deps.user_database_path, allocation_static_database_path=deps.static_database_path)
    plans, _ = facade.execute_allocation_inventory(inventory, names, mode="role_priority", axis_selection=selection,
        custom_sets={name: case["suit_name"] for name in names}, set_effect_modes={name: "none" for name in names},
        tape_main_filters={name: ["攻击力%"] for name in names}, crit_rate_baselines=baselines,
        postprocess_budget={"max_evaluations": 1, "max_rounds": 1})
    assert all(plan.get("valid") for plan in plans.values()), {name: plan.get("failure_reason") for name, plan in plans.items()}
    preview = plans[names[0]]["team_enhancement_preview"]
    assert len(preview.commit.entries) == 1 and all(plans[name]["team_enhancement_preview"] == preview for name in names)
    with UserDataDao(deps.user_database_path) as dao:
        assert not dao.list_team_enhancements(team.team_id)
        locks = build_allocation_lock_snapshot(dao, inventory_snapshot_id=snapshot)
    rows = []
    for name, key in zip(names, ids):
        plan = plans[name]
        items = [plan["assigned_tape"], *plan.get("assigned_set_drives", ()), *plan.get("assigned_extra_drives", ())]
        rows.append(dict(role_name=name, character_id=key, snapshot_id=snapshot, name="四人增强", score=plan["score"],
            role_state=allocation_role_state_from_plan(plan), slot_id=None,
            axis_preview_source=plan.get("axis_preview_source"), baseline_preview_source=plan.get("baseline_preview_source"),
            team_enhancement_preview=preview, payload={"source_role_name": name,
                "assignment_scores": {item.uid: item.role_scores[name] for item in items},
                "tape_main_values": legacy_plan_tape_main_values(plan), **optimization_payload(plan)}))
    arguments = dict(database_path=deps.user_database_path, static_database_path=release.database_path,
        static_identity=(release.database_path, release.dataset_id), static_release=release, lock_snapshot=locks,
        rows=rows, checkpoint=lambda: None, progress=lambda _: None,
        axis_analysis_client=create_bundled_analysis_client(static_database_path=release.database_path))
    assert save_allocation_plans(**arguments) == 4
    with UserDataDao(deps.user_database_path) as dao:
        saved, = dao.list_team_enhancements(team.team_id)
        before = tuple(dao._db().iterdump())
        assert saved.run_id == preview.commit.run_id and len(saved.members) == 4
        assert saved.payload["baseline_build_sha256"] and saved.payload["provenance"]["static_sha256"] == release.sha256
    assert save_allocation_plans(**arguments) == 4
    with UserDataDao(deps.user_database_path) as dao:
        assert tuple(dao._db().iterdump()) == before
