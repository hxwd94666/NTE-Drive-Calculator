# 验证不可变上下文的本地图纸 Top-K 与 UID 唯一分配。
"""Regression tests for pure Context puzzle Top-K and UID-disjoint allocation."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from src.services.allocation_context import (
    AllocationCandidate, AllocationContext, AllocationRolePreference, BlueprintCell,
    InventorySnapshotReference, OfficialAttribute, OfficialShape, OfficialShapeCell,
    OfficialStat, PropertyLimit, RoleEquipmentConstraints, StaticDatasetReference,
    SuitConstraint, build_allocation_context,
)
from src.services.allocation_solver import solve_allocation_context
from src.services.allocation_legacy_adapter import _legacy_items, _shapes, run_legacy_allocation
from src.services.sqlite_allocation_inventory import SqliteAllocationInventory
from src.solver.orchestrator import NTEPipelineOrchestrator
from src.storage.sqlite.static_game_data_dao import STATIC_DATABASE_ENV, StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


STATIC_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"
SHAPES = ("ShapeA", "ShapeB", "ShapeC", "ShapeD")


def candidate(uid: tuple[int, int], *, kind: str, item_id: str, geometry: str | None,
              suit_id: str | None, stats: dict[str, float], main: bool = False) -> AllocationCandidate:
    values = tuple(OfficialStat(property_id, value, False) for property_id, value in stats.items())
    return AllocationCandidate(
        uid_slot=uid[0], uid_serial=uid[1], kind=kind, item_id=item_id, suit_id=suit_id,
        geometry=geometry, grid_count=None, quality="orange", level=20, max_level=20,
        locked=False, discarded=False, equipped=False, equipped_character_id=None,
        is_duplicate_drive=False, duplicate_group_id=None, duplicate_index=None,
        duplicate_count=None, main_stats=values if main else (), sub_stats=() if main else values,
    )


def fixture_shapes() -> tuple[OfficialShape, ...]:
    return tuple(
        OfficialShape(shape, 5, tuple(OfficialShapeCell(0, column) for column in range(5)))
        for shape in SHAPES
    )


def fixture_role(character_id: int, *, weight_id: str, target_suit: str = "Preferred",
                 limit: PropertyLimit | None = None) -> AllocationRolePreference:
    cells = tuple(BlueprintCell(row, column) for row in range(1, 5) for column in range(1, 6))
    equipment = RoleEquipmentConstraints(character_id=character_id, cells=cells)
    return AllocationRolePreference(
        character_id=character_id, ordinal=character_id, priority_group=0,
        target_suit_id=target_suit, suit_requirement_mode="four_piece",
        core_main_property_id="Main", property_weights=((weight_id, 1.0),),
        substat_priorities=(weight_id,), property_limits=(limit,) if limit else (), equipment=equipment,
        effective_property_weights=((weight_id, 1.0),),
        effective_main_property_weights=(("Main", 1.0), (weight_id, 1.0)),
        extra_shape_label="Type-3",
    )


def fixture_context(roles: tuple[AllocationRolePreference, ...], candidates: tuple[AllocationCandidate, ...],
                    *, strategy: str = "global_optimal") -> AllocationContext:
    suits = (SuitConstraint("Preferred", SHAPES), SuitConstraint("Other", SHAPES))
    return AllocationContext(
        account_id="solver-test", static_dataset=StaticDatasetReference(10, "fixture", 1, "now"),
        snapshot=InventorySnapshotReference(7, "nte_core", 1, 1, 1, "now", len(candidates), len(candidates)),
        profile_id=3, profile_version=2, allocation_strategy=strategy, solver_version="weighted-solver-v1",
        roles=roles, candidates=candidates, shapes=fixture_shapes(), suits=suits,
        attributes=(OfficialAttribute("Main", "Main"), OfficialAttribute("Score", "Score"),
                    OfficialAttribute("A", "A"), OfficialAttribute("B", "B"),
                    OfficialAttribute("Limit", "Limit"), OfficialAttribute("CritRate", "暴击率%")),
    )


def complete_role_candidates(role_id: int, weight_id: str, *, shape_a_score: float,
                             other_scores: float = 1.0) -> tuple[AllocationCandidate, ...]:
    values = [
        candidate((role_id, 1), kind="core", item_id=f"BetterCore{role_id}", geometry=None,
                  suit_id="Preferred", stats={"Main": 1}, main=True),
    ]
    for index, shape in enumerate(SHAPES, start=2):
        values.append(candidate((role_id, index), kind="module", item_id=f"Actual{role_id}{shape}", geometry=shape,
                                suit_id="Preferred", stats={weight_id: shape_a_score if shape == "ShapeA" else other_scores}))
    return tuple(values)


class AllocationSolverTests(unittest.TestCase):
    def test_context_projection_reuses_old_percent_units_and_type_three_label(self) -> None:
        role = fixture_role(1, weight_id="Score")
        percent_drive = AllocationCandidate(
            uid_slot=1, uid_serial=9, kind="module", item_id="PercentDrive", suit_id="Preferred",
            geometry="ShapeA", grid_count=None, quality="orange", level=20, max_level=20,
            locked=False, discarded=False, equipped=False, equipped_character_id=None,
            is_duplicate_drive=False, duplicate_group_id=None, duplicate_index=None, duplicate_count=None,
            main_stats=(), sub_stats=(OfficialStat("CritRate", 0.1, True),),
        )
        shapes = tuple(
            OfficialShape(shape, 5, tuple(OfficialShapeCell(0, column) for column in range(5)),
                          legacy_shape_id=shape, legacy_label="Type-3" if shape == "ShapeA" else "Type-2")
            for shape in SHAPES
        )
        context = replace(fixture_context((role,), (percent_drive,)), shapes=shapes)
        items, _ = _legacy_items(context)
        self.assertEqual(10.0, items[0].sub_stats["暴击率%"])
        self.assertEqual("Type-3", _shapes(context)["ShapeA"].label)

    def test_top_one_matches_existing_dispatcher_for_all_three_strategies(self) -> None:
        role = fixture_role(1, weight_id="Score")
        frozen_candidates = complete_role_candidates(1, "Score", shape_a_score=3.0)
        for strategy in ("role_priority", "drive_priority", "global_optimal"):
            with self.subTest(strategy=strategy):
                context = fixture_context((role,), frozen_candidates, strategy=strategy)
                old_run = run_legacy_allocation(context)
                old_plan = old_run.plans[old_run.role_key(1)]
                old_uids = {
                    old_run.candidates_by_legacy_uid[item.uid].uid
                    for item in (
                        [old_plan.get("assigned_tape")]
                        + list(old_plan.get("assigned_set_drives", ()) or ())
                        + list(old_plan.get("assigned_extra_drives", ()) or ())
                    )
                    if item is not None
                }
                result = solve_allocation_context(context, top_k=1)
                self.assertEqual(old_uids, result.unified.selected[0].used_uids)

    def test_generates_legal_dynamic_layout_not_official_recommendation(self) -> None:
        role = fixture_role(1, weight_id="Score")
        candidates = list(complete_role_candidates(1, "Score", shape_a_score=10.0))
        candidates[0] = candidate((1, 1), kind="core", item_id="BetterCore1", geometry=None,
                                  suit_id="Preferred", stats={"Main": 1.0, "Score": 10.0}, main=True)
        candidates.append(candidate((9, 1), kind="core", item_id="OfficialCore1", geometry=None,
                                    suit_id="Preferred", stats={"Main": 1.0, "Score": 1.0}, main=True))
        for index, shape in enumerate(SHAPES, start=2):
            candidates.append(candidate((9, index), kind="module", item_id=f"OfficialModule{shape}", geometry=shape,
                                        suit_id="Preferred", stats={"Score": 1.0}))
        frozen = fixture_context((role,), tuple(candidates))

        result = solve_allocation_context(frozen, top_k=2)

        selected = result.unified.selected[0]
        self.assertEqual(5, len(selected.assignments))
        self.assertEqual("BetterCore1", selected.assignments[0].item_id)
        self.assertIsNone(selected.assignments[0].official_recommendation_item_id)
        self.assertTrue(all(assignment.item_id.startswith("Actual") for assignment in selected.assignments[1:]))
        self.assertGreater(selected.score, 5.0)  # official preset would score 1 + 4 × 1
        self.assertIn("PuzzleCombinatorics + DFSPuzzleSolver", selected.satisfied_constraints)
        self.assertIn("官方配装预设未参与选优", result.unified.explanation[2])

    def test_returns_top_k_and_explains_weighted_stat_contribution(self) -> None:
        role = fixture_role(1, weight_id="Score")
        candidates = list(complete_role_candidates(1, "Score", shape_a_score=10.0))
        candidates.append(candidate((9, 9), kind="module", item_id="SecondBest", geometry="ShapeA",
                                    suit_id="Preferred", stats={"Score": 5.0}))
        frozen = fixture_context((role,), tuple(candidates))

        result = solve_allocation_context(frozen, top_k=2)

        options = result.role_top_k[0].options
        self.assertEqual(2, len(options))
        self.assertNotEqual(options[0].used_uids, options[1].used_uids)
        shape_a = next(item for item in options[0].assignments if item.geometry == "ShapeA")
        self.assertEqual("Score", shape_a.contributions[0].property_id)
        self.assertGreater(shape_a.score, 0.0)

    def test_global_selection_resolves_shared_uid_without_duplicate_equipment(self) -> None:
        first, second = fixture_role(1, weight_id="A"), fixture_role(2, weight_id="B")
        candidates = list(complete_role_candidates(1, "A", shape_a_score=10.0))
        candidates.extend(complete_role_candidates(2, "B", shape_a_score=1.0))
        shared = candidate((8, 8), kind="module", item_id="Shared", geometry="ShapeA", suit_id="Preferred", stats={"A": 10.0, "B": 9.0})
        candidates = [item for item in candidates if item.uid != (1, 2)] + [shared]
        frozen = fixture_context((first, second), tuple(candidates))

        result = solve_allocation_context(frozen, top_k=2)

        selected = {option.character_id: option for option in result.unified.selected}
        self.assertIn((8, 8), selected[1].used_uids)
        self.assertIn((2, 2), selected[2].used_uids)
        all_uids = [assignment.uid for option in result.unified.selected for assignment in option.assignments]
        self.assertEqual(len(all_uids), len(set(all_uids)))

    def test_property_limit_rejects_high_scoring_layout(self) -> None:
        role = fixture_role(1, weight_id="Score", limit=PropertyLimit("CritRate", None, 5.0))
        candidates = list(complete_role_candidates(1, "Score", shape_a_score=100.0))
        candidates[1] = candidate((1, 2), kind="module", item_id="Illegal", geometry="ShapeA", suit_id="Preferred", stats={"Score": 100.0, "CritRate": 9.0})
        candidates.append(candidate((9, 9), kind="module", item_id="Legal", geometry="ShapeA", suit_id="Preferred", stats={"Score": 5.0, "CritRate": 5.0}))
        frozen = fixture_context((role,), tuple(candidates), strategy="role_priority")

        result = solve_allocation_context(frozen)

        self.assertIn((9, 9), result.unified.selected[0].used_uids)

    def test_none_mode_keeps_a_main_filtered_core_without_module_set_target(self) -> None:
        base = fixture_role(1, weight_id="Score")
        role = replace(base, target_suit_id=None, suit_requirement_mode="none", extra_shape_label="")
        candidates = [candidate((1, 1), kind="core", item_id="OtherSuitCore", geometry=None,
                                suit_id="Other", stats={"Main": 1.0}, main=True)]
        candidates.extend(
            candidate((2, index), kind="module", item_id=f"Loose{index}", geometry="ShapeA",
                      suit_id="Other", stats={"Score": 1.0})
            for index in range(1, 5)
        )

        result = solve_allocation_context(fixture_context((role,), tuple(candidates)), top_k=1)

        selected = result.unified.selected[0]
        self.assertEqual((1, 1), selected.assignments[0].uid)
        self.assertEqual("Other", selected.assignments[0].suit_id)
        self.assertEqual(5, len(selected.assignments))

    def test_weighted_entry_can_keep_a_complete_official_drive_set_when_core_is_missing(self) -> None:
        role = fixture_role(1, weight_id="Score")
        drives_only = tuple(
            item for item in complete_role_candidates(1, "Score", shape_a_score=3.0)
            if item.kind == "module"
        )
        context = fixture_context((role,), drives_only, strategy="role_priority")

        result = solve_allocation_context(
            context, top_k=1, include_role_top_k=False, allow_missing_core=True,
        )

        self.assertEqual(1, len(result.unified.selected))
        selected = result.unified.selected[0]
        self.assertTrue(all(item.kind == "module" for item in selected.assignments))
        self.assertEqual(4, len(selected.assignments))

    def test_four_piece_preference_rejects_wrong_suit_even_when_higher_scoring(self) -> None:
        role = fixture_role(1, weight_id="Score")
        candidates = list(complete_role_candidates(1, "Score", shape_a_score=1.0))
        candidates.append(candidate((9, 9), kind="module", item_id="WrongSuit", geometry="ShapeA", suit_id="Other", stats={"Score": 99.0}))
        frozen = fixture_context((role,), tuple(candidates))

        result = solve_allocation_context(frozen)

        self.assertNotIn((9, 9), result.unified.selected[0].used_uids)

    def test_consumes_frozen_real_context_after_both_daos_are_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {STATIC_DATABASE_ENV: str(STATIC_DATABASE_PATH)}):
                user_dao = UserDataDao(Path(temp_dir) / "user.sqlite3", account_id="solver-real")
                static_dao = StaticGameDataDao()
                try:
                    plan = static_dao.get_equipment_plan(1003)
                    assert plan is not None
                    rows = [{
                        "uid": {"serial": 1, "slot": 1}, "kind": "core", "item_id": plan["core_item_id"],
                        "suit_id": "Suit11", "geometry": "Core", "grid": None, "quality": "orange", "level": 20,
                        "max_level": 20, "locked": False, "discarded": False, "equipped": False,
                        "equipped_character_uid": None, "equipped_character_id": None, "equipped_placement": None,
                        "names": {}, "suit_names": {},
                        "main_stats": [{"property_id": "DamageUpCosmosBase", "value": 0.1, "percent": True, "names": {}}], "sub_stats": [],
                    }]
                    for index, item_id in enumerate(plan["module_item_ids"], start=2):
                        template = static_dao.get_equipment_item(item_id)
                        assert template is not None
                        rows.append({
                            "uid": {"serial": index, "slot": index}, "kind": "module", "item_id": item_id,
                            "suit_id": "Suit11", "geometry": template["geometry_id"].removeprefix("EquipmentGeometry_"),
                            "grid": template["grid_count"], "quality": "orange", "level": 20, "max_level": 20, "locked": False,
                            "discarded": False, "equipped": False, "equipped_character_uid": None,
                            "equipped_character_id": None, "equipped_placement": None, "names": {}, "suit_names": {},
                            "main_stats": [], "sub_stats": [{"property_id": "AtkAdd", "value": float(index), "percent": False, "names": {}}],
                        })
                    snapshot_id = user_dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
                        "complete": True, "generation": 1, "sequence": 1, "observed_at_unix_ms": 1,
                        "item_count": len(rows), "items": rows,
                    }})
                    profile = user_dao.create_optimization_profile(
                        "solver frozen context", allocation_strategy="global_optimal", characters=[{
                            "character_id": 1003, "target_suit_id": "Suit11", "suit_requirement_mode": "four_piece",
                            "core_main_property_id": "DamageUpCosmosBase", "property_weights": {"AtkAdd": 1.0},
                        }],
                    )
                    frozen = build_allocation_context(user_dao, static_dao, snapshot_id=snapshot_id, profile_id=profile["profile_id"], profile_version=1)
                finally:
                    static_dao.close()
                    user_dao.close()
            result = solve_allocation_context(frozen, top_k=2)

        self.assertEqual(snapshot_id, result.snapshot_id)
        self.assertTrue(result.role_top_k[0].options)
        self.assertTrue(result.unified.selected)

    def test_context_adapter_matches_real_legacy_entry_for_all_strategies(self) -> None:
        """Compare Context against the public old in-memory entry, not itself."""

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {STATIC_DATABASE_ENV: str(STATIC_DATABASE_PATH)}):
                user_dao = UserDataDao(Path(temp_dir) / "user.sqlite3", account_id="solver-equivalence")
                static_dao = StaticGameDataDao()
                try:
                    plan = static_dao.get_equipment_plan(1003)
                    assert plan is not None
                    rows = [{
                        "uid": {"serial": 1, "slot": 1}, "kind": "core", "item_id": plan["core_item_id"],
                        "suit_id": "Suit11", "geometry": "Core", "grid": None, "quality": "orange", "level": 20,
                        "max_level": 20, "locked": False, "discarded": False, "equipped": False,
                        "equipped_character_uid": None, "equipped_character_id": None, "equipped_placement": None,
                        "names": {}, "suit_names": {},
                        "main_stats": [{"property_id": "DamageUpIncantationBase", "value": 0.1, "percent": True, "names": {}}],
                        "sub_stats": [],
                    }]
                    for index, item_id in enumerate(plan["module_item_ids"], start=2):
                        template = static_dao.get_equipment_item(item_id)
                        assert template is not None
                        rows.append({
                            "uid": {"serial": index, "slot": index}, "kind": "module", "item_id": item_id,
                            "suit_id": "Suit11", "geometry": template["geometry_id"].removeprefix("EquipmentGeometry_"),
                            "grid": template["grid_count"], "quality": "orange", "level": 20, "max_level": 20, "locked": False,
                            "discarded": False, "equipped": False, "equipped_character_uid": None,
                            "equipped_character_id": None, "equipped_placement": None, "names": {}, "suit_names": {},
                            "main_stats": [], "sub_stats": [{"property_id": "AtkAdd", "value": float(index), "percent": False, "names": {}}],
                        })
                    snapshot_id = user_dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
                        "complete": True, "generation": 1, "sequence": 1, "observed_at_unix_ms": 1,
                        "item_count": len(rows), "items": rows,
                    }})
                    profile = user_dao.create_optimization_profile(
                        "legacy entry equivalence", allocation_strategy="role_priority", characters=[{
                            "character_id": 1003, "target_suit_id": "Suit11", "suit_requirement_mode": "four_piece",
                            "core_main_property_id": "DamageUpIncantationBase", "property_weights": {"AtkAdd": 0.4},
                        }],
                    )
                    frozen = build_allocation_context(
                        user_dao, static_dao, snapshot_id=snapshot_id, profile_id=profile["profile_id"], profile_version=1,
                        workshop_roles_path=STATIC_DATABASE_PATH.parents[1] / "config" / "roles.json",
                    )
                    projection = SqliteAllocationInventory(user_dao, static_dao).build(snapshot_id)
                    role_name = static_dao.get_character(1003)["name_zh"]
                    suit_name = static_dao.get_suit("Suit11")["name_zh"].strip("「」")
                    main_name = next(
                        attribute.scoring_name for attribute in frozen.attributes
                        if attribute.property_id == "DamageUpIncantationBase"
                    )
                    for strategy in ("role_priority", "drive_priority", "global_optimal"):
                        with self.subTest(strategy=strategy):
                            old_plan = NTEPipelineOrchestrator("config").run_full_allocation(
                                list(projection.items), [role_name], {role_name: suit_name}, strategy,
                                tape_main_filters={role_name: [main_name]},
                                set_effect_modes={role_name: "four_piece"},
                            )[role_name]
                            new_run = run_legacy_allocation(replace(frozen, allocation_strategy=strategy))
                            new_plan = new_run.plans[new_run.role_key(1003)]
                            old_uids = {
                                tuple(map(int, item.uid.rsplit("-", 2)[1:]))
                                for item in [old_plan.get("assigned_tape"), *(old_plan.get("assigned_set_drives") or ()), *(old_plan.get("assigned_extra_drives") or ())]
                                if item is not None
                            }
                            new_uids = {
                                new_run.candidates_by_legacy_uid[item.uid].uid
                                for item in [new_plan.get("assigned_tape"), *(new_plan.get("assigned_set_drives") or ()), *(new_plan.get("assigned_extra_drives") or ())]
                                if item is not None
                            }
                            self.assertTrue(old_plan["valid"])
                            self.assertTrue(new_plan["valid"])
                            self.assertEqual(old_uids, new_uids)
                            self.assertEqual(float(old_plan["score"]), float(new_plan["score"]))
                finally:
                    static_dao.close()
                    user_dao.close()

    def test_context_uses_shared_multi_role_candidate_pool_limits(self) -> None:
        """Two Type-2 roles need more than the old fixed 15 drive candidates."""

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict("os.environ", {STATIC_DATABASE_ENV: str(STATIC_DATABASE_PATH)}):
                user_dao = UserDataDao(Path(temp_dir) / "user.sqlite3", account_id="solver-pool-limit")
                static_dao = StaticGameDataDao()
                try:
                    rows = []
                    for serial in range(1, 3):
                        rows.append({
                            "uid": {"serial": serial, "slot": serial}, "kind": "core", "item_id": "Nature_orange",
                            "suit_id": "Suit2", "geometry": "Core", "grid": None, "quality": "orange", "level": 20,
                            "max_level": 20, "locked": False, "discarded": False, "equipped": False,
                            "equipped_character_uid": None, "equipped_character_id": None, "equipped_placement": None,
                            "names": {}, "suit_names": {},
                            "main_stats": [{"property_id": "DamageUpNatureBase", "value": 0.1, "percent": True, "names": {}}],
                            "sub_stats": [],
                        })
                    for serial in range(3, 19):
                        rows.append({
                            "uid": {"serial": serial, "slot": serial}, "kind": "module", "item_id": "cell2_style1_1_Orange",
                            "suit_id": None, "geometry": "Hen2", "grid": 2, "quality": "orange", "level": 20,
                            "max_level": 20, "locked": False, "discarded": False, "equipped": False,
                            "equipped_character_uid": None, "equipped_character_id": None, "equipped_placement": None,
                            "names": {}, "suit_names": {}, "main_stats": [],
                            "sub_stats": [{"property_id": "AtkAdd", "value": 1.0, "percent": False, "names": {}}],
                        })
                    for serial in range(19, 23):
                        rows.append({
                            "uid": {"serial": serial, "slot": serial}, "kind": "module", "item_id": "cell2_style2_1_Orange",
                            "suit_id": None, "geometry": "Shu2", "grid": 2, "quality": "orange", "level": 20,
                            "max_level": 20, "locked": False, "discarded": False, "equipped": False,
                            "equipped_character_uid": None, "equipped_character_id": None, "equipped_placement": None,
                            "names": {}, "suit_names": {}, "main_stats": [],
                            "sub_stats": [{"property_id": "AtkAdd", "value": 1.0, "percent": False, "names": {}}],
                        })
                    snapshot_id = user_dao.import_inventory_snapshot({"method": "event.inventory.snapshot", "params": {
                        "complete": True, "generation": 1, "sequence": 1, "observed_at_unix_ms": 1,
                        "item_count": len(rows), "items": rows,
                    }})
                    profile = user_dao.create_optimization_profile(
                        "shared candidate pool", allocation_strategy="global_optimal", characters=[
                            {"character_id": 1010, "ordinal": 0, "priority_group": 0, "suit_requirement_mode": "none",
                             "core_main_property_id": "DamageUpNatureBase", "property_weights": {"AtkAdd": 1.0}},
                            {"character_id": 1055, "ordinal": 1, "priority_group": 0, "suit_requirement_mode": "none",
                             "core_main_property_id": "DamageUpNatureBase", "property_weights": {"AtkAdd": 1.0}},
                        ],
                    )
                    context = build_allocation_context(
                        user_dao, static_dao, snapshot_id=snapshot_id,
                        profile_id=profile["profile_id"], profile_version=1,
                    )
                finally:
                    static_dao.close()
                    user_dao.close()

            result = solve_allocation_context(context, top_k=1)

        self.assertEqual({1010, 1055}, {option.character_id for option in result.unified.selected})
        self.assertTrue(all(len(option.assignments) == 11 for option in result.unified.selected))
        used_uids = [assignment.uid for option in result.unified.selected for assignment in option.assignments]
        self.assertEqual(len(used_uids), len(set(used_uids)))


if __name__ == "__main__":
    unittest.main()
