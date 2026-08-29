# 验证战报角色修改副本的养成来源边界。
from __future__ import annotations

import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.services.battle_report_history_service import BattleReportHistoryService


class _FakeBattleDao:
    def __init__(self) -> None:
        self.build_edit = None

    @staticmethod
    def load_battle_build_snapshot(_record_id: int) -> dict:
        return {
            "characters": [{
                "character_id": 1036,
                "observed_name": "残虹",
                "ordinal": 0,
                "profile": {
                    "character_level": 70,
                    "selected_awaken_effect_ids": [1, 2, 3, 4, 5, 6],
                    "awakening_selection_initialized": True,
                },
                "equipment": [],
            }]
        }

    def load_battle_build_edit(self, _record_id: int):
        return self.build_edit

    @staticmethod
    def load_battle_axis_evidence(_record_id: int) -> dict:
        return {"hits": []}

    @staticmethod
    def load_battle_report_import_origin(_record_id: int):
        return None

    @staticmethod
    def load_battle_import_equipment_locks(_record_id: int) -> dict:
        return {}


class _FakeStaticDao:
    def __init__(self, _path: Path) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        pass


class BattleBuildEditorSeedTests(unittest.TestCase):
    def setUp(self) -> None:
        dao = _FakeBattleDao()
        service = object.__new__(BattleReportHistoryService)
        service._dependencies = SimpleNamespace(
            user_database_path=Path("account.sqlite3"),
            static_database_path=Path("static.sqlite3"),
        )
        service._assert_counterfactual_editable = lambda _record_id: None

        @contextmanager
        def open_dao():
            yield dao

        service._open_current_dao = open_dao
        self.dao = dao
        self.service = service

    @staticmethod
    def _current_role_detail(*_args, **_kwargs) -> dict:
        return {
            "character": {"character_id": 1036, "name_zh": "残虹"},
            "profile": {
                "character_level": 60,
                "selected_awaken_effect_ids": [],
                "awakening_selection_initialized": True,
            },
            "equipment_contexts": {},
        }

    def _load(self, *, seed_from_role_page: bool) -> dict:
        with (
            patch(
                "src.services.battle_report_history_service.StaticGameDataDao",
                _FakeStaticDao,
            ),
            patch(
                "src.services.battle_report_history_service.static_character_shape_profile_fields",
                return_value={},
            ),
            patch(
                "src.services.battle_report_history_service.load_official_role_detail",
                side_effect=self._current_role_detail,
            ),
        ):
            return self.service.load_build_editor_data(
                41,
                seed_from_role_page=seed_from_role_page,
            )["details"][0]

    def test_first_edit_clones_immutable_battle_profile(self) -> None:
        detail = self._load(seed_from_role_page=False)

        self.assertEqual("battle_frozen", detail["editor_seed_source"])
        self.assertEqual(70, detail["profile"]["character_level"])
        self.assertEqual(
            [1, 2, 3, 4, 5, 6],
            detail["profile"]["selected_awaken_effect_ids"],
        )

    def test_explicit_role_page_sync_overwrites_cultivation_seed(self) -> None:
        self.dao.build_edit = {
            "characters": [{
                "character_id": 1036,
                "profile": {
                    "character_level": 65,
                    "selected_awaken_effect_ids": [1],
                    "equipment_override": [{"kind": "core", "stats": []}],
                },
            }]
        }
        detail = self._load(seed_from_role_page=True)

        self.assertEqual("current_role_page", detail["editor_seed_source"])
        self.assertEqual(60, detail["profile"]["character_level"])
        self.assertEqual([], detail["profile"]["selected_awaken_effect_ids"])
        self.assertIn("equipment_override", detail["profile"])


if __name__ == "__main__":
    unittest.main()
