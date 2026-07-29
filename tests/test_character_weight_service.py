# 验证账号词条权重默认值可刷新，且无改动保存不会冻结更新。

from __future__ import annotations

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
from src.services.character_shape_bonus_service import save_public_character_shape_bonus
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

    def test_shared_shape_edit_does_not_freeze_account_weights(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            static_database = root / "game_static.sqlite3"
            shared_database = root / "app_shared.sqlite3"
            user_database = root / "user.sqlite3"
            shutil.copy2(PROJECT_DATABASE, static_database)
            with UserDataDao(user_database, account_id="weights"):
                pass
            with patch.dict(
                "os.environ",
                {
                    "NTE_GAME_STATIC_DB": str(static_database),
                    "NTE_APP_SHARED_DB": str(shared_database),
                },
            ):
                before = ensure_account_character_weights(user_database, (1051,))[1051]
                save_public_character_shape_bonus(
                    1051,
                    shape_label="Type-3",
                    property_values={"CritBase": 8.0},
                    database_path=static_database,
                    shared_database_path=shared_database,
                )
                after = ensure_account_character_weights(user_database, (1051,))[1051]

        self.assertEqual("default", before["source_kind"])
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
