# 验证规则深冻结、唯一公式、批量操作和正式取消路径，合成数据不依赖账号。
from concurrent.futures import CancelledError
from copy import deepcopy
from dataclasses import asdict, FrozenInstanceError, replace
import inspect
import json
from pathlib import Path
from shutil import copyfile
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.domain.allocation.scoring_rules import AllocationScoringRules
from src.features.weighted_allocation import runner
from src.optimizer.scoring import ScoringEngine
from src.services.allocation.context_builder import AllocationContextError, build_allocation_context, read_allocation_scoring_rules
from src.domain.allocation.inputs import OfficialStat, PropertyLimit
from src.services.allocation import legacy_adapter as adapter
from src.services.allocation_solver import solve_allocation_context
from src.services.characters.custom_character_service import create_custom_character
from src.services.equipment.weighted_loadout_comparison_service import freeze_weighted_loadout_comparisons, refresh_weighted_loadout_comparisons
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_allocation_solver import complete_role_candidates, fixture_context, fixture_role
from tests.allocation.test_context import item, snapshot

NTE_TEST_TIER = "core"


def payload(main=80):
    return dict(gold_base_values={"Score": 1}, tape_main_stats_pool=["Main"],
                tape_main_stat_values={"Main": main}, tape_stat_values={"Score": 1},
                main_only_keywords=["Main"], stat_alias_mapping={"AliasScore": "Score"},
                benefit_one={"Score": 2}, benefit_alias_mapping={"AliasScore": "Score"},
                weight_pool=["Score", "Main"])


def full_context(rules=None, *, limits=False):
    role = replace(fixture_role(1, weight_id="Score"), substat_priorities=(),
                   property_limits=(PropertyLimit("Score", 1000, None),) if limits else ())
    candidates = list(complete_role_candidates(1, "Score", shape_a_score=.4))
    candidates[0] = replace(candidates[0], main_stats=(OfficialStat("Main", 80, False),))
    candidates = [replace(value, grid_count=5 if value.kind == "module" else 15) for value in candidates]
    candidates.append(replace(candidates[1], uid_slot=9, uid_serial=9, item_id="spare"))
    return replace(fixture_context((role,), tuple(candidates)),
                   scoring_rules=rules or AllocationScoringRules.from_data(payload()))


def forbid_default_io():
    from contextlib import ExitStack
    stack = ExitStack()
    for target in ("_load_stats", "_load_roles_from_sqlite"):
        stack.enter_context(patch.object(ScoringEngine, target, side_effect=AssertionError("default IO after freeze")))
    return stack


def test_same_frozen_public_candidate_survives_external_rule_mutation(tmp_path):
    path = tmp_path / "stats.json"
    path.write_text(json.dumps(payload()), encoding="utf-8")
    rules = read_allocation_scoring_rules(path)
    context = full_context(rules)
    role, core = context.roles[0], context.candidates[0]
    before = asdict(context)
    with forbid_default_io(), patch.object(ScoringEngine, "calculate_cartridge_score", autospec=True,
                side_effect=ScoringEngine.calculate_cartridge_score) as formula:
        first = adapter.score_allocation_candidate(context, role, core)
        path.write_text(json.dumps(payload(160)), encoding="utf-8")
        second = adapter.score_allocation_candidate(context, role, core)
        rebuilt = replace(context, scoring_rules=read_allocation_scoring_rules(path))
        third = adapter.score_allocation_candidate(rebuilt, role, core)
    assert (first, second, third) == (50.0, 50.0, 25.0)
    assert formula.call_count == 3
    assert asdict(context) == before
    assert rules.source_sha256 != rebuilt.scoring_rules.source_sha256


def test_nine_fields_are_detached_and_materializations_do_not_share_mutation():
    original = payload()
    rules = AllocationScoringRules.from_data(original)
    frozen = asdict(rules)
    fingerprint = rules.rule_sha256
    for value in original.values():
        value.clear()
    catalog = rules.to_stat_catalog()
    catalog.gold_base_values["Score"] = 900
    catalog.tape_main_values["Main"] = 160
    catalog.weight_pool.clear()
    assert asdict(rules) == frozen
    assert rules.rule_sha256 == fingerprint
    assert rules.to_stat_catalog().tape_main_values == {"Main": 80.0}
    with pytest.raises(FrozenInstanceError):
        rules.weight_pool = ("Other",)
    with pytest.raises(ValueError):
        AllocationScoringRules.from_data({})


def test_missing_or_invalid_rule_source_is_build_error_not_default_fallback(tmp_path):
    with forbid_default_io(), pytest.raises(AllocationContextError):
        read_allocation_scoring_rules(tmp_path / "missing.json")
    path = tmp_path / "bad.json"
    path.write_text('{"gold_base_values":[]}', encoding="utf-8")
    with forbid_default_io(), pytest.raises(AllocationContextError):
        read_allocation_scoring_rules(path)
    with pytest.raises(TypeError):
        replace(full_context(), scoring_rules={})


@pytest.mark.parametrize("quality", ["orange", "purple", "blue"])
def test_bulk_single_and_real_solver_selected_scores_use_same_engine(quality):
    context = full_context()
    context = replace(context, candidates=tuple(replace(c, quality=quality) for c in context.candidates))
    before = asdict(context)
    with forbid_default_io():
        bulk = adapter.score_allocation_candidates(context, context.roles[0], context.candidates)
        singles = {c.uid: adapter.score_allocation_candidate(context, context.roles[0], c) for c in context.candidates}
        result = solve_allocation_context(context, top_k=1, include_role_top_k=False)
    assert bulk == singles
    assert result.unified.selected
    assert all(a.score == bulk[a.uid] for a in result.unified.selected[0].assignments)
    assert asdict(context) == before


def test_bulk_projects_only_requested_canonical_items_once():
    context = full_context()
    requested = (context.candidates[0], context.candidates[1], replace(context.candidates[0], main_stats=()))
    with forbid_default_io(), patch.object(adapter, "_project_candidate", wraps=adapter._project_candidate) as project, \
            patch.object(ScoringEngine, "__init__", autospec=True, side_effect=ScoringEngine.__init__) as constructor:
        scores = adapter.score_allocation_candidates(context, context.roles[0], requested)
    assert scores[context.candidates[0].uid] == 50
    assert project.call_count == 2
    assert constructor.call_count == 1
    with pytest.raises(ValueError):
        adapter.score_allocation_candidates(context, context.roles[0], (replace(context.candidates[0], uid_serial=999),))
    with pytest.raises(ValueError):
        adapter.score_allocation_candidates(context, replace(context.roles[0], effective_property_weights=()), requested)


def test_real_sqlite_builder_closes_daos_and_keeps_official_custom_role_inputs(tmp_path):
    from tests.allocation.static_release_helpers import ROOT as root
    static_path = tmp_path / "static.sqlite3"
    database = tmp_path / "user.sqlite3"
    copyfile(root / "data/game_static.sqlite3", static_path)
    rules = read_allocation_scoring_rules(root / "config/stats.json")
    with UserDataDao(database, account_id="scoring-freeze") as user, StaticGameDataDao(static_path) as static:
        custom = create_custom_character(database, "合成角色")
        custom_id = int(custom["character_id"])
        user.save_custom_character_shape_bonus(custom_id, shape_label="Type-4", property_values={"CritBase": 8})
        source = user.import_inventory_snapshot(snapshot(1, [item(101, 11, kind="module"), item(202, 22, kind="core")]))
        profile = user.create_optimization_profile("fixture", allocation_strategy="role_priority", characters=[
            dict(character_id=1003, ordinal=0, suit_requirement_mode="none", property_weights={"CritDamageBase": 1.5}),
            dict(character_id=custom_id, ordinal=1, suit_requirement_mode="none", property_weights={"CritDamageBase": .5}),
        ])
        context = build_allocation_context(user, static, scoring_rules=rules, snapshot_id=source,
                    profile_id=profile["profile_id"], profile_version=1, shared_database_path=tmp_path / "shared.sqlite3")
    before = asdict(context)
    with UserDataDao(database) as user:
        user.save_custom_character_shape_bonus(custom_id, shape_label="Type-2", property_values={"CritBase": 30})
        user.create_optimization_profile("later", allocation_strategy="role_priority", characters=[
            dict(character_id=1003, property_weights={"CritDamageBase": 99}, suit_requirement_mode="none")])
    with forbid_default_io(), patch.object(UserDataDao, "__init__", side_effect=AssertionError("late role DAO")), \
            patch.object(StaticGameDataDao, "__init__", side_effect=AssertionError("late static DAO")):
        scores = [adapter.score_allocation_candidates(context, role, context.candidates) for role in context.roles]
    assert all(scores)
    assert context.roles[1].extra_shape_label == "Type-4"
    assert asdict(context) == before


def test_comparison_both_sides_use_context_hash_and_keep_saved_score_facts(tmp_path):
    path = tmp_path / "stats.json"
    path.write_text(json.dumps(payload()), encoding="utf-8")
    context = full_context(read_allocation_scoring_rules(path))
    core = context.candidates[0]
    history = dict(plan_id=3, source_snapshot_id=7, assignments=[dict(kind="core", uid_slot=core.uid_slot,
        uid_serial=core.uid_serial)], payload={"assignment_scores": {"old": 123.45}}, score=123.45)
    frozen_history = deepcopy(history)
    native_item = dict(kind="core", quality="orange", grid_count=15, main_stats=[dict(property_id="Main", value=80)], sub_stats=[])
    user = SimpleNamespace(list_loadout_slots=lambda _: [dict(slot_id=2, slot_name="主力", slot_key="main", current_plan=history)],
                           list_inventory_items=lambda *_args, **_kwargs: [dict(native_item, uid_slot=core.uid_slot, uid_serial=core.uid_serial)])
    static = SimpleNamespace(list_equipment_attributes=lambda: [dict(attribute_id=a.property_id, display_name_zh=a.scoring_name) for a in context.attributes],
                             list_suits=lambda: [], list_shapes=lambda: [])
    with forbid_default_io():
        baselines = freeze_weighted_loadout_comparisons(user, static, context, ())
        path.write_text(json.dumps(payload(160)), encoding="utf-8")
        result = solve_allocation_context(context, top_k=1, include_role_top_k=False)
        updated = refresh_weighted_loadout_comparisons(baselines, context, result.unified.selected)
    row = updated[1][0]
    assert row.scorer.metadata()["rule_sha256"] == context.scoring_rule_sha256
    assert row.old_items[0]["score"] == 50
    assert row.scorer.score_inventory_item(native_item) == 50
    assert history == frozen_history
    assert all(not item.get("comparison_score_unavailable") for item in row.diff.get("added", ()))


@pytest.mark.parametrize("stage", ["before", "projection", "blueprint", "inventory", "top_k", "kernel_rerun", "dispatcher"])
def test_public_solve_cancels_at_real_stage_without_partial_success(stage):
    context = full_context(limits=stage == "kernel_rerun")
    if stage == "dispatcher":
        second = replace(context.roles[0], character_id=2, ordinal=2,
                         equipment=replace(context.roles[0].equipment, character_id=2))
        extras = tuple(replace(c, uid_slot=2, uid_serial=c.uid_serial + 10) for c in context.candidates)
        context = replace(context, roles=(context.roles[0], second), candidates=context.candidates + extras)
    before = asdict(context)
    checks, reached = 0, []
    def cancel():
        nonlocal checks
        checks += 1
        frame = inspect.currentframe().f_back
        frames = []
        while frame is not None:
            frames.append((Path(frame.f_code.co_filename).name, frame.f_code.co_name, frame.f_locals))
            frame = frame.f_back
        if stage == "before": match = checks == 1
        elif stage == "projection": match = any(name == "legacy_items" for _, name, _ in frames)
        elif stage == "blueprint": match = any(name == "solve_blueprints" for _, name, _ in frames)
        elif stage == "inventory": match = any(file == "scoring.py" and name == "checked" for file, name, _ in frames)
        elif stage == "top_k": match = any(name == "_top_k" and values.get("options") and "uid" in values for _, name, values in frames)
        elif stage == "kernel_rerun":
            # 统一回退wrapper之后，真实属性约束重求仍由_pinned持有seen；必须已进入重求内核。
            match = (any(file == "allocation_kernel.py" and name == "_execute_pinned" and values.get("seen")
                         for file, name, values in frames)
                     and any(file == "allocation_kernel.py" and name == "_execute_once" for file, name, _ in frames))
        else: match = any(file == "drive_candidate_ranker.py" and name == "_check_cancelled" for file, name, _ in frames)
        if match: reached.append((stage, checks))
        return bool(match)
    with forbid_default_io(), pytest.raises(CancelledError):
        solve_allocation_context(context, top_k=2, include_role_top_k=stage == "top_k", cancel_check=cancel)
    assert reached and len(reached) == 1
    assert asdict(context) == before
    print(json.dumps(dict(cancel_stage=stage, checks=checks, reached=reached, partial_result=False, context_unchanged=True)))


def test_inventory_cancel_bounds_formula_work_and_owner_exception_is_unmodified():
    context = full_context()
    calls = []
    original = ScoringEngine.calculate_drive_score
    def drive(self, *args, **kwargs):
        calls.append(args[0].uid)
        return original(self, *args, **kwargs)
    with forbid_default_io(), patch.object(ScoringEngine, "calculate_drive_score", drive), pytest.raises(CancelledError):
        solve_allocation_context(context, top_k=1, include_role_top_k=False, cancel_check=lambda: len(calls) == 2)
    assert len(calls) == 2
    print(json.dumps(dict(cancel_stage="inventory-after-work", drive_formula_calls=len(calls), partial_result=False)))
    sentinel = CancelledError("owner token revoked")
    def owner(): raise sentinel
    with pytest.raises(CancelledError) as error:
        solve_allocation_context(context, cancel_check=owner)
    assert error.value is sentinel


def test_restore_overlay_preserves_historical_scores_while_new_scoring_uses_current_rules():
    context = full_context()
    result = solve_allocation_context(context, top_k=1, include_role_top_k=False)
    option = result.unified.selected[0]
    saved = tuple(runner.WeightedSavedAssignmentSignature(a.uid, a.kind,
        min((r for r, _ in a.board_cells), default=None), min((c for _, c in a.board_cells), default=None),
        float(index + 1), item_id=a.item_id, suit_id=a.suit_id, geometry=a.geometry, grid_count=a.grid_count)
        for index, a in enumerate(option.assignments))
    signature = runner.WeightedSavedPlanSignature(1, 1, 77, option.used_uids, saved)
    restored = runner._restore_saved_option(context, signature)
    assert restored.score == 77
    assert tuple(a.score for a in restored.assignments) == tuple(a.score for a in saved)
    assert adapter.score_allocation_candidate(context, context.roles[0], context.candidates[0]) == 50
    assert runner._restore_saved_option(context, signature) == restored


def test_replacement_recomputes_score_from_frozen_context_not_presentation(tmp_path):
    context = full_context()
    result = solve_allocation_context(context, top_k=1, include_role_top_k=False)
    preview = runner.WeightedAllocationPreview(result, context.static_dataset, context.account_id,
        tmp_path / "user.sqlite3", context, equipment_only=True)
    assignment = next(a for a in result.unified.selected[0].assignments if a.geometry == "ShapeA")
    with forbid_default_io(), patch.object(adapter, "_project_candidate", wraps=adapter._project_candidate) as project:
        updated = runner.replace_weighted_allocation_assignment(preview, old_uid=assignment.uid, new_uid=(9, 9))
        assert project.call_count == 1
    replaced = next(a for a in updated.result.unified.selected[0].assignments if a.uid == (9, 9))
    assert replaced.score == adapter.score_allocation_candidate(context, context.roles[0], context.candidates[-1])
    assert updated.context is context and preview.result is result


def test_public_cancel_reaches_deferred_reservation_recovery():
    from src.domain.allocation.inputs import OfficialAttribute
    context = full_context()
    high = replace(context.roles[0], effective_property_weights=(("High", 1.), ("Other", .1)), priority_group=0)
    later = tuple(replace(high, character_id=i, ordinal=i, priority_group=1,
                    equipment=replace(high.equipment, character_id=i), effective_property_weights=(("Later", 1.), ("Other", .1))) for i in (2, 3))
    drives = []
    for index, original in enumerate(context.candidates[1:5]):
        for kind, high_value, low_value in ((0, .1, 1.), (1, .1, 1.), (2, .01, .01), (3, .01, .01)):
            drives.append(replace(original, uid_slot=9, uid_serial=index * 10 + kind + 1,
                sub_stats=((OfficialStat("High", high_value, False), OfficialStat("Later", low_value, False))
                           if kind < 2 else (OfficialStat("Other", low_value, False),))))
    cores = tuple(replace(context.candidates[0], uid_slot=i) for i in (1, 2, 3))
    rules = payload()
    rules["gold_base_values"] = {"High": 1, "Later": 1, "Other": 1}
    context = replace(context, roles=(high, *later), candidates=(*cores, *drives),
        attributes=(*context.attributes, OfficialAttribute("High", "High"), OfficialAttribute("Later", "Later"), OfficialAttribute("Other", "Other")),
        scoring_rules=AllocationScoringRules.from_data(rules))
    before = asdict(context)
    reached = []
    checks = 0
    def cancel():
        nonlocal checks
        checks += 1
        frame = inspect.currentframe().f_back
        while frame is not None:
            if Path(frame.f_code.co_filename).name == "role_priority_execution.py" and frame.f_code.co_name == "_check_cancelled":
                reached.append(frame.f_code.co_name)
                return True
            frame = frame.f_back
        return False
    with forbid_default_io(), pytest.raises(CancelledError):
        solve_allocation_context(context, top_k=1, include_role_top_k=False, cancel_check=cancel)
    assert reached == ["_check_cancelled"] and asdict(context) == before
    print(json.dumps(dict(cancel_stage="deferred-reservation-recovery", checks=checks, reached=reached, partial_result=False, context_unchanged=True)))


@pytest.mark.parametrize("quality", ["orange", "purple", "blue"])
@pytest.mark.parametrize("zero_blacklist", [False, True])
def test_alias_percent_blacklist_and_main_weights_match_the_direct_sole_engine(quality, zero_blacklist):
    from src.domain.stat_catalog import StatCatalog
    from src.models.equipment import Drive
    from src.domain.allocation.inputs import OfficialAttribute
    raw = payload()
    raw["gold_base_values"]["Percent%"] = 1
    raw["stat_alias_mapping"]["AliasPercent%"] = "Percent%"
    context = full_context(AllocationScoringRules.from_data(raw))
    role = replace(context.roles[0], effective_property_weights=(("Score", .5), ("PercentStat", 1.)),
        effective_main_property_weights=(("Main", .6),), substat_blacklist=("Score",), blacklist_zero_weight=zero_blacklist)
    context = replace(context, roles=(role,), attributes=(OfficialAttribute("Main", "Main"),
        OfficialAttribute("Score", "AliasScore"), OfficialAttribute("PercentStat", "AliasPercent%")),
        candidates=tuple(replace(c, quality=quality, sub_stats=(OfficialStat("Score", .4, False),
            OfficialStat("PercentStat", .03, True))) for c in context.candidates))
    before = asdict(context)
    with forbid_default_io():
        # 参考端直接调用原StatCatalog+公式，而非再次调用批量适配入口。
        engine = ScoringEngine(stat_catalog=StatCatalog.from_data(deepcopy(raw)), roles_db={})
        weights = {"AliasScore": .5, "AliasPercent%": 1.}
        zeros = ("AliasScore",) if zero_blacklist else ()
        maximum = engine.max_theoretical_weight(weights, zero_weight_stats=zeros)
        items, by_uid = adapter.legacy_items(context)
        expected = {by_uid[i.uid].uid: (engine.calculate_drive_score(i, weights, maximum, zero_weight_stats=zeros)
                    if isinstance(i, Drive) else engine.calculate_cartridge_score(i, weights, maximum, {"Main": .6})) for i in items}
        actual = adapter.score_allocation_candidates(context, role, context.candidates)
        assert actual == expected == {c.uid: adapter.score_allocation_candidate(context, role, c) for c in context.candidates}
        assert all(i.sub_stats["AliasPercent%"] == 3. for i in items)
        if zero_blacklist:
            selected = solve_allocation_context(context, top_k=1, include_role_top_k=False).unified.selected
            assert selected and all(a.score == actual[a.uid] for a in selected[0].assignments)
    assert asdict(context) == before


def test_real_borrowed_replacement_preserves_source_others_and_virtual_zero():
    context = full_context()
    option = solve_allocation_context(context, top_k=1, include_role_top_k=False).unified.selected[0]
    role2 = replace(context.roles[0], character_id=2, ordinal=2, equipment=replace(context.roles[0].equipment, character_id=2))
    extras = tuple(replace(c, uid_slot=2, uid_serial=c.uid_serial + 20) for c in context.candidates)
    context = replace(context, roles=(*context.roles, role2), candidates=(*context.candidates, *extras))
    extra_by_old = {c.uid: e for c, e in zip(context.candidates[:len(extras)], extras)}
    source_assignments = tuple(replace(a, uid=extra_by_old[a.uid].uid, score=float(index + 1))
                               for index, a in enumerate(option.assignments))
    source = replace(option, character_id=2, score=sum(a.score for a in source_assignments), assignments=source_assignments)
    target_core = next(a for a in option.assignments if a.kind == "core")
    source_core = next(a for a in source.assignments if a.kind == "core")
    result = solve_allocation_context(full_context(), top_k=1, include_role_top_k=False)
    result = replace(result, unified=replace(result.unified, selected=(option, source), total_score=option.score + source.score))
    preview = runner.WeightedAllocationPreview(result, context.static_dataset, context.account_id, Path("fixture.sqlite3"), context, equipment_only=True)
    before = asdict(preview)
    with forbid_default_io():
        updated = runner.replace_weighted_allocation_assignment(preview, old_uid=target_core.uid, new_uid=source_core.uid)
        expected = adapter.score_allocation_candidate(context, context.roles[0], extra_by_old[target_core.uid])
    target, previous_owner = updated.result.unified.selected
    assert next(a.score for a in target.assignments if a.uid == source_core.uid) == expected
    placeholder = next(a for a in previous_owner.assignments if a.kind == "core")
    assert placeholder.virtual and placeholder.score == 0
    assert tuple((a.uid, a.score) for a in previous_owner.assignments if a.kind == "module") == tuple((a.uid, a.score) for a in source.assignments if a.kind == "module")
    assert previous_owner.score == source.score - source_core.score
    assert asdict(preview) == before
