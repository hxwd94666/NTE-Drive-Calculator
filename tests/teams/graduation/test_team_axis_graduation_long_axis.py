# 用隔离合成长轴验证正式EXE的同域数学缩放、预算及稀疏未知事件门禁，不替代实战轴验收。
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

from src.domain.teams.graduation.axis_graduation_configuration import configuration_seeds
from src.domain.teams.report_matching import ReportRange
from src.domain.teams.models import TeamDraft
from src.domain.equipment.properties.equipment_normalizer import equipment_quality_coefficient
from src.integrations.native_core.nte_core_battle import parse_battle_summary
from src.observability import OperationContext
from src.services.battle_report.persistence import (
    BattleReportPersistenceDependencies, BattleReportPersistenceService,
)
from src.services.teams.axis_graduation.service import TeamAxisGraduationService
from src.services.teams.axis_graduation.inputs import TeamAxisCurrentInputService
from src.services.teams.axis_graduation.rules import freeze_theory_equipment_rules
from src.services.allocation.inputs.context_builder import read_allocation_scoring_rules
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.allocation.axis_team_fixture import attach_fixed_builds, attach_native_team
from tests.battle_report.analysis.axis_evaluation_catalog_fixture import create_axis_catalog_case
from tests.teams.graduation.test_team_axis_graduation_configuration import target_for


NTE_TEST_TIER = "core"
REPETITIONS = 512


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf8")).hexdigest()


def frozen_facts(dao, record_id):
    graph = dao.load_battle_report_transfer_rows(record_id)
    return digest({"summary": graph["tables"]["battle_record"][0]["raw_summary_json"],
        "hits": graph["tables"]["battle_hit_evidence"],
        "intervals": graph["tables"]["battle_time_stop_interval"],
        "record": dao.load_finalized_battle_capture_record(record_id),
        "build": dao.load_battle_build_snapshot(record_id)})


def repeated_axis(inputs, record_id, *, unknown_last=False):
    """只在本例新建合成账号内建立新记录，旧冻结上下文作为明确合成输入复用。"""
    with UserDataDao(inputs.database_path) as dao:
        original = dao.load_battle_report_transfer_rows(record_id)
        before = frozen_facts(dao, record_id)
        record = dao.load_finalized_battle_capture_record(record_id)
        target = dao.load_battle_target_condition(record_id)
        template = [json.loads(row["raw_hit_json"]) for row in original["tables"]["battle_hit_evidence"]]
        summary = json.loads(original["tables"]["battle_record"][0]["raw_summary_json"])
        operation = "synthetic-graduation-long-axis"
        # 已结算上下文是压缩引用，不是可复用的采集中对象；仅从本记录自身的冻结事实还原。
        context = deepcopy(record["calc_capture_context"])
        canonical = {(row["uid_slot"], row["uid_serial"]): json.loads(row["raw_item_json"])
                     for row in original["tables"]["battle_equipment_snapshot"]}
        scope_only = context.get("equipment_storage", {}).get("scope_equipment", {})
        context["equipment"] = list(canonical.values())
        for entry in context.get("native_scope_builds", {}).values():
            resolved = []
            for ref in entry.pop("equipment_refs", []):
                if ref["storage"] == "battle_equipment_snapshot":
                    item = canonical[ref["uid_slot"], ref["uid_serial"]]
                else:
                    assert ref["storage"] == "scope_equipment"
                    item = scope_only[ref["sha256"]]
                    assert digest(item) == ref["sha256"]
                resolved.append(deepcopy(item))
            entry["equipment"] = resolved
        context.pop("equipment_storage", None)
        dao.begin_battle_axis_capture(capture_operation_id=operation,
            captured_at_utc="2026-08-20T00:00:00+00:00", account_generation=inputs.generation,
            frozen_build=context)
    assert len(template) == 3 and len({h["gameplay_effect_name"] for h in template}) == 1
    count = len(template) * REPETITIONS
    deps = BattleReportPersistenceDependencies(inputs.account_id, inputs.database_path,
        inputs.generation, inputs.static_database_path)
    persistence = BattleReportPersistenceService(dependencies=deps, context_is_current=lambda _: True,
        operation_context=OperationContext.create("battle_report"))
    # 合成事件延长到768秒；正式GE和冻结养成不变，不声称这种连击在游戏中可执行。
    for start in range(0, count, 128):
        hits = []
        for index in range(start, min(start + 128, count)):
            hit = deepcopy(template[index % len(template)])
            hit.update(sequence=str(index + 1), relative_time_seconds=(index + 1) * .5,
                       timestamp_unix=100 + (index + 1) * .5)
            if unknown_last and index == count - 1:
                hit["gameplay_effect_name"] = "GE_Synthetic_Unmodelled_LongAxis_LastHit"
            hits.append(hit)
        persistence.append_axis_page(capture_operation_id=operation, page={
            "contract_version": 4, "battle_record_id": "synthetic-long-axis", "generation": "1",
            "complete": True, "total_hits": str(count), "retained_hits": count, "rows": hits})
    duration = count * .5
    summary.update(total_hits=count, total_damage=summary["total_damage"] * REPETITIONS,
                   duration_seconds=duration)
    summary["total_dps"] = summary["total_damage"] / duration
    for actor in summary["characters"]:
        actor.update(hits=actor["hits"] * REPETITIONS, damage=actor["damage"] * REPETITIONS)
        actor["dps"] = actor["damage"] / duration
    record.pop("calc_capture_context")  # 新采集owner持有上下文，不能从调用方覆写其结算冻结。
    record.update(battle_record_id="synthetic-long-axis", axis_total_hits=str(count),
                  axis_first_sequence="1", axis_complete=True)
    outcome = persistence.finalize_summary(raw_summary_payload=summary, summary=parse_battle_summary(summary),
        capture_operation_id=operation, captured_at_utc="2026-08-20T00:00:00+00:00",
        finalized_at_utc="2026-08-20T00:12:48+00:00", raw_record_payload=record)
    assert outcome.battle_record_id is not None
    with UserDataDao(inputs.database_path) as dao:
        dao.save_battle_target_condition(outcome.battle_record_id, target)
        assert frozen_facts(dao, record_id) == before
    return ReportRange(outcome.battle_record_id), count


def prepare(directory, *, ids=(1003, 1072, 1004, 1008)):
    if os.environ.get("NTE_TEAM_AXIS_GRADUATION_LONG_AXIS_SMOKE") != "1":
        pytest.skip("explicit current-release synthetic long-axis EXE verification required")
    # 当前发行身份变化时另建明确的新版本样本，不改旧夹具哈希或混用旧静态/新规则。
    case = create_axis_catalog_case(directory, core_suit_id="Suit1", main_character_id=ids[0])
    attach_native_team(case, ids=ids)
    attach_fixed_builds(case, ids=ids)
    deps = case["dependencies"]
    root = Path(__file__).resolve().parents[3]
    rules = freeze_theory_equipment_rules(scoring_rules=read_allocation_scoring_rules(root / "config/stats.json"),
        quality_coefficients={q: equipment_quality_coefficient(q) for q in ("Gold", "Purple", "Blue")},
        static_database_path=deps.static_database_path)
    specs = {item.item_id: item for item in rules.items}
    with UserDataDao(deps.user_database_path) as dao, StaticGameDataDao(deps.static_database_path) as static:
        team = dao.save_team(TeamDraft("当前发行合成长轴", ids))
        snapshot = dao.current_inventory_snapshot_id()
        _, inventory = dao.export_inventory_snapshot(snapshot)
        # 旧轴夹具允许空副词条；本专项明确构造满强化、规则满值的合成实物，不以空值冒充已知。
        native = []
        for row in inventory:
            spec = specs[row["item_id"]]
            pool = rules.core_sub_stats if row["kind"] == "core" else rules.drive_sub_stats
            native.append({**row, "uid": {"slot": row["uid_slot"], "serial": row["uid_serial"]},
                "grid": row["grid_count"], "level": spec.max_level,
                "sub_stats": [dict(property_id=s.property_id, value=s.value, percent=s.is_percent)
                              for s in pool[:spec.sub_stat_count]]})
        snapshot = dao.import_inventory_snapshot({"complete": True, "item_count": len(native), "items": native}, source="nte_core")
        _, inventory = dao.export_inventory_snapshot(snapshot)
        by_uid = {f"nte-{row['kind']}-{row['uid_slot']}-{row['uid_serial']}": row for row in inventory}
        for cid in ids:
            growth = max(static.list_character_panel_growth(cid), key=lambda r: (r["level"], r["breakthrough_stage"]))
            dao.save_character_profile(character_id=cid, character_level=growth["level"],
                breakthrough_stage=growth["breakthrough_stage"], awakening_level=0, fork_id=None,
                fork_level=None, fork_breakthrough_stage=None, fork_refinement_level=None,
                skill_levels={row["skill_id"]: 1 for row in static.list_character_skills(cid)},
                selected_awaken_effect_ids=(), awakening_selection_initialized=True)
            anchors = [row for row in static.get_equipment_plan(cid)["cells"] if row["anchor_item_id"]]
            assignments = []
            for index, uid in enumerate(case["fixed_equipment_by_character"][cid]):
                row = by_uid[uid]
                assignments.append(dict(kind=row["kind"], uid_slot=row["uid_slot"], uid_serial=row["uid_serial"],
                    rotation=0, target_row=None if index == 0 else anchors[index-1]["row"],
                    target_column=None if index == 0 else anchors[index-1]["column"]))
            slot = dao.create_loadout_slot(cid, "当前发行图纸", slot_key="primary")
            dao.save_plan_to_slot(slot, name="合成冻结落位", assignments=assignments,
                source_snapshot_id=snapshot, score=1, status="ready", payload={"schema": "game-observed-loadout-v1"})
        dao.replace_application_setting_copy("world_bonus", {"yaodao_attack_add": 0, "quantao_crit_damage": 0})
    inputs = TeamAxisCurrentInputService(account_id=deps.account_id, generation=deps.generation,
        database_path=deps.user_database_path, static_database_path=deps.static_database_path,
        context_is_current=lambda: True)
    semantics = Path(__file__).resolve().parents[3] / "config/gameplay_effect_semantics.json"
    original = {"selector": {"battle_record_id": case["battle_record_id"], "semantics_path": str(semantics)}}
    service = TeamAxisGraduationService(current_inputs=inputs, semantics_path=semantics)
    configuration = service.load_configuration(team)
    target = target_for(configuration)
    options = dict(target=target, rules=configuration.rules,
        seeds=configuration_seeds(target, configuration.rules), max_millis=15000)
    return service, inputs, team, original, options


def test_many_event_long_axis_keeps_math_domain_budget_and_immutable_reference(tmp_path):
    service, inputs, team, original, options = prepare(tmp_path)
    short = service.solve_benchmark(service.prepare_benchmark(team,
        ReportRange(original["selector"]["battle_record_id"]), **options,
        request_id="long-axis-short-control", max_evaluations=2))
    axis, count = repeated_axis(inputs, original["selector"]["battle_record_id"])
    started = time.monotonic()
    long = service.solve_benchmark(service.prepare_benchmark(team, axis, **options,
        request_id="long-axis-default-budget", max_evaluations=256))
    wall = time.monotonic() - started
    current = long.response["current"]["axis_evaluation"]
    assert current["status"] == "complete" and current["total_events"] == current["quantified_events"] == count
    assert current["damage"] == pytest.approx(short.response["current"]["axis_evaluation"]["damage"] * REPETITIONS)
    assert long.reference is not None and short.reference is not None
    reference = long.reference.to_payload()["payload"]
    assert reference["reference_damage"] >= short.reference.to_payload()["payload"]["reference_damage"] * REPETITIONS - 1e-6
    search = reference["search"]
    assert 0 < search["evaluated_candidates"] <= 256 and search["evaluation_budget"] == 256
    assert search["optimality"] == "bounded_best" and search["unquantified_candidates"] == 0
    assert long.response["fixed_axis_graduation"]["status"] == "comparable"
    assert long.response["fixed_axis_graduation"]["axis_feasibility"] == "unverified"
    snapshot = long.axis_snapshot
    service.save_completed_result(long, expected_target_revision=0, expected_selection_revision=0)
    selected = service.selected_result(team)
    with UserDataDao(inputs.database_path) as dao:
        assert dao.delete_battle_record(axis.record_id)
    updated = service.evaluate_current(service.prepare_current(team, axis,
        target=options["target"], rules=options["rules"], reference=long.reference,
        axis_snapshot=snapshot, request_id="long-axis-snapshot-current"))
    assert updated.reference == long.reference and updated.axis_snapshot == snapshot
    assert updated.response["current"]["axis_evaluation"] == current
    assert service.selected_result(team) == selected
    result = {"fixture_kind": "synthetic_repeated_plain_attack_long_axis_v1", "events": count,
        "duration_seconds": count * .5, "repetitions": REPETITIONS, "search": search,
        "wall_seconds_including_input_and_component_guards": wall,
        "snapshot_payload_bytes": len(snapshot.to_payload()["payload_json"].encode("utf8")),
        "ratio_percent": long.response["fixed_axis_graduation"]["ratio_percent"],
        "frozen_axis_survives_source_deletion": True, "mathematical_model_mocked": False,
        "real_combat_representativeness_proven": False}
    (tmp_path / "synthetic-long-axis-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    print(json.dumps(result, ensure_ascii=False))


def test_one_unknown_event_is_not_hidden_by_many_quantified_events(tmp_path):
    service, inputs, team, original, options = prepare(tmp_path)
    axis, count = repeated_axis(inputs, original["selector"]["battle_record_id"], unknown_last=True)
    result = service.solve_benchmark(service.prepare_benchmark(team, axis, **options,
        request_id="long-axis-one-unknown", max_evaluations=2))
    current = result.response["current"]["axis_evaluation"]
    assert current["total_events"] == count and current["quantified_events"] == count - 1
    assert current["status"] != "complete" and current["damage"] is None
    assert result.response["fixed_axis_graduation"]["ratio_percent"] is None
    assert current["event_gaps"] or current["global_gap_codes"]
    with UserDataDao(inputs.database_path) as dao:
        assert dao.get_team_axis_selection(team.team_id) is None
