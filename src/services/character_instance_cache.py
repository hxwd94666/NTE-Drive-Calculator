# 提供按账号隔离的安装级角色实例缓存，供极速装配查询使用。
"""安装级角色实例缓存：跨账号目录保留、按账号 ID 严格隔离。"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from src.storage.sqlite.user_data_dao import UserDataDao


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def default_character_instance_cache_path(user_database_path: str | Path) -> Path:
    """Return the writable, install-level cache path without using static data.

    ``game_static.sqlite3`` is bundled game data and may be replaced during a
    data update.  Character instance UIDs must never be placed there.  This
    adjacent sidecar is shared by the local installation but every query is
    scoped by the application account ID.
    """

    path = Path(user_database_path).expanduser().resolve()
    accounts_directory = path.parent.parent
    root = accounts_directory.parent if accounts_directory.name == "accounts" else path.parent
    return root / "data" / "character_instance_cache.sqlite3"


class CharacterInstanceCache:
    """Small public-local cache for account-private character instance UIDs."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.database_path, timeout=10.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout = 10000")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS character_instance_cache (
                account_id TEXT NOT NULL,
                character_id INTEGER NOT NULL CHECK (character_id > 0),
                uid_slot INTEGER NOT NULL CHECK (uid_slot > 0),
                uid_serial INTEGER NOT NULL CHECK (uid_serial > 0),
                source TEXT NOT NULL CHECK (source IN ('snapshot', 'manual')),
                updated_at_utc TEXT NOT NULL,
                PRIMARY KEY (account_id, character_id)
            )
            """
        )
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "CharacterInstanceCache":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    @staticmethod
    def _account_id(value: object) -> str:
        account_id = str(value or "").strip()
        if not account_id:
            raise ValueError("角色实例缓存缺少账号 ID")
        return account_id

    @staticmethod
    def _uid(value: Mapping[str, object]) -> tuple[int, int]:
        try:
            slot = int(value["slot"])
            serial = int(value["serial"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("角色实例 UID 无效") from exc
        if slot <= 0 or serial <= 0:
            raise ValueError("角色实例 UID 必须为正整数")
        return slot, serial

    def upsert(
        self, account_id: object, character_id: int, uid: Mapping[str, object], *, source: str,
    ) -> None:
        if source not in {"snapshot", "manual"}:
            raise ValueError("角色实例缓存 source 无效")
        account = self._account_id(account_id)
        character = int(character_id)
        if character <= 0:
            raise ValueError("角色实例缓存 character_id 无效")
        slot, serial = self._uid(uid)
        self._connection.execute(
            """
            INSERT INTO character_instance_cache(
                account_id, character_id, uid_slot, uid_serial, source, updated_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id, character_id) DO UPDATE SET
                uid_slot = excluded.uid_slot,
                uid_serial = excluded.uid_serial,
                source = excluded.source,
                updated_at_utc = excluded.updated_at_utc
            """,
            (account, character, slot, serial, source, _utc_now()),
        )
        self._connection.commit()

    def get(self, account_id: object, character_id: int) -> dict[str, int] | None:
        row = self._connection.execute(
            """
            SELECT uid_slot, uid_serial FROM character_instance_cache
            WHERE account_id = ? AND character_id = ?
            """,
            (self._account_id(account_id), int(character_id)),
        ).fetchone()
        return ({"slot": int(row["uid_slot"]), "serial": int(row["uid_serial"])} if row else None)


def mirror_user_character_instance_cache(
    user_dao: UserDataDao, *, cache_path: str | Path | None = None,
) -> int:
    """Copy unambiguous per-account mappings to the installation-level cache."""

    profile = user_dao.profile()
    account_id = profile["account_id"]
    rows_by_character: dict[int, list[dict]] = defaultdict(list)
    for row in user_dao.list_character_instance_mappings():
        rows_by_character[int(row["character_id"])].append(row)
    copied = 0
    with CharacterInstanceCache(cache_path or default_character_instance_cache_path(user_dao.database_path)) as cache:
        for character_id, rows in rows_by_character.items():
            manual_rows = [row for row in rows if row.get("source") == "manual"]
            candidates = manual_rows or rows
            uids = {(int(row["uid_slot"]), int(row["uid_serial"])) for row in candidates}
            if len(uids) != 1:
                continue
            slot, serial = next(iter(uids))
            cache.upsert(
                account_id, character_id, {"slot": slot, "serial": serial},
                source="manual" if manual_rows else "snapshot",
            )
            copied += 1
    return copied
