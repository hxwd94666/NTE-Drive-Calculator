# 用真实角色服务和临时账号库验证后台角色同步回执、事务失败与代次取消。
from types import SimpleNamespace

import pytest

from src.services.inventory_capture_wait import InventorySyncCancelled
from src.services.inventory_sync_runtime import _apply_native_profiles
from src.services.inventory_sync_service import InventorySyncService
from src.services.official_role_profile_service import OfficialRoleProfileService
from src.storage.sqlite.user_data_dao import UserDataDao
from tests.test_native_role_profile_patch import defaults, seed


@pytest.fixture
def automatic(tmp_path):
    path = tmp_path / "account.sqlite3"
    with UserDataDao(path, account_id="fixture") as dao:
        original = seed(dao)
    context = {"generation": 3}
    role_service = OfficialRoleProfileService(path, growth_defaults_loader=defaults)
    service = InventorySyncService(
        path, account_id="fixture", capture_source="native", client_factory=lambda: None,
        operation_guard=lambda _: None, context_is_current=lambda: context["generation"] == 3,
        native_profiles_apply=role_service.patch_native_profiles,
    )
    notices = []
    service.add_state_handler(notices.append)
    return SimpleNamespace(path=path, original=original, context=context, roles=role_service,
                           service=service, notices=notices)


def status(*profiles):
    return {"native_character_snapshot": {"profiles": list(profiles)}}


def test_success_advances_ui_revision_only_after_committed_sparse_profile_update(automatic):
    value = automatic
    committed_levels = []

    def observe(state):
        if state.character_sync_revision:
            with UserDataDao(value.path) as dao:
                committed_levels.append(dao.get_character_profile(1003)["character_level"])

    value.service.add_state_handler(observe)
    _apply_native_profiles(value.service, status({"character_id": 1003, "character_level": 61,
                                                "awakening_level": 6, "skill_levels": {}}))
    assert value.service.state.character_sync_revision == 1
    assert value.service.state.character_sync_error is None
    assert committed_levels == [61]
    with UserDataDao(value.path) as dao:
        after = dao.get_character_profile(1003)
        assert after["character_level"] == 61
        for key in value.original.keys() - {"character_level", "updated_at_utc"}:
            assert after[key] == value.original[key]
        assert dao.get_native_character_profile_observation(1003)["character_level"] == 61


def test_failed_batch_preserves_all_profiles_and_retry_clears_error(automatic):
    value = automatic
    _apply_native_profiles(value.service, status(
        {"character_id": 1003, "character_level": 61},
        {"character_id": 1004, "character_level": 10},
    ))
    assert value.service.state.character_sync_revision == 0
    assert "已保留原养成" in value.service.state.character_sync_error
    assert all(state.character_sync_revision == 0 for state in value.notices)
    with UserDataDao(value.path) as dao:
        assert dao.get_character_profile(1003) == value.original
        assert dao.get_native_character_profile_observation(1003) is None
        assert dao.get_native_character_profile_observation(1004) is None
    _apply_native_profiles(value.service, status({"character_id": 1003, "character_level": 61}))
    assert value.service.state.character_sync_revision == 1
    assert value.service.state.character_sync_error is None


@pytest.mark.parametrize("invalidation", ["generation", "cancel"])
def test_stale_or_cancelled_sync_rejects_write_and_never_publishes_success(automatic, invalidation):
    value = automatic
    if invalidation == "generation":
        value.context["generation"] += 1
    else:
        value.service._stop_requested.set()
    with pytest.raises(InventorySyncCancelled):
        _apply_native_profiles(value.service, status({"character_id": 1003, "character_level": 61}))
    assert value.service.state.character_sync_revision == 0
    assert value.notices == []
    with UserDataDao(value.path) as dao:
        assert dao.get_character_profile(1003) == value.original
        assert dao.get_native_character_profile_observation(1003) is None


@pytest.mark.parametrize("invalidation", ["generation", "cancel"])
def test_in_transaction_invalidation_rolls_back_and_suppresses_success(automatic, invalidation):
    value = automatic
    interrupted = []

    class InterruptingDao(UserDataDao):
        def patch_native_character_profiles(self, profiles, *, growth_defaults, check):
            def guarded():
                observation = self.get_native_character_profile_observation(1003)
                if self._db().in_transaction and observation is not None:
                    interrupted.append(True)
                    if invalidation == "generation":
                        value.context["generation"] += 1
                    else:
                        value.service._stop_requested.set()
                check()

            return super().patch_native_character_profiles(profiles, growth_defaults=growth_defaults, check=guarded)

    roles = OfficialRoleProfileService(value.path, dao_factory=InterruptingDao, growth_defaults_loader=defaults)
    value.service._native_profiles_apply = roles.patch_native_profiles
    with pytest.raises(InventorySyncCancelled):
        _apply_native_profiles(value.service, status({"character_id": 1003, "character_level": 61}))
    assert interrupted == [True]
    assert value.service.state.character_sync_revision == 0
    assert value.notices == []
    with UserDataDao(value.path) as dao:
        assert dao.get_character_profile(1003) == value.original
        assert dao.get_native_character_profile_observation(1003) is None


def test_read_failure_is_visible_without_advancing_revision_or_touching_account(automatic):
    value = automatic
    _apply_native_profiles(value.service, {"native_character_error": "角色状态尚未就绪，保留已保存养成。"})
    assert value.service.state.character_sync_revision == 0
    assert value.service.state.character_sync_error == "角色状态尚未就绪，保留已保存养成。"
    assert len(value.notices) == 1
    _apply_native_profiles(value.service, {"native_character_error": value.service.state.character_sync_error})
    assert len(value.notices) == 1
    with UserDataDao(value.path) as dao:
        assert dao.get_character_profile(1003) == value.original
