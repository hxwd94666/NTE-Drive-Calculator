# 验证同步弧盘大小写兼容、标准身份保存与未知输入保护。
from __future__ import annotations

import copy
from pathlib import Path
import tempfile
import unittest

from src.services.native_role_profile_projection import validate_native_cultivation_patch
from src.services.official_role_profile_service import OfficialRoleProfileService
from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.user_data_support import UserDataValidationError

ROOT = Path(__file__).resolve().parents[1]


class NativeRoleForkIdentityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.database = Path(temporary.name) / "user.sqlite3"
        with UserDataDao(self.database, account_id="fork-identity-test"):
            pass
        self.service = OfficialRoleProfileService(
            self.database, static_database_path=ROOT / "data/game_static.sqlite3",
        )

    def row(self, identity):
        return {"character_id": 1010, "fork_observed": True, "fork_id": identity,
                "fork_level": 80, "fork_breakthrough_stage": 6, "fork_refinement_level": 4}

    def test_case_variants_save_catalog_spelling_without_changing_input(self):
        for raw, canonical in (("Fork_wushoutieyu", "fork_wushoutieyu"),
                               ("fork_Wushoutieyu", "fork_wushoutieyu"),
                               ("FORK_WUSHOUTIEYU", "fork_wushoutieyu"),
                               ("fork_wushoutieyu", "fork_wushoutieyu"),
                               ("fork_goldrecord", "fork_GoldRecord")):
            with self.subTest(raw=raw):
                rows = [self.row(raw)]
                original = copy.deepcopy(rows)
                result = self.service.patch_native_profiles(rows, check=lambda: None)
                self.assertEqual(1, result.saved_count)
                self.assertFalse(result.warnings)
                self.assertEqual(original, rows)
                with UserDataDao(self.database) as dao:
                    observation = dao.get_native_character_profile_observation(1010)
                self.assertEqual(canonical, observation["fork_id"])
                self.assertEqual(4, observation["fork_refinement_level"])

    def test_unknown_or_missing_prefix_preserves_saved_fork_and_valid_growth(self):
        self.service.patch_native_profiles([self.row("fork_wushoutieyu")], check=lambda: None)
        for invalid in ("wushoutieyu", "fork_missing", " fork_wushoutieyu", "fork_wushoutieyu_extra"):
            with self.subTest(identity=invalid):
                result = self.service.patch_native_profiles(
                    [{**self.row(invalid), "character_level": 70}], check=lambda: None,
                )
                self.assertEqual(1, result.saved_count)
                self.assertTrue(result.warnings)
                with UserDataDao(self.database) as dao:
                    observation = dao.get_native_character_profile_observation(1010)
                self.assertEqual("fork_wushoutieyu", observation["fork_id"])
                self.assertEqual(70, observation["character_level"])

    def test_ambiguous_case_insensitive_identity_is_rejected(self):
        row = self.row("FORK_COLLISION")
        with self.assertRaisesRegex(UserDataValidationError, "大小写匹配冲突"):
            validate_native_cultivation_patch(row, None, {"fork_collision", "fork_Collision"})
        self.assertEqual("FORK_COLLISION", row["fork_id"])

    def test_explicit_unequip_still_clears_saved_fork(self):
        self.service.patch_native_profiles([self.row("Fork_wushoutieyu")], check=lambda: None)
        result = self.service.patch_native_profiles(
            [{"character_id": 1010, "fork_observed": True, "fork_id": None}], check=lambda: None,
        )
        self.assertFalse(result.warnings)
        with UserDataDao(self.database) as dao:
            self.assertIsNone(dao.get_native_character_profile_observation(1010)["fork_id"])


if __name__ == "__main__":
    unittest.main()
