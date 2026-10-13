# 按账号原子保存养成历史、冻结全选集合并执行修订号保护的批量删除。
from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Sequence
from functools import lru_cache
from typing import Any

from src.domain.cultivation_history import (
    PAYLOAD_VERSION, HistoryConflict, HistoryPage, HistoryPayload, HistoryRecord,
    HistorySelection, HistorySummary, integer, text_value,
    MAX_PAYLOAD_BYTES, MAX_COLLECTION_SIZE, configuration_character_labels, configuration_search_names,
)
from src.domain.name_search import match_pinyin
from src.storage.sqlite.protocols import UserDataDaoMixinHost
from src.storage.sqlite.user_data_support import UserDataError, UserDataValidationError, _utc_now

_SUMMARY_COLUMNS = (
    "history_id, mode, revision, first_calculated_at_utc, last_calculated_at_utc, "
    f"CASE WHEN length(CAST(summary_json AS BLOB)) <= {MAX_PAYLOAD_BYTES} "
    "THEN summary_json ELSE '{}' END AS summary_json"
)


def _predicate(mode: str | None, search: str) -> tuple[str, tuple[str, ...]]:
    clauses: list[str] = []
    parameters: list[str] = []
    if mode is not None:
        if mode not in {"single", "batch"}:
            raise UserDataValidationError("养成历史筛选模式无效")
        clauses.append("mode = ?")
        parameters.append(mode)
    if not isinstance(search, str) or len(search) > 256:
        raise UserDataValidationError("养成历史搜索文本无效")
    if search.strip():
        clauses.append(
            "nte_history_name_match("
            f"CASE WHEN length(CAST(configuration_json AS BLOB)) <= {MAX_PAYLOAD_BYTES} "
            "THEN configuration_json ELSE NULL END, "
            f"CASE WHEN length(CAST(search_text AS BLOB)) <= {MAX_PAYLOAD_BYTES} "
            "THEN search_text ELSE NULL END, ?) = 1"
        )
        parameters.append(search.strip())
    return (" WHERE " + " AND ".join(clauses) if clauses else ""), tuple(parameters)


def _register_search(connection: sqlite3.Connection, check: Callable[[], None]) -> None:
    # Cache only name matches for this connection/request, never account payloads globally.
    @lru_cache(maxsize=512)
    def matches_name(name: str, query: str) -> bool:
        return match_pinyin(name, query)

    def matches(configuration: str | None, legacy: str | None, query: str) -> int:
        check()
        try:
            names = configuration_search_names(configuration or "")
        except ValueError:
            # A damaged record stays searchable/deletable by its bounded legacy names.
            names = tuple(legacy.splitlines()) if isinstance(legacy, str) else ()
            if len(names) > MAX_COLLECTION_SIZE:
                return 0
        return int(any(matches_name(name, query) for name in names if 0 < len(name) <= 512))

    connection.create_function("nte_history_name_match", 3, matches)


def _summary(row: Any) -> HistorySummary:
    labels: tuple[str, ...] = ()
    if "display_configuration_json" in row.keys() and row["display_configuration_json"] is not None:
        try:
            labels = configuration_character_labels(row["display_configuration_json"])
        except ValueError:
            pass  # Keep the row selectable even when its configuration is damaged.
    return HistorySummary(character_labels=labels, **{key: row[key] for key in (
        "history_id", "mode", "revision", "first_calculated_at_utc",
        "last_calculated_at_utc", "summary_json",
    )})


class CultivationHistoryDaoMixin(UserDataDaoMixinHost):
    """SQL 与账号一致性的唯一持久化入口；更新缺失行绝不补插。"""

    def _history_account(self, account_id: str, check: Callable[[], None]) -> None:
        check()
        if self.profile()["account_id"] != account_id:
            raise UserDataValidationError("养成历史与当前账号不一致")

    def save_cultivation_history(
        self, account_id: str, history_id: str, payload: HistoryPayload, *,
        expected_revision: int | None = None, check: Callable[[], None] = lambda: None,
    ) -> HistorySummary:
        self._history_account(account_id, check)
        text_value(history_id, maximum=128)
        if expected_revision is not None:
            integer(expected_revision, 1)
        # A caller can construct the frozen DTO directly: validate its contents here too.
        verified = HistoryPayload.decode(payload.configuration_json, payload.result_snapshot_json)
        if verified != payload:
            raise UserDataValidationError("养成历史摘要或指纹不一致")
        configuration = json.loads(payload.configuration_json)
        search_text = "\n".join(target["name"] for target in configuration["targets"])
        connection = self._db()
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                check()
                existing = connection.execute(
                    "SELECT * FROM cultivation_history WHERE history_id = ?", (history_id,),
                ).fetchone()
                if expected_revision is None:
                    if existing is not None:
                        raise HistoryConflict("养成历史身份已存在，请刷新保存绑定")
                    now = _utc_now()
                    connection.execute(
                        "INSERT INTO cultivation_history "
                        "(history_id, mode, revision, first_calculated_at_utc, last_calculated_at_utc, "
                        "payload_version, configuration_json, result_snapshot_json, summary_json, search_text, content_sha256) "
                        "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (history_id, payload.mode, now, now, PAYLOAD_VERSION, payload.configuration_json,
                         payload.result_snapshot_json, payload.summary_json, search_text, payload.content_sha256),
                    )
                else:
                    if existing is None or existing["revision"] != expected_revision:
                        raise HistoryConflict("养成历史已更新或删除，请刷新后重试")
                    if existing["mode"] != payload.mode:
                        raise UserDataValidationError("养成历史模式不得跨草稿更新")
                    if existing["content_sha256"] != payload.content_sha256:
                        cursor = connection.execute(
                            "UPDATE cultivation_history SET revision = revision + 1, last_calculated_at_utc = ?, "
                            "payload_version = ?, configuration_json = ?, result_snapshot_json = ?, "
                            "summary_json = ?, search_text = ?, content_sha256 = ? "
                            "WHERE history_id = ? AND revision = ?",
                            (_utc_now(), PAYLOAD_VERSION, payload.configuration_json, payload.result_snapshot_json,
                             payload.summary_json, search_text, payload.content_sha256, history_id, expected_revision),
                        )
                        if cursor.rowcount != 1:
                            raise HistoryConflict("养成历史保存绑定已失效")
                saved = connection.execute(
                    f"SELECT {_SUMMARY_COLUMNS} FROM cultivation_history WHERE history_id = ?", (history_id,),
                ).fetchone()
                check()
                return _summary(saved)
        except sqlite3.Error as error:
            raise UserDataError("养成历史保存失败，原记录保持不变") from error

    def list_cultivation_histories(
        self, account_id: str, *, mode: str | None = None, search: str = "",
        page: int = 1, page_size: int = 20, check: Callable[[], None] = lambda: None,
    ) -> HistoryPage:
        self._history_account(account_id, check)
        integer(page, 1, 2**31 - 1)
        integer(page_size, 1, 100)
        where, parameters = _predicate(mode, search)
        connection = self._db()
        _register_search(connection, check)
        try:
            with connection:
                connection.execute("BEGIN")
                total = connection.execute(
                    f"SELECT COUNT(*) FROM cultivation_history{where}", parameters,
                ).fetchone()[0]
                items = connection.execute(
                    f"SELECT {_SUMMARY_COLUMNS}, CASE WHEN length(CAST(configuration_json AS BLOB)) "
                    f"<= {MAX_PAYLOAD_BYTES} THEN configuration_json ELSE NULL END AS display_configuration_json "
                    f"FROM cultivation_history{where} "
                    "ORDER BY last_calculated_at_utc DESC, history_id DESC LIMIT ? OFFSET ?",
                    (*parameters, page_size, (page - 1) * page_size),
                ).fetchall()
                check()
                return HistoryPage(tuple(_summary(row) for row in items), total, page, page_size)
        except sqlite3.Error as error:
            raise UserDataError("养成历史列表读取失败") from error

    def get_cultivation_history(
        self, account_id: str, history_id: str, *, check: Callable[[], None] = lambda: None,
    ) -> HistoryRecord | None:
        self._history_account(account_id, check)
        text_value(history_id, maximum=128)
        try:
            row = self._one("SELECT * FROM cultivation_history WHERE history_id = ?", (history_id,))
            check()
            if row is None:
                return None
            if row["payload_version"] != PAYLOAD_VERSION:
                raise ValueError("养成历史格式版本暂未支持")
            payload = HistoryPayload.decode(row["configuration_json"], row["result_snapshot_json"])
            if (payload.content_sha256 != row["content_sha256"] or payload.mode != row["mode"]
                    or payload.summary_json != row["summary_json"]):
                raise ValueError("养成历史内容校验失败")
            return HistoryRecord(_summary(row), payload)
        except sqlite3.Error as error:
            raise UserDataError("养成历史详情读取失败") from error

    def select_cultivation_histories(
        self, account_id: str, *, mode: str | None = None, search: str = "",
        check: Callable[[], None] = lambda: None,
    ) -> tuple[HistorySelection, ...]:
        self._history_account(account_id, check)
        where, parameters = _predicate(mode, search)
        _register_search(self._db(), check)
        try:
            selected = self._rows(
                f"SELECT history_id, revision FROM cultivation_history{where} "
                "ORDER BY last_calculated_at_utc DESC, history_id DESC", parameters,
            )
            check()
            return tuple(HistorySelection(row["history_id"], row["revision"]) for row in selected)
        except sqlite3.Error as error:
            raise UserDataError("养成历史选择集合读取失败") from error

    def delete_cultivation_histories(
        self, account_id: str, selected: Sequence[HistorySelection], *,
        check: Callable[[], None] = lambda: None,
    ) -> int:
        self._history_account(account_id, check)
        selection = tuple(selected)
        seen: set[str] = set()
        for item in selection:
            text_value(item.history_id, maximum=128)
            integer(item.revision, 1)
            if item.history_id in seen:
                raise UserDataValidationError("养成历史删除集合包含重复身份")
            seen.add(item.history_id)
        if not selection:
            return 0
        connection = self._db()
        deleted = 0
        try:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                check()
                # Under the single write transaction no row can change between validation and delete.
                for offset in range(0, len(selection), 400):
                    chunk = selection[offset:offset + 400]
                    ids = tuple(item.history_id for item in chunk)
                    placeholders = ",".join("?" for _ in ids)
                    current = dict(connection.execute(
                        f"SELECT history_id, revision FROM cultivation_history WHERE history_id IN ({placeholders})",
                        ids,
                    ).fetchall())
                    if any(item.history_id in current and current[item.history_id] != item.revision for item in chunk):
                        raise HistoryConflict("选中历史已更新，本次删除未提交，请刷新后重新确认")
                    check()
                    cursor = connection.execute(
                        f"DELETE FROM cultivation_history WHERE history_id IN ({placeholders})", ids,
                    )
                    deleted += cursor.rowcount
                check()
            return deleted
        except sqlite3.Error as error:
            raise UserDataError("养成历史删除失败，本次删除已回滚") from error
