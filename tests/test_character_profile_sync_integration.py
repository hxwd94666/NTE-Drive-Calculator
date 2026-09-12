# 串联角色同步控制器、真实原生会话及默认持久化服务，验证稀疏字段和账号保存边界。
from copy import deepcopy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from src.features.official_role.dependencies import OfficialRoleDependencies
from src.features.official_role import page
from src.features.official_role.sync_controller import CharacterProfileSyncController
from src.services.native_game_session import NativeGameSession
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_native_game_session import FakeNativeCore


def source_responses(name):
    fixture = json.loads((Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8"))
    assert fixture["synthetic"] is True
    character = fixture["character"]
    if isinstance(character, list):
        return character[0]["response"]["result"], character[1]["response"]["result"]
    return character["refresh"]["result"], character["response"]["result"]


def seed_profile(dao, character_id):
    return dao.save_character_profile(
        character_id=character_id, character_level=10, breakthrough_stage=0,
        awakening_level=2, fork_id="synthetic-fork", fork_level=20,
        fork_breakthrough_stage=1, fork_refinement_level=3,
        selected_skill_id="synthetic-skill", skill_levels={"synthetic-skill": 7},
        selected_awaken_effect_ids=["synthetic-awaken-1", "synthetic-awaken-2"], awakening_selection_initialized=True,
        likeability_level_10_enabled=True, ordinal=4,
    )


@pytest.mark.parametrize("fixture_name", ["native_business_source_responses.json", "native_business_222.json"])
def test_controller_real_session_and_default_service_persist_only_observed_fields(tmp_path, monkeypatch, fixture_name):
    app = QApplication.instance() or QApplication([])
    header, response = source_responses(fixture_name)
    expected = {row["character_id"]: row for row in response["profiles"]}
    assert expected
    database = tmp_path / "account.sqlite3"
    static_path = Path(__file__).resolve().parents[1] / "data/game_static.sqlite3"
    assert static_path.is_file()
    with UserDataDao(database, account_id="synthetic") as dao:
        for character_id in (*expected, 1005):
            seed_profile(dao, character_id)
        before = {character_id: dao.get_character_profile(character_id) for character_id in (*expected, 1005)}
    dependencies = OfficialRoleDependencies("synthetic", 1, database, static_path, tmp_path / "shared.sqlite3")

    class CharacterCore(FakeNativeCore):
        def __init__(self):
            super().__init__()
            self.hello_result["capabilities"].append("native_character_profile_v1")
        def call(self, method, params, **kwargs):
            self.calls.append((method, deepcopy(params)))
            if method == "native.snapshot.refresh":
                assert params == {"domain": "character"}
                return deepcopy(header)
            if method == "native.character.page":
                assert params == {"snapshotId": header["snapshotId"], "offset": 0, "limit": 64}
                return deepcopy(response)
            return {}

    core = CharacterCore()
    session = NativeGameSession(lambda: core, lambda _cap: None, lambda: 1)
    owner = QWidget()
    owner._official_role_dirty_ids = set(expected)
    owner._official_role_world_bonus_dirty = True
    owner._my_role_dirty = True
    owner._official_role_editors = {next(iter(expected)): {"draft": "pending edit"}}
    notifications, rendered = [], []
    monkeypatch.setattr("src.features.official_role.sync_controller.QMessageBox.warning", lambda *args: notifications.append(args))
    monkeypatch.setattr("src.features.official_role.sync_controller.QMessageBox.information", lambda *args: notifications.append(args))
    monkeypatch.setattr(page, "_refresh_my_role", lambda *_args, **_kwargs: rendered.append(True))

    def refresh():
        # Successful saving must precede clearing the user's pending drafts.
        assert owner._my_role_dirty
        with UserDataDao(database) as dao:
            for character_id, patch in expected.items():
                assert dao.get_native_character_profile_observation(character_id)["character_level"] == patch["character_level"]
        page.refresh_official_role_page(owner, discard_pending=True)

    controller = CharacterProfileSyncController(
        parent=owner, dependencies_factory=lambda: dependencies, operation_generation=lambda: 1,
        read_profiles=session.read_character_profiles, operation_guard=lambda _cap: None,
        operation_entry=lambda *_args: True, operation_unavailable=lambda *args: notifications.append(args),
        hotkey_manager=SimpleNamespace(active_owner=None, start=Mock(), stop=Mock()), refresh=refresh,
        # Deliberately retain the production OfficialRoleProfileService factory and real worker.
    )
    button, editor = QPushButton(owner), QWidget(owner)
    controller.attach_controls(button, (editor,))
    worker = None
    try:
        button.click()
        worker = controller._thread
        assert controller.is_running() and not editor.isEnabled()
        deadline = time.monotonic() + 3
        while controller.is_running() and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert not controller.is_running()
        assert notifications == [] and rendered == [True]
        assert editor.isEnabled()
        assert not owner._my_role_dirty and not owner._official_role_dirty_ids
        assert not owner._official_role_world_bonus_dirty and owner._official_role_editors == {}
        with UserDataDao(database) as dao:
            assert dao.get_character_profile(1005) == before[1005]
            assert dao.get_native_character_profile_observation(1005) is None
            for character_id, patch in expected.items():
                actual = dao.get_character_profile(character_id)
                assert actual["character_level"] == patch["character_level"]
                assert actual["breakthrough_stage"] == patch.get("breakthrough_stage", before[character_id]["breakthrough_stage"])
                untouched = set(before[character_id]) - {"character_level", "breakthrough_stage", "updated_at_utc"}
                assert {key: actual[key] for key in untouched} == {key: before[character_id][key] for key in untouched}
                observation = dao.get_native_character_profile_observation(character_id)
                assert observation["breakthrough_stage"] == patch.get("breakthrough_stage")
        assert [method for method, _params in core.calls] == ["native.snapshot.refresh", "native.character.page"]
    finally:
        controller.close()
        if worker is not None:
            worker.join(3)
        session.close()
        owner.close()
