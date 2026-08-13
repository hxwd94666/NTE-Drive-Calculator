# 验证第四阶段新增公共装备规范化、投影、评分、替换和虚拟装备行为。
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from src.domain.equipment_normalizer import (
    calculate_drive_main_stats,
    normalize_equipment_item,
    normalize_inventory,
)
from src.services.equipment_level_projection_service import (
    project_equipment_items_to_max_level,
)
from src.services.official_role_equipment_scoring_service import (
    score_official_role_equipment,
)
from src.services.official_role_replacement_service import (
    _same_inventory_item,
    replacement_candidates_for_official_role,
    save_official_role_replacement,
)
from src.services.virtual_equipment_service import (
    grid_count_from_geometry,
    is_virtual_equipment_assignment,
    make_virtual_equipment_assignment,
    virtual_equipment_inventory_item,
    virtual_equipment_item_id,
    virtual_equipment_uid,
)
from src.integrations.vision.equipment_classifier import (
    classify_item,
    looks_like_drive_identity,
    looks_like_tape_identity,
)
from src.integrations.vision.uid import make_unique_uid


class _ProjectionDao:
    def list_equipment_items(self):
        return [
            {
                "item_id": "core-1",
                "kind": "core",
                "quality": "ORANGE",
                "max_level": 20,
            },
            {
                "item_id": "module-1",
                "kind": "module",
                "quality": "PURPLE",
                "grid_count": 3,
                "max_level": 15,
            },
        ]

    def evaluate_equipment_base_attribute_curve(self, curve_id, level):
        if curve_id and curve_id.startswith("AtkAdd"):
            return float(level * 2)
        return None


class EquipmentNormalizerTests(unittest.TestCase):
    def test_drive_tape_unknown_and_inventory_normalization(self):
        self.assertEqual(
            {"攻击力": 50.4, "生命值": 672.0},
            calculate_drive_main_stats(3, "Purple"),
        )
        self.assertEqual(
            {"攻击力": 12.6, "生命值": 168.0},
            calculate_drive_main_stats(1, "Blue"),
        )
        drive = normalize_equipment_item(
            {
                "item_type": "drive",
                "area": 2,
                "quality": "Gold",
                "main_stats": {"broken": 1},
            }
        )
        self.assertEqual("DRIVE_2", drive["shape_id"])
        self.assertEqual(2, len(drive["main_stats"]))
        self.assertEqual({}, drive["sub_stats"])
        valid_main = {"攻击力": 42.0, "生命值": 560.0}
        normalized_main = normalize_equipment_item(
            {
                "item_type": "drive",
                "area": 2,
                "main_stats": valid_main,
            }
        )["main_stats"]
        self.assertEqual(valid_main, normalized_main)
        self.assertIsNot(valid_main, normalized_main)

        tape_from_mapping = normalize_equipment_item(
            {"item_type": "tape", "main_stats": {"暴击率%": 1}}
        )
        tape_unknown = normalize_equipment_item(
            {"item_type": "tape", "main_stats": "  "}
        )
        self.assertEqual("暴击率%", tape_from_mapping["main_stats"])
        self.assertEqual("未知主词条", tape_unknown["main_stats"])
        unknown = {"item_type": "other"}
        normalized = normalize_inventory([unknown])
        self.assertEqual("other", normalized[0]["item_type"])
        self.assertIsNot(unknown, normalized[0])


class VisionClassificationContractTests(unittest.TestCase):
    @staticmethod
    def _processor(shape_id: str, confidence: float, text: str):
        return SimpleNamespace(
            DRIVE_TYPE_CONFIDENCE=0.86,
            shape_recognizer=SimpleNamespace(
                recognize=lambda _crop: {
                    "shape_id": shape_id,
                    "confidence": confidence,
                }
            ),
            ocr_engine=SimpleNamespace(extract_text=lambda _crop: [text]),
            parser=SimpleNamespace(REAL_SETS_WHITE_LIST=["森林萤火之心"]),
        )

    def test_temporary_uid_and_identity_contracts(self):
        self.assertEqual("uid", make_unique_uid("uid", set()))
        self.assertEqual("uid_4", make_unique_uid("uid", {"uid", "uid_2", "uid_3"}))
        self.assertFalse(looks_like_drive_identity("卡带"))
        self.assertTrue(looks_like_drive_identity("驱动块"))
        self.assertTrue(looks_like_drive_identity("驱 动"))
        self.assertFalse(looks_like_drive_identity("装备"))

        parser = SimpleNamespace(REAL_SETS_WHITE_LIST=["森林萤火之心"])
        self.assertFalse(looks_like_tape_identity(parser, ""))
        self.assertTrue(looks_like_tape_identity(parser, "卡 带"))
        self.assertTrue(looks_like_tape_identity(parser, "森林萤火之心"))
        self.assertTrue(looks_like_tape_identity(parser, "森林萤火"))
        self.assertFalse(looks_like_tape_identity(parser, "驱动块"))

    def test_classification_fallback_routes(self):
        image = np.zeros((20, 20, 3), dtype=np.uint8)
        empty_regions = {
            "drive_shape_icon": (0, 0, 0, 0),
            "identity_check": (0, 0, 10, 10),
        }
        self.assertEqual(
            "tape",
            classify_item(
                self._processor("Unknown", -1.0, ""),
                image,
                [("empty", empty_regions)],
            )[0],
        )

        regions = {
            "drive_shape_icon": (0, 0, 10, 10),
            "identity_check": (10, 0, 20, 10),
        }
        self.assertEqual(
            "drive",
            classify_item(
                self._processor("Unknown", 0.2, "驱动块"),
                image,
                [("text", regions)],
            )[0],
        )
        self.assertEqual(
            "drive",
            classify_item(
                self._processor("H_2", 0.9, ""),
                image,
                [("shape", regions)],
            )[0],
        )
        self.assertEqual(
            "tape",
            classify_item(
                self._processor("Unknown", 0.2, "未识别"),
                image,
                [("fallback", regions)],
            )[0],
        )


class EquipmentProjectionTests(unittest.TestCase):
    def test_projection_uses_official_curves_and_preserves_substats(self):
        source = [
            {
                "item_id": "core-1",
                "main_stats": [{"property_id": "AtkAdd", "value": 1}],
                "sub_stats": [{"property_id": "CritBase", "value": 2}],
            },
            {
                "item_id": "module-1",
                "main_stats": [
                    {"property_id": "HPMaxAdd", "value": 3},
                    {"property_id": "", "value": 4},
                ],
            },
            {
                "item_id": "unknown",
                "kind": "invalid",
                "quality": "GREEN",
                "level": 7,
                "main_stats": [{"property_id": "AtkAdd", "value": 5}],
            },
        ]
        projected = project_equipment_items_to_max_level(
            source,
            _ProjectionDao(),
        )
        self.assertEqual(40.0, projected[0]["main_stats"][0]["value"])
        self.assertEqual(20, projected[0]["level"])
        self.assertEqual("orange", projected[0]["quality"])
        self.assertEqual(3, projected[1]["grid_count"])
        self.assertEqual(3, projected[1]["main_stats"][0]["value"])
        self.assertEqual(7, projected[2]["level"])
        self.assertEqual(5, projected[2]["main_stats"][0]["value"])
        self.assertIsNot(source[0], projected[0])
        self.assertIsNot(source[0]["sub_stats"][0], projected[0]["sub_stats"][0])


class OfficialEquipmentScoringTests(unittest.TestCase):
    def setUp(self):
        self.detail = {
            "attributes": {
                "CritBase": {"filter_name_zh": "暴击率%"},
                "AtkAdd": {"display_name_zh": "攻击力"},
            },
            "property_weights": {"CritBase": 2.0},
            "main_property_weights": {"AtkAdd": 3.0},
        }

    def test_none_core_and_module_routes(self):
        self.assertEqual(
            0.0,
            score_official_role_equipment(
                None,
                detail=self.detail,
                item={},
                shape_areas={},
            ),
        )
        with patch(
            "src.services.official_role_equipment_scoring_service.score_tape_stats",
            return_value=12.5,
        ) as tape_score:
            result = score_official_role_equipment(
                object(),
                detail=self.detail,
                item={
                    "kind": "core",
                    "quality": "ORANGE",
                    "main_stats": [{"property_id": "AtkAdd"}],
                    "sub_stats": [{"property_id": "CritBase"}],
                },
                shape_areas={},
            )
        self.assertEqual(12.5, result)
        self.assertEqual("攻击力", tape_score.call_args.kwargs["main_stat_name"])
        self.assertEqual("Gold", tape_score.call_args.kwargs["quality"])

        with (
            patch(
                "src.services.official_role_equipment_scoring_service.legacy_shape_id",
                side_effect=Exception,
            ),
            patch(
                "src.services.official_role_equipment_scoring_service.score_drive_stats",
                return_value=8.0,
            ) as drive_score,
        ):
            with self.assertRaises(Exception):
                score_official_role_equipment(
                    object(),
                    detail=self.detail,
                    item={"kind": "module", "geometry": "shape", "grid_count": 2},
                    shape_areas={"shape": 4},
                )

        from src.services.sqlite_allocation_inventory import (
            AllocationInventoryProjectionError,
        )

        with (
            patch(
                "src.services.official_role_equipment_scoring_service.legacy_shape_id",
                side_effect=AllocationInventoryProjectionError("bad"),
            ),
            patch(
                "src.services.official_role_equipment_scoring_service.score_drive_stats",
                return_value=8.0,
            ) as drive_score,
        ):
            result = score_official_role_equipment(
                object(),
                detail=self.detail,
                item={
                    "kind": "module",
                    "geometry": "shape",
                    "grid_count": 0,
                    "quality": "blue",
                    "sub_stats": [{"property_id": "Unknown"}],
                },
                shape_areas={"shape": 4},
            )
        self.assertEqual(8.0, result)
        self.assertEqual(4, drive_score.call_args.kwargs["area"])
        self.assertEqual("Blue", drive_score.call_args.kwargs["quality"])


class VirtualEquipmentTests(unittest.TestCase):
    def test_uid_assignment_and_inventory_projection(self):
        self.assertTrue(is_virtual_equipment_assignment({"virtual": True}))
        self.assertTrue(
            is_virtual_equipment_assignment(
                {"uid_slot": 0, "virtual_equipment": {}}
            )
        )
        self.assertFalse(is_virtual_equipment_assignment({"uid_slot": 1}))
        self.assertEqual(3, grid_count_from_geometry("EquipmentGeometry_3"))
        self.assertEqual(0, grid_count_from_geometry(None))
        first = virtual_equipment_uid(
            character_id=10,
            displaced_uid=(2, 3),
            ordinal=1,
            kind="module",
        )
        self.assertEqual(
            first,
            virtual_equipment_uid(
                character_id=10,
                displaced_uid=(2, 3),
                ordinal=1,
                kind="module",
            ),
        )
        self.assertEqual(0, first[0])
        self.assertGreater(first[1], 0)
        self.assertEqual(
            "virtual-module-3",
            virtual_equipment_item_id("module", "EquipmentGeometry_3"),
        )
        self.assertEqual("virtual-core", virtual_equipment_item_id("core"))

        module = make_virtual_equipment_assignment(
            {
                "uid_slot": 2,
                "uid_serial": 3,
                "kind": "module",
                "geometry": "EquipmentGeometry_3",
            },
            inventory_item={
                "suit_id": "suit",
                "names": {"zh_cn": "原装备"},
            },
            character_id=10,
            ordinal=1,
        )
        projected = virtual_equipment_inventory_item(module)
        self.assertEqual(0, projected["uid_slot"])
        self.assertEqual(3, projected["grid_count"])
        self.assertEqual(2, len(projected["main_stats"]))
        self.assertTrue(projected["virtual"])

        core = make_virtual_equipment_assignment(
            {"uid_slot": 5, "uid_serial": 6, "kind": "core"},
            inventory_item=None,
            character_id=10,
            ordinal=2,
        )
        projected_core = virtual_equipment_inventory_item(core)
        self.assertEqual("空空幕", projected_core["names"]["zh_cn"])
        self.assertEqual([], projected_core["main_stats"])
        with self.assertRaises(ValueError):
            make_virtual_equipment_assignment(
                {"kind": "weapon"},
                inventory_item=None,
                character_id=10,
                ordinal=0,
            )


class OfficialRoleReplacementTests(unittest.TestCase):
    @staticmethod
    def _item(serial, slot, *, kind="module", suit="S", geometry="G3"):
        return {
            "uid": {"serial": serial, "slot": slot},
            "uid_serial": serial,
            "uid_slot": slot,
            "kind": kind,
            "suit_id": suit,
            "geometry": geometry,
            "grid_count": 3,
        }

    def test_identity_and_candidate_filters(self):
        left = self._item(1, 2)
        self.assertTrue(_same_inventory_item(left, dict(left)))
        self.assertTrue(
            _same_inventory_item(
                {"uid_serial": 1, "uid_slot": 2},
                {"uid_serial": 1, "uid_slot": 2},
            )
        )
        marker = {}
        self.assertTrue(_same_inventory_item(marker, marker))
        self.assertFalse(_same_inventory_item({}, {}))
        self.assertEqual(
            [],
            replacement_candidates_for_official_role({}, "saved", left),
        )
        self.assertEqual(
            [],
            replacement_candidates_for_official_role(
                {"equipment_contexts": {"saved": {"plan": {}}}},
                "saved",
                left,
            ),
        )

    def test_candidates_rank_same_slot_without_reusing_equipped_item(self):
        target = self._item(1, 1)
        equipped_core = self._item(5, 5, kind="core")
        candidate = self._item(2, 2)
        wrong_shape = self._item(3, 3, geometry="G4")
        detail = {
            "equipment_contexts": {
                "saved": {
                    "plan": {"plan_id": 1},
                    "items": [target, equipped_core],
                }
            },
            "replacement_items": [
                target,
                equipped_core,
                candidate,
                wrong_shape,
            ],
            "property_weights": {},
            "main_property_weights": {},
        }

        class _StaticDao:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        with (
            patch(
                "src.services.official_role_replacement_service.StaticGameDataDao",
                return_value=_StaticDao(),
            ),
            patch(
                "src.services.official_role_replacement_service.project_equipment_items_to_max_level",
                side_effect=lambda items, _dao: [dict(item) for item in items],
            ),
            patch(
                "src.services.official_role_replacement_service.calculate_official_role_margins",
                side_effect=[{"damage": 100.0}, {"damage": 110.0}],
            ),
            patch(
                "src.services.official_role_replacement_service.calculate_official_role_final_weights",
                return_value={
                    "property_weights": {},
                    "main_property_weights": {},
                },
            ),
            patch(
                "src.services.official_role_replacement_service.calculate_official_role_item_gain",
                return_value={"gain_percent": 5.0},
            ),
            patch(
                "src.services.official_role_replacement_service.calculate_official_role_hidden_equipment_score",
                side_effect=[10.0, 20.0],
            ),
        ):
            rows = replacement_candidates_for_official_role(
                detail,
                "saved",
                target,
            )
        self.assertEqual(1, len(rows))
        self.assertEqual(2, rows[0]["item"]["uid_serial"])
        self.assertEqual(2, len(rows[0]["current_items"]))
        self.assertEqual(20.0, rows[0]["score"])
        self.assertAlmostEqual(10.0, rows[0]["gain_percent"])

    def test_save_replacement_updates_score_diff_and_uniqueness(self):
        target = self._item(1, 1)
        other = self._item(3, 3, kind="core")
        replacement = self._item(2, 2)
        plan = {
            "plan_id": 9,
            "character_id": 10,
            "source_snapshot_id": 20,
            "score": 100.0,
            "payload": {
                "assignment_scores": {
                    "nte-module-1-1": 10.0,
                    "nte-core-3-3": 5.0,
                }
            },
            "assignments": [target, other],
        }
        detail = {
            "character": {"name_zh": "角色"},
            "equipment_contexts": {"saved": {"plan": plan}},
        }
        captured = {}

        class _UserDao:
            def __init__(self, _path):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def replace_active_loadout_plans(self, plans):
                captured["plans"] = plans
                return [77]

        with patch(
            "src.services.official_role_replacement_service.UserDataDao",
            _UserDao,
        ):
            saved_id = save_official_role_replacement(
                Path("account.sqlite3"),
                detail,
                target,
                replacement,
                replacement_score=15.0,
                current_score=10.0,
            )
        self.assertEqual(77, saved_id)
        saved = captured["plans"][0]
        self.assertEqual(105.0, saved["score"])
        self.assertEqual("saved", saved["status"])
        self.assertEqual(
            ["nte-module-2-2"],
            saved["payload"]["changed_uids"],
        )

        missing = {
            "equipment_contexts": {"saved": {"plan": {"character_id": 1}}}
        }
        with self.assertRaises(ValueError):
            save_official_role_replacement(
                "missing.sqlite3",
                missing,
                target,
                replacement,
            )
        with self.assertRaises(ValueError):
            save_official_role_replacement(
                "missing.sqlite3",
                detail,
                self._item(99, 99),
                replacement,
            )
        with self.assertRaises(ValueError):
            save_official_role_replacement(
                "missing.sqlite3",
                detail,
                target,
                other,
            )


if __name__ == "__main__":
    unittest.main()
