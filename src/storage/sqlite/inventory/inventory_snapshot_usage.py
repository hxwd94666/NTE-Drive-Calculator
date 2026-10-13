# 在本次进程内保护已导出的计算输入，避免后台清理删掉尚未保存的方案来源。
from functools import wraps
from pathlib import Path
from threading import RLock


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
            result = method(owner, snapshot_id)
            _exported.setdefault(_key(owner.database_path), set()).add(int(snapshot_id))
            return result
    return export


def serialize_snapshot_prune(method):
    @wraps(method)
    def prune(owner, **kwargs):
        with _lock:
            return method(owner, **kwargs)
    return prune
