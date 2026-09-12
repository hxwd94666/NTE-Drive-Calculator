# 按原生数据域修订合并变化，同一已完成版本只复核状态，不定时重读全量。
from __future__ import annotations

from time import monotonic

from src.integrations.nte_core_protocol import NteCoreProtocolError


CHANGES_CAPABILITY = "snapshot.changes.v1"
CHANGE_SETTLE_SECONDS = 0.25


def domain_status(status, domain):
    if not isinstance(status, dict) or not isinstance(status.get("domains"), list):
        raise NteCoreProtocolError("原生变化状态格式无效。")
    rows = [row for row in status["domains"] if isinstance(row, dict) and row.get("domain") == domain]
    if len(rows) != 1:
        raise NteCoreProtocolError("原生变化状态缺少唯一的数据域。")
    return rows[0]


def snapshot_change_key(status, domain):
    row = domain_status(status, domain)
    revision = row.get("revision")
    provider = status.get("providerId")
    if (not isinstance(provider, str) or not provider
            or not isinstance(revision, str) or not revision.isascii() or not revision.isdecimal()
            or len(revision) > 20 or (len(revision) > 1 and revision.startswith("0"))
            or int(revision) > 18446744073709551615 or type(row.get("dirty")) is not bool
            or type(row.get("ready")) is not bool):
        raise NteCoreProtocolError("原生变化修订格式无效。")
    # A not-ready domain may have no root yet; its revision still invalidates old observations.
    key = row.get("domainKey", "")
    if key is None and row["ready"] is False:
        key = ""
    if not isinstance(key, str) or len(key) > 1024 or (row["ready"] and not key):
        raise NteCoreProtocolError("原生变化会话身份无效。")
    return provider, key, revision


class NativeSnapshotChanges:
    def __init__(self, *, clock=monotonic):
        self._clock = clock
        self._seen = {}
        self._accepted = {}

    def needs_refresh(self, status, domain):
        key, now = snapshot_change_key(status, domain), self._clock()
        if domain_status(status, domain)["ready"] is False:
            self._accepted.pop(domain, None)
        previous = self._seen.get(domain)
        if previous is None or previous[0] != key:
            self._seen[domain] = key, now
        accepted = self._accepted.get(domain)
        if accepted is None:
            return previous is None or now - self._seen[domain][1] >= CHANGE_SETTLE_SECONDS
        if key == accepted[0] and not domain_status(status, domain)["dirty"]:
            return False
        return now - self._seen[domain][1] >= CHANGE_SETTLE_SECONDS

    def accept(self, status, domain):
        key, now = snapshot_change_key(status, domain), self._clock()
        self._seen[domain] = key, now
        self._accepted[domain] = key, now

    def is_current(self, status, domain):
        accepted = self._accepted.get(domain)
        return (accepted is not None and snapshot_change_key(status, domain) == accepted[0]
                and domain_status(status, domain)["ready"] is True
                and not domain_status(status, domain)["dirty"])
