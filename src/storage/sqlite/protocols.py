# 描述 SQLite DAO mixin 实际依赖的宿主读取、写入与跨域查询能力。
"""Typing protocols for focused SQLite DAO mixin hosts."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TYPE_CHECKING


if TYPE_CHECKING:
    class UserDataDaoMixinHost:
        """Static-checking host surface shared by user-data mixins.

        This is deliberately not a ``Protocol`` base.  A protocol in the real
        facade's MRO would make ``UserDataDao`` abstract even though
        ``UserDataDaoCore`` supplies every host operation.
        """

        database_path: Path

        def _rows(
            self,
            sql: str,
            parameters: Iterable[Any] = (),
        ) -> list[dict[str, Any]]:
            raise NotImplementedError

        def _one(
            self,
            sql: str,
            parameters: Iterable[Any] = (),
        ) -> dict[str, Any] | None:
            raise NotImplementedError

        def _db(self) -> sqlite3.Connection:
            raise NotImplementedError

        def profile(self) -> dict[str, Any]:
            raise NotImplementedError

        def get_sync_settings(self) -> dict[str, Any]:
            raise NotImplementedError

        def current_inventory_summary(self) -> dict[str, Any] | None:
            raise NotImplementedError

        def inventory_snapshot_summary(
            self,
            snapshot_id: int,
        ) -> dict[str, Any] | None:
            raise NotImplementedError

else:
    class UserDataDaoMixinHost:
        """Runtime marker; concrete behavior comes from UserDataDaoCore."""


if TYPE_CHECKING:
    class StaticDataDaoMixinHost:
        """Static-checking host surface for read-only query mixins."""

        def _rows(
            self,
            sql: str,
            parameters: Iterable[Any] = (),
        ) -> list[dict[str, Any]]:
            raise NotImplementedError

        def _one(
            self,
            sql: str,
            parameters: Iterable[Any] = (),
        ) -> dict[str, Any] | None:
            raise NotImplementedError

else:
    class StaticDataDaoMixinHost:
        """Runtime marker; concrete behavior comes from StaticGameDataDao."""
