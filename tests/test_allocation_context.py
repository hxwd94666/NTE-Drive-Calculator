# 验证固定数据库版本的 AllocationContext 不会随后台数据漂移。
"""Regression tests for immutable, database-pinned allocation contexts."""

from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from src.services.allocation_context import (
    ALLOCATION_CONTEXT_SOLVER_VERSION,
    AllocationContextError,
    build_allocation_context,
)
from src.storage.sqlite.static_game_data_dao import STATIC_DATABASE_ENV, StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao


STATIC_DATABASE_PATH = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


def stat(property_id: str, value: float, *, percent: bool = False) -> dict:
    return {"property_id": property_id, "value": value, "percent": percent, "names": {}}


def item(serial: int, slot: int, *, kind: str) -> dict:
    module = kind == "module"
    return {
        "uid": {"serial": serial, "slot": slot},
        "kind": kind,
        "item_id": "cell3_style3_1_Orange" if module else "GetEfficiency_orange",
        "suit_id": "Suit6",
        "geometry": "ZhiJiao1" if module else "Core",
        "grid": 3 if module else None,
        "quality": "orange",
        "level": 20,
        "max_level": 20,
        "locked": False,
        "discarded": False,
        "equipped": False,
        "equipped_character_uid": None,
        "equipped_character_id": None,
        "equipped_placement": None,
        "names": {},
        "suit_names": {},
        "main_stats": [stat("AtkAdd", 63.0)] if module else [stat("DamageUpCosmosBase", 0.375, percent=True)],
        "sub_stats": [stat("CritDamageBase", 0.06, percent=True)],
    }


def snapshot(generation: int, items: list[dict]) -> dict:
    return {
        "method": "event.inventory.snapshot",
        "params": {
            "complete": True,
            "generation": generation,
            "sequence": generation,
            "observed_at_unix_ms": 1_800_000_000_000 + generation,
            "item_count": len(items),
            "items": items,
        },
    }


class AllocationContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.static_database_env = patch.dict(
            "os.environ", {STATIC_DATABASE_ENV: str(STATIC_DATABASE_PATH)}
        )
        self.static_database_env.start()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.user_dao = UserDataDao(
            Path(self.temp_dir.name) / "user.sqlite3", account_id="allocation-context"
        )
        self.static_dao = StaticGameDataDao()

    def tearDown(self) -> None:
        self.static_dao.close()
        self.user_dao.close()
        self.temp_dir.cleanup()
        self.static_database_env.stop()

    def _profile(self) -> dict:
        return self.user_dao.create_optimization_profile(
            "Pinned allocation",
            allocation_strategy="role_priority",
            characters=[
                {
                    "character_id": 1003,
                    "ordinal": 0,
                    "priority_group": 0,
                    "target_suit_id": "Suit6",
                    "suit_requirement_mode": "four_piece",
                    "core_main_property_id": "DamageUpCosmosBase",
                    "property_weights": {"CritDamageBase": 1.5, "AtkAdd": 0.8},
                    "substat_priorities": ["CritDamageBase", "AtkAdd"],
                    "property_limits": {"CritBase": {"minimum": 0.4, "maximum": 0.8}},
                },
                {
                    "character_id": 1004,
                    "ordinal": 1,
                    "priority_group": 2,
                    "suit_requirement_mode": "none",
                    "property_weights": {},
                    "substat_priorities": [],
                    "property_limits": {},
                },
            ],
        )

    def test_copies_all_reproducibility_inputs_into_frozen_official_id_context(self) -> None:
        snapshot_id = self.user_dao.import_inventory_snapshot(
            snapshot(1, [item(101, 11, kind="module"), item(202, 22, kind="core")])
        )
        profile = self._profile()

        context = build_allocation_context(
            self.user_dao,
            self.static_dao,
            snapshot_id=snapshot_id,
            profile_id=profile["profile_id"],
            profile_version=1,
            solver_version="weighted-solver-v1",
        )

        self.assertEqual("allocation-context", context.account_id)
        self.assertEqual(snapshot_id, context.snapshot.snapshot_id)
        self.assertEqual("weighted-solver-v1", context.solver_version)
        self.assertEqual(10, context.static_dataset.schema_version)
        self.assertTrue(context.static_dataset.dataset_id)
        self.assertEqual([1003, 1004], [role.character_id for role in context.roles])
        self.assertEqual([0, 2], [role.priority_group for role in context.roles])
        self.assertEqual("Suit6", context.roles[0].target_suit_id)
        shape = next(shape for shape in context.shapes if shape.shape_id == "EquipmentGeometry_ZhiJiao1")
        self.assertEqual(shape.cell_count, len(shape.cells))
        self.assertTrue(shape.cells)
        self.assertEqual(
            (("AtkAdd", 0.8), ("CritDamageBase", 1.5)),
            context.roles[0].property_weights,
        )
        drive = next(candidate for candidate in context.candidates if candidate.kind == "module")
        self.assertEqual((11, 101), drive.uid)
        self.assertEqual("EquipmentGeometry_ZhiJiao1", drive.geometry)
        self.assertEqual("AtkAdd", drive.main_stats[0].property_id)
        with self.assertRaises(FrozenInstanceError):
            context.profile_id = 99
        with self.assertRaises(FrozenInstanceError):
            context.candidates[0].level = 1

    def test_new_snapshot_or_preference_version_cannot_change_existing_context(self) -> None:
        first_snapshot_id = self.user_dao.import_inventory_snapshot(
            snapshot(1, [item(101, 11, kind="module")])
        )
        profile = self._profile()
        context = build_allocation_context(
            self.user_dao,
            self.static_dao,
            snapshot_id=first_snapshot_id,
            profile_id=profile["profile_id"],
            profile_version=1,
        )

        second_snapshot_id = self.user_dao.import_inventory_snapshot(
            snapshot(2, [item(303, 33, kind="module"), item(404, 44, kind="core")])
        )
        self.user_dao.create_optimization_profile_version(
            profile["profile_id"],
            allocation_strategy="global_optimal",
            characters=[
                {
                    "character_id": 1003,
                    "target_suit_id": "Suit6",
                    "suit_requirement_mode": "two_piece",
                    "property_weights": {"AtkAdd": 9.0},
                }
            ],
        )

        self.assertNotEqual(first_snapshot_id, second_snapshot_id)
        self.assertEqual(first_snapshot_id, context.snapshot.snapshot_id)
        self.assertEqual(1, context.profile_version)
        self.assertEqual("role_priority", context.allocation_strategy)
        self.assertEqual((("AtkAdd", 0.8), ("CritDamageBase", 1.5)), context.roles[0].property_weights)
        self.assertEqual([(11, 101)], [candidate.uid for candidate in context.candidates])

        self.assertTrue(self.user_dao.deactivate_optimization_profile(profile["profile_id"]))
        historical = build_allocation_context(
            self.user_dao,
            self.static_dao,
            snapshot_id=first_snapshot_id,
            profile_id=profile["profile_id"],
            profile_version=1,
        )
        self.assertEqual(context, historical)

    def test_pins_static_chassis_shapes_and_profile_weights_after_daos_close(self) -> None:
        snapshot_id = self.user_dao.import_inventory_snapshot(
            snapshot(1, [item(101, 11, kind="module"), item(202, 22, kind="core")])
        )
        profile = self._profile()
        official_plan = self.static_dao.get_equipment_plan(1003)
        context = build_allocation_context(
            self.user_dao,
            self.static_dao,
            snapshot_id=snapshot_id,
            profile_id=profile["profile_id"],
            profile_version=1,
        )

        self.static_dao.close()
        self.user_dao.close()
        constraints = context.roles[0].equipment
        self.assertEqual(
            [(cell["row"], cell["column"]) for cell in official_plan["cells"]],
            [(cell.row, cell.column) for cell in constraints.cells],
        )
        self.assertTrue(context.shapes[0].cells)
        self.assertTrue(context.suits[0].required_shape_ids)
        self.assertTrue(context.roles[0].effective_property_weights)
        self.assertTrue(context.attributes)

    def test_default_context_does_not_read_workshop_json(self) -> None:
        snapshot_id = self.user_dao.import_inventory_snapshot(
            snapshot(1, [item(101, 11, kind="module")])
        )
        profile = self._profile()
        with patch("src.services.allocation_context._workshop_roles") as workshop_roles, \
             patch("src.services.allocation_context._legacy_shape_labels") as shape_labels:
            context = build_allocation_context(
                self.user_dao, self.static_dao, snapshot_id=snapshot_id,
                profile_id=profile["profile_id"], profile_version=1,
            )
        workshop_roles.assert_not_called()
        shape_labels.assert_not_called()
        self.assertEqual("", context.roles[0].extra_shape_label)

    def test_protagonist_workshop_defaults_match_official_id_before_display_name(self) -> None:
        snapshot_id = self.user_dao.import_inventory_snapshot(snapshot(1, [item(101, 11, kind="module")]))
        profile = self.user_dao.create_optimization_profile(
            "protagonist workshop mapping",
            allocation_strategy="role_priority",
            characters=[{"character_id": 1046, "suit_requirement_mode": "none"}],
        )

        context = build_allocation_context(
            self.user_dao, self.static_dao, snapshot_id=snapshot_id,
            profile_id=profile["profile_id"], profile_version=1,
            workshop_roles_path=STATIC_DATABASE_PATH.parents[1] / "config" / "roles.json",
        )

        role = context.roles[0]
        self.assertEqual(1046, role.character_id)
        self.assertEqual("Type-3", role.extra_shape_label)
        self.assertTrue(role.effective_property_weights)

    def test_rejects_unknown_static_role_suit_property_and_template_ids(self) -> None:
        snapshot_id = self.user_dao.import_inventory_snapshot(
            snapshot(1, [item(101, 11, kind="module")])
        )
        unknown_role = self.user_dao.create_optimization_profile(
            "Unknown role",
            allocation_strategy="role_priority",
            characters=[{"character_id": 999999}],
        )
        with self.assertRaises(AllocationContextError):
            build_allocation_context(
                self.user_dao, self.static_dao, snapshot_id=snapshot_id,
                profile_id=unknown_role["profile_id"], profile_version=1,
            )

        unknown_suit = self.user_dao.create_optimization_profile(
            "Unknown suit",
            allocation_strategy="role_priority",
            characters=[{
                "character_id": 1003, "target_suit_id": "UnknownSuit",
                "suit_requirement_mode": "two_piece",
            }],
        )
        with self.assertRaises(AllocationContextError):
            build_allocation_context(
                self.user_dao, self.static_dao, snapshot_id=snapshot_id,
                profile_id=unknown_suit["profile_id"], profile_version=1,
            )

        unknown_property = self.user_dao.create_optimization_profile(
            "Unknown property",
            allocation_strategy="role_priority",
            characters=[{"character_id": 1003, "property_weights": {"UnknownProperty": 1.0}}],
        )
        with self.assertRaises(AllocationContextError):
            build_allocation_context(
                self.user_dao, self.static_dao, snapshot_id=snapshot_id,
                profile_id=unknown_property["profile_id"], profile_version=1,
            )


    def test_rejects_candidates_with_unknown_official_ids_or_template_kind(self) -> None:
        invalid_candidates = []
        unknown_item = item(101, 11, kind="module")
        unknown_item["item_id"] = "UnknownItem"
        invalid_candidates.append(unknown_item)
        unknown_suit = item(102, 12, kind="module")
        unknown_suit["suit_id"] = "UnknownSuit"
        invalid_candidates.append(unknown_suit)
        unknown_geometry = item(103, 13, kind="module")
        unknown_geometry["geometry"] = "UnknownGeometry"
        invalid_candidates.append(unknown_geometry)
        kind_mismatch = item(104, 14, kind="module")
        kind_mismatch["item_id"] = "GetEfficiency_orange"
        invalid_candidates.append(kind_mismatch)
        profile = self._profile()

        for generation, candidate in enumerate(invalid_candidates, start=1):
            with self.subTest(candidate=candidate["item_id"]):
                snapshot_id = self.user_dao.import_inventory_snapshot(
                    snapshot(generation, [candidate])
                )
                with self.assertRaises(AllocationContextError):
                    build_allocation_context(
                        self.user_dao, self.static_dao, snapshot_id=snapshot_id,
                        profile_id=profile["profile_id"], profile_version=1,
                    )

    def test_rejects_incomplete_or_inconsistent_static_blueprint(self) -> None:
        snapshot_id = self.user_dao.import_inventory_snapshot(
            snapshot(1, [item(101, 11, kind="module")])
        )
        profile = self._profile()
        original_plan = self.static_dao.get_equipment_plan
        malformed_plan = deepcopy(original_plan(1003))
        malformed_plan["cells"] = malformed_plan["cells"][:-1]
        with patch.object(
            self.static_dao, "get_equipment_plan",
            side_effect=lambda character_id: malformed_plan if character_id == 1003 else original_plan(character_id),
        ):
            with self.assertRaises(AllocationContextError):
                build_allocation_context(
                    self.user_dao, self.static_dao, snapshot_id=snapshot_id,
                    profile_id=profile["profile_id"], profile_version=1,
                )


    def test_rejects_suit_with_unknown_required_shape(self) -> None:
        snapshot_id = self.user_dao.import_inventory_snapshot(
            snapshot(1, [item(101, 11, kind="module")])
        )
        profile = self._profile()
        malformed_suits = deepcopy(self.static_dao.list_suits())
        next(suit for suit in malformed_suits if suit["suit_id"] == "Suit6")[
            "required_shape_ids"
        ].append("EquipmentGeometry_Unknown")
        with patch.object(self.static_dao, "list_suits", return_value=malformed_suits):
            with self.assertRaises(AllocationContextError):
                build_allocation_context(
                    self.user_dao, self.static_dao, snapshot_id=snapshot_id,
                    profile_id=profile["profile_id"], profile_version=1,
                )

    def test_rejects_context_when_atomic_snapshot_export_detects_count_mismatch(self) -> None:
        snapshot_id = self.user_dao.import_inventory_snapshot(
            snapshot(1, [item(101, 11, kind="module")])
        )
        profile = self._profile()
        connection = self.user_dao._db()
        connection.execute("DELETE FROM inventory_item WHERE snapshot_id = ?", (snapshot_id,))
        connection.commit()

        with self.assertRaises(AllocationContextError):
            build_allocation_context(
                self.user_dao, self.static_dao, snapshot_id=snapshot_id,
                profile_id=profile["profile_id"], profile_version=1,
            )

    def test_requires_explicit_existing_snapshot_and_profile_version(self) -> None:
        snapshot_id = self.user_dao.import_inventory_snapshot(
            snapshot(1, [item(101, 11, kind="module")])
        )
        profile = self._profile()
        with self.assertRaises(AllocationContextError):
            build_allocation_context(
                self.user_dao,
                self.static_dao,
                snapshot_id=snapshot_id + 999,
                profile_id=profile["profile_id"],
                profile_version=1,
            )
        with self.assertRaises(AllocationContextError):
            build_allocation_context(
                self.user_dao,
                self.static_dao,
                snapshot_id=snapshot_id,
                profile_id=profile["profile_id"],
                profile_version=99,
            )
        context = build_allocation_context(
            self.user_dao,
            self.static_dao,
            snapshot_id=snapshot_id,
            profile_id=profile["profile_id"],
            profile_version=1,
        )
        self.assertEqual(ALLOCATION_CONTEXT_SOLVER_VERSION, context.solver_version)


if __name__ == "__main__":
    unittest.main()
