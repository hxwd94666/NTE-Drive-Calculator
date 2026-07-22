# 校验仓库单件鉴定从固定官方快照取数，再交给现有鉴定流程。
import unittest
from types import SimpleNamespace


class WarehouseIdentificationServiceTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
