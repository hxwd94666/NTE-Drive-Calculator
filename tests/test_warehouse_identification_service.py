# 校验仓库单件鉴定从固定官方快照取数，再交给现有鉴定流程。
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from src.models.equipment import Drive


class WarehouseIdentificationServiceTests(unittest.TestCase):
    def test_shared_identification_service_ranks_drive_role_matches(self):
        from src.services.equipment_identification_service import (
            EquipmentIdentificationService,
        )

        class Orchestrator:
            roles_db = {
                "角色A": {"default_set": "套装A", "weights": {"score": 4.0}},
                "角色B": {"default_set": "套装A", "weights": {"score": 8.0}},
            }
            sets_db = {"套装A": {"shapes": ["H_3"]}}

            @staticmethod
            def _resolve_set_name(value):
                return value

        class Scoring:
            @staticmethod
            def max_theoretical_weight(_weights):
                return 10.0

            @staticmethod
            def calculate_drive_score(_item, weights, _max_weight):
                return weights["score"]

            @staticmethod
            def get_grade_tag(score, _area):
                return "S" if score >= 8 else "A"

        item = Drive(
            uid="drive-1",
            quality="Gold",
            area=3,
            sub_stats={"攻击力%": 1.0},
            discarded=False,
            shape_id="H_3",
            set_name="测试套装",
            main_stats={"攻击力": 10.0, "防御力": 10.0},
        )
        service = EquipmentIdentificationService(
            cast(Any, Orchestrator()),
            {"角色A": [{}], "角色B": [{}]},
            cast(Any, Scoring()),
        )

        result = service.identify_item(item)

        self.assertIs(item, result["item"])
        self.assertEqual(["角色B", "角色A"], [row["role"] for row in result["rows"]])
        self.assertEqual(["S", "A"], [row["grade"] for row in result["rows"]])

    def test_load_item_returns_official_snapshot_drive(self):
        from src.services.warehouse_identification_service import WarehouseIdentificationService

        payload = {
            "uid": "nte-module-1-2", "item_type": "drive", "quality": "Gold", "area": 3,
            "sub_stats": {"攻击力%": 1.0}, "discarded": False, "shape_id": "H_3",
            "set_name": "未知套装", "main_stats": {"攻击力": 10.0, "防御力": 10.0},
        }

        class ContextManager:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        class Projection:
            def build(self, snapshot_id):
                if snapshot_id != 7:
                    raise AssertionError("unexpected snapshot")
                return SimpleNamespace(items=(payload,))

        service = WarehouseIdentificationService(
            "unused.sqlite3",
            dao_factory=lambda _path: ContextManager(),
            static_dao_factory=ContextManager,
            projection_factory=lambda _dao, _static: Projection(),
        )

        item = service.load_item(7, "nte-module-1-2")

        self.assertEqual("drive", item.item_type)
        self.assertEqual("H_3", item.shape_id)
        self.assertEqual({"攻击力%": 1.0}, item.sub_stats)

    def test_warehouse_scoring_uses_public_service_not_main_window_method(self):
        from src.features.inventory.warehouse_identification_controller import (
            _identify_snapshot_items,
        )

        item = Drive(
            uid="drive-1",
            quality="Gold",
            area=3,
            sub_stats={"攻击力%": 1.0},
            discarded=False,
            shape_id="H_3",
            set_name="测试套装",
            main_stats={"攻击力": 10.0, "防御力": 10.0},
        )
        matcher = SimpleNamespace(
            identify_item=lambda value: {"item": value, "rows": [{"role": "角色A"}]}
        )
        with (
            patch(
                "src.features.inventory.warehouse_identification_controller."
                "WarehouseIdentificationService"
            ) as loader_type,
            patch(
                "src.features.inventory.warehouse_identification_controller."
                "EquipmentIdentificationService.from_paths",
                return_value=matcher,
            ) as matcher_factory,
        ):
            loader_type.return_value.load_item.return_value = item
            result = _identify_snapshot_items(
                database_path="account.sqlite3",
                config_dir="config",
                snapshot_id=7,
                uids=("drive-1", "drive-2"),
            )

        self.assertEqual(2, len(result))
        self.assertEqual("角色A", result[0]["rows"][0]["role"])
        self.assertEqual(2, loader_type.return_value.load_item.call_count)
        matcher_factory.assert_called_once_with(
            config_dir="config",
            user_database_path="account.sqlite3",
        )


if __name__ == "__main__":
    unittest.main()
