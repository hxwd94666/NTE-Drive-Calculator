# 按提供方、对象域和修订保存有界当前基线，复用只读数据而不改变已冻结战报。
from copy import deepcopy
import json

from src.integrations.nte_core_protocol import NteCoreProtocolError


class NativeSnapshotBaseline:
    MAX_BYTES = 64 * 1024 * 1024

    def __init__(self):
        self._entries = {}
        self._sizes = {}

    def get(self, domain, status):
        if (not isinstance(status, dict) or not isinstance(status.get("domains"), list)
                or any(not isinstance(row, dict) for row in status["domains"])):
            raise NteCoreProtocolError("原生基线复核状态格式无效。")
        entry = self._entries.get(domain)
        if entry is None:
            return None
        rows = [row for row in status.get("domains", []) if row.get("domain") == domain]
        if len(rows) != 1:
            return None
        current, snapshot = rows[0], entry["snapshot"]
        if (status.get("providerId") != snapshot.get("providerId")
                or current.get("dirty") is not False or current.get("ready") is not True
                or current.get("enabled") is not True
                or any(not snapshot.get(key) or current.get(key) != snapshot[key] for key in ("domainKey", "revision"))):
            return None
        return deepcopy(entry)

    def put(self, domain, snapshot, projection=None):
        if not snapshot.get("revision") or snapshot.get("dirty") is not False:
            return
        entry = {"snapshot": snapshot, "projection": projection}
        size = len(json.dumps(entry, ensure_ascii=False).encode("utf-8"))
        if size > self.MAX_BYTES:
            return
        self._entries.pop(domain, None)
        self._sizes.pop(domain, None)
        while self._entries and sum(self._sizes.values()) + size > self.MAX_BYTES:
            oldest = next(iter(self._entries))
            self._entries.pop(oldest)
            self._sizes.pop(oldest)
        self._entries[domain] = deepcopy(entry)
        self._sizes[domain] = size
