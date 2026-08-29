# 验证角色好感度与觉醒多选属于当前账号并可稳定重载。
import tempfile
import unittest
from pathlib import Path

from src.storage.sqlite.user_data_dao import UserDataDao


class CharacterProfileAdvancementTests(unittest.TestCase):
    def test_round_trip_preserves_likeability_and_selected_awakenings(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "user_data.sqlite3"
            with UserDataDao(database, account_id="fixture") as dao:
                saved = dao.save_character_profile(
                    character_id=1072,
                    character_level=80,
                    breakthrough_stage=6,
                    awakening_level=2,
                    selected_awaken_effect_ids=("Effect2", "Effect5"),
                    awakening_selection_initialized=True,
                    likeability_level_10_enabled=True,
                    fork_id=None,
                    fork_level=None,
                    fork_breakthrough_stage=None,
                    fork_refinement_level=None,
                    selected_skill_id="Skill",
                    skill_levels={"Skill": 10},
                )

            self.assertTrue(saved["likeability_level_10_enabled"])
            self.assertTrue(saved["awakening_selection_initialized"])
            self.assertEqual(
                ["Effect2", "Effect5"],
                saved["selected_awaken_effect_ids"],
            )
