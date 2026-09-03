# 验证账号词条权重默认值可刷新，且无改动保存不会冻结更新。

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.services.character_weight_service import (
    ensure_account_character_weights,
    reset_account_character_weights,
    save_account_character_weights,
)
from src.services.custom_character_service import (
    create_custom_character,
    delete_custom_character,
    save_custom_character_board,
    save_custom_character_shape_bonus,
    save_custom_character_target_suit,
)
from src.storage.sqlite.user_data_dao import UserDataDao


PROJECT_DATABASE = Path(__file__).resolve().parents[1] / "data" / "game_static.sqlite3"


class CharacterWeightServiceTests(unittest.TestCase):
    def test_noop_save_keeps_default_row_refreshable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            static_database = Path(directory) / "game_static.sqlite3"
            user_database = Path(directory) / "user.sqlite3"
            shutil.copy2(PROJECT_DATABASE, static_database)
            with UserDataDao(user_database, account_id="weights"):
                pass
            with patch.dict("os.environ", {"NTE_GAME_STATIC_DB": str(static_database)}):
                before = ensure_account_character_weights(user_database, (1051,))[1051]
                after = save_account_character_weights(
                    user_database,
                    1051,
                    before["property_weights"],
                    main_property_weights=before["main_property_weights"],
                )

        self.assertEqual("default", after["source_kind"])
        self.assertEqual(before["seeded_at_utc"], after["seeded_at_utc"])
        self.assertEqual(before["updated_at_utc"], after["updated_at_utc"])

    def test_reset_restores_default_after_a_real_edit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            static_database = Path(directory) / "game_static.sqlite3"
            user_database = Path(directory) / "user.sqlite3"
            shutil.copy2(PROJECT_DATABASE, static_database)
            with UserDataDao(user_database, account_id="weights"):
                pass
            with patch.dict("os.environ", {"NTE_GAME_STATIC_DB": str(static_database)}):
                before = ensure_account_character_weights(user_database, (1051,))[1051]
                edited_weights = dict(before["property_weights"])
                edited_weights["CritBase"] = edited_weights.get("CritBase", 0.0) + 0.25
                edited = save_account_character_weights(
                    user_database, 1051, edited_weights,
                    main_property_weights=before["main_property_weights"],
                )
                restored = reset_account_character_weights(user_database, (1051,))[1051]

        self.assertEqual("account", edited["source_kind"])
        self.assertEqual("default", restored["source_kind"])
        self.assertTrue(restored["seeded_at_utc"] == restored["updated_at_utc"])
        self.assertEqual(before["property_weights"], restored["property_weights"])
        self.assertEqual(before["main_property_weights"], restored["main_property_weights"])

    def test_runtime_template_refreshes_default_but_never_account_override(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static_database = root / "game_static.sqlite3"
            user_database = root / "user.sqlite3"
            template_file = root / "workshop_weight_template.json"
            shutil.copy2(PROJECT_DATABASE, static_database)
            template_file.write_text(json.dumps({
                "schema_version": 1,
                "payload_sha256": "first",
                "characters": {
                    "1051": {
                        "character_id": 1051,
                        "source_kind": "workshop_runtime",
                        "properties": [{
                            "property_id": "CritBase", "weight": 1.4,
                            "main_weight": 0.8, "ordinal": 0,
                        }],
                        "property_weights": {"CritBase": 1.4},
                        "main_property_weights": {"CritBase": 0.8},
                    },
                },
            }), encoding="utf-8")
            with UserDataDao(user_database, account_id="weights"):
                pass
            with patch.dict("os.environ", {
                "NTE_GAME_STATIC_DB": str(static_database),
                "NTE_WORKSHOP_WEIGHT_TEMPLATE_FILE": str(template_file),
            }, clear=False):
                default = ensure_account_character_weights(user_database, (1051,))[1051]
                account = save_account_character_weights(
                    user_database,
                    1051,
                    {"CritBase": 2.0},
                    main_property_weights={"CritBase": 1.0},
                )
                template = json.loads(template_file.read_text(encoding="utf-8"))
                template["payload_sha256"] = "second"
                template["characters"]["1051"]["property_weights"] = {"CritBase": 1.8}
                template["characters"]["1051"]["properties"][0]["weight"] = 1.8
                template_file.write_text(json.dumps(template), encoding="utf-8")
                after_refresh = ensure_account_character_weights(user_database, (1051,))[1051]
                reset = reset_account_character_weights(user_database, (1051,))[1051]

        self.assertEqual({"CritBase": 1.4}, default["property_weights"])
        self.assertEqual("account", account["source_kind"])
        self.assertEqual({"CritBase": 2.0}, after_refresh["property_weights"])
        self.assertEqual({"CritBase": 1.8}, reset["property_weights"])

    def test_custom_character_has_an_account_owned_weight_seed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user.sqlite3"
            with UserDataDao(database, account_id="custom"):
                pass
            created = create_custom_character(database, "测试自建角色")
            with UserDataDao(database) as dao:
                custom = dao.list_custom_characters()
                self.assertEqual(created["character_id"], custom[0]["character_id"])
                self.assertEqual(25, len(custom[0]["board_cells"]))
                weights = dao.get_character_weight_preferences(created["character_id"])
        self.assertEqual("custom", weights["source_kind"])

    def test_custom_character_board_round_trips_enabled_and_locked_cells(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user.sqlite3"
            with UserDataDao(database, account_id="custom"):
                pass
            created = create_custom_character(database, "测试底盘角色")
            with UserDataDao(database) as dao:
                initial = dao.list_custom_characters()[0]["board_cells"]
            edited = [
                {
                    "row": int(cell["row_number"]),
                    "column": int(cell["column_number"]),
                    "is_enabled": bool(cell["is_enabled"]),
                    "is_locked": bool(cell["is_locked"]),
                }
                for cell in initial
            ]
            for cell in edited:
                if (cell["row"], cell["column"]) == (4, 5):
                    cell["is_enabled"] = False
                elif (cell["row"], cell["column"]) == (5, 1):
                    cell["is_enabled"] = True
                    cell["is_locked"] = True
            save_custom_character_board(database, int(created["character_id"]), edited)
            with UserDataDao(database) as dao:
                stored = dao.list_custom_characters()[0]["board_cells"]

        by_position = {
            (row["row_number"], row["column_number"]): row for row in stored
        }
        self.assertFalse(by_position[(4, 5)]["is_enabled"])
        self.assertTrue(by_position[(5, 1)]["is_enabled"])
        self.assertTrue(by_position[(5, 1)]["is_locked"])

    def test_deleting_custom_character_removes_its_weight_seed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user.sqlite3"
            with UserDataDao(database, account_id="custom"):
                pass
            created = create_custom_character(database, "待删除角色")
            delete_custom_character(database, int(created["character_id"]))
            with UserDataDao(database) as dao:
                remaining = dao.list_custom_characters()
                weights = dao.get_character_weight_preferences(created["character_id"])

        self.assertEqual([], remaining)
        self.assertIsNone(weights)

    def test_custom_character_keeps_an_account_private_target_suit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user.sqlite3"
            with UserDataDao(database, account_id="custom-suit"):
                pass
            created = create_custom_character(database, "套装角色")
            save_custom_character_target_suit(
                database,
                int(created["character_id"]),
                "EquipmentSuit_Test",
            )
            with UserDataDao(database) as dao:
                stored = dao.list_custom_characters()[0]
        self.assertEqual("EquipmentSuit_Test", stored["target_suit_id"])

    def test_custom_character_keeps_an_account_private_extra_shape_setting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user.sqlite3"
            with UserDataDao(database, account_id="custom"):
                pass
            created = create_custom_character(database, "额外形状角色")
            save_custom_character_shape_bonus(
                database,
                int(created["character_id"]),
                shape_label="Type-4",
                property_values={"CritBase": 8.0},
            )
            with UserDataDao(database) as dao:
                stored = dao.get_custom_character_shape_bonus(created["character_id"])

        self.assertEqual("Type-4", stored["shape_label"])
        self.assertEqual(
            [{"property_id": "CritBase", "display_value": 8.0}],
            stored["properties"],
        )


if __name__ == "__main__":
    unittest.main()
