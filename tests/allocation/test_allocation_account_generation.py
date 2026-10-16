# 验证计算线程在账号代次变化后丢弃旧输入和迟到结果。
from __future__ import annotations

from concurrent.futures import CancelledError
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.features.allocation.runner import (
    AllocationRunResult,
    _on_done,
    _run_allocation,
    _select_allocation_save_slots,
)
from src.features.allocation.save_workflow import save_allocation
from src.services.allocation.locks import AllocationLockSnapshot
from tests.allocation.static_release_helpers import clone_test_release


NTE_TEST_TIER = "core"


def test_slot_selection_does_not_create_an_empty_slot() -> None:
    owner = SimpleNamespace(final_plan={"role": {"valid": True}})
    user_dao = SimpleNamespace(list_loadout_slots=lambda _character_id: [])
    with patch(
        "src.features.allocation.runner.resolve_character_id_for_allocation_role",
        return_value=1004,
    ):
        targets = _select_allocation_save_slots(owner, user_dao, object(), 1)
    assert targets == {"role": (1004, None)}


def _window(generation: int, account_id: str) -> SimpleNamespace:
    return SimpleNamespace(app_context=SimpleNamespace(
        generation=generation,
        account=SimpleNamespace(
            active_account_id=account_id,
            user_database_path=Path("account.sqlite3"),
        ),
    ))


def test_stale_worker_does_not_open_a_different_account() -> None:
    window = _window(2, "new")
    with pytest.raises(CancelledError, match="账号已切换"):
        _run_allocation(
            window, "role_priority", [], {},
            expected_context_identity=(1, "old", Path("account.sqlite3")),
        )


def test_stale_result_does_not_replace_the_new_account_preview() -> None:
    window = _window(2, "new")
    window.final_plan = {"current": {"valid": True}}
    result = AllocationRunResult(
        plans={"stale": {"valid": True}},
        snapshot_id=1,
        lock_snapshot=None,
        selected_locked_role_names=frozenset(),
        static_database_path=Path("static.sqlite3"),
        static_dataset_id="old",
        static_file_identity=(0, 0),
        context_identity=(1, "old", Path("account.sqlite3")),
    )

    _on_done(window, result)

    assert window.final_plan == {"current": {"valid": True}}


def test_previous_run_result_does_not_replace_a_newer_preview() -> None:
    window = _window(2, "same")
    window._pending_run_id = 2
    window.final_plan = {"current": {"valid": True}}
    result = AllocationRunResult(
        plans={"older": {"valid": True}},
        snapshot_id=1,
        lock_snapshot=None,
        selected_locked_role_names=frozenset(),
        static_database_path=Path("static.sqlite3"),
        static_dataset_id="same",
        static_file_identity=(0, 0),
        context_identity=(2, "same", Path("account.sqlite3")),
        run_id=1,
    )

    _on_done(window, result)

    assert window.final_plan == {"current": {"valid": True}}


def test_save_rechecks_current_account_after_slot_dialog(tmp_path) -> None:
    static_path, release = clone_test_release(tmp_path)
    owner = _window(1, "old")
    owner.final_plan = {"role": {"valid": True}}
    owner._saving = False
    owner._worker = None
    owner.btn_save = None
    owner._cancel_event = Event()
    owner._pending_allocation_snapshot_id = 1
    owner._pending_allocation_static_identity = (
        static_path, release.dataset_id, (static_path.stat().st_size, static_path.stat().st_mtime_ns),
    )
    owner._pending_allocation_static_release = release
    owner._allocation_lock_snapshot = AllocationLockSnapshot(
        inventory_snapshot_id=1,
        locked_role_names=frozenset(),
        reserved_uids=frozenset(),
        plan_revisions=(),
    )

    def switch_account(*_args, **_kwargs):
        owner.app_context = _window(2, "new").app_context
        return {"role": (1, 1)}

    with (
        patch("src.features.allocation.runner._allocation_paths", return_value=(
            Path("account.sqlite3"), Path("config"), Path("user-config"),
            Path("screenshots"), static_path,
        )),
        patch("src.features.allocation.runner._select_allocation_save_slots",
              side_effect=switch_account),
        patch("src.features.allocation.save_workflow.UserDataDao"),
        patch("src.features.allocation.save_workflow.AllocationSaveProgress") as progress,
    ):
        assert save_allocation(owner, show_message=False) is False

    assert owner.app_context.generation == 2
    progress.assert_not_called()
