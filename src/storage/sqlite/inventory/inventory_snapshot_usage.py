# 在本次进程内保护已导出的计算输入，避免后台清理删掉尚未保存的方案来源。
from functools import wraps
from contextlib import closing
from pathlib import Path
import sqlite3
from threading import RLock

from src.storage.sqlite.user_data_support import UserDataError, UserDataValidationError, _integer


_lock = RLock()
_exported: dict[str, set[int]] = {}


def _key(database_path):
    return str(Path(database_path).resolve()).casefold()


def exported_snapshot_ids(database_path):
    with _lock:
        return frozenset(_exported.get(_key(database_path), ()))


def protect_exported_snapshot(method):
    @wraps(method)
    def export(owner, snapshot_id):
        # 导出与清理各自管理事务，先取得同一进程锁再 BEGIN。
        # 只有成功导出才登记保护；清理看到登记之后才决定删除范围。
        with _lock:
            selected_id = _integer(snapshot_id, "snapshot_id", minimum=1)
            # 调用方可能早已建立 WAL 读视图，旧视图里的“存在”不证明来源仍可保存。
            # 锁内以独立只读连接复核最新提交；不提交/回滚调用方事务，也不换成最新库存。
            uri = Path(owner.database_path).resolve().as_uri() + "?mode=ro"
            try:
                with closing(sqlite3.connect(uri, uri=True)) as committed:
                    exists = committed.execute(
                        "SELECT 1 FROM inventory_snapshot WHERE snapshot_id = ?", (selected_id,),
                    ).fetchone()
            except sqlite3.Error as exc:
                raise UserDataError("无法核对已提交的背包快照来源") from exc
            if exists is None:
                raise UserDataValidationError(f"背包快照已清理或尚未提交：{selected_id}")
            result = method(owner, snapshot_id)
            _exported.setdefault(_key(owner.database_path), set()).add(selected_id)
            return result
    return export


def serialize_snapshot_prune(method):
    @wraps(method)
    def prune(owner, **kwargs):
        with _lock:
            return method(owner, **kwargs)
    return prune
