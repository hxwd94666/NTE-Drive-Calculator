"""Project immutable imported equipment evidence onto a battle build copy."""

from __future__ import annotations

from typing import Any

from src.storage.sqlite.user_data_dao import UserDataError


def apply_import_equipment_locks(
    build: dict[str, Any] | None,
    locks: dict[int, dict[str, Any]],
) -> None:
    if build is None or not locks:
        return
    for character in build.get("characters") or ():
        lock = locks.get(int(character["character_id"]))
        if lock is None:
            raise UserDataError("导入战报缺少当前角色的固化配装")
        character["equipment"] = [
            dict(item) for item in lock.get("equipment") or ()
        ]
        character["equipment_source_kind"] = "imported_locked"
        character["equipment_sha256"] = str(lock["equipment_sha256"])


__all__ = ["apply_import_equipment_locks"]
