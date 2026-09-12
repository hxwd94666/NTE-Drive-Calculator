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
        # Export and prune own their SQLite transactions and acquire this lock
        # before BEGIN. The export must finish and register before pruning reads
        # the protected IDs; a failed export never registers a snapshot.
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
