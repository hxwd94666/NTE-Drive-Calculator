# 以局部场景与配置修订触发战报快照预备，缓存仅供首击证据核验。
from copy import deepcopy
from time import monotonic

from src.services.native_battle_scopes import validate_first_hit


class NativeBattlePreparation:
    def __init__(self):
        self.key = None
        self.snapshot = None
        self.retry_at = 0.0

    def prepare(self, record, attempts, read):
        # Maintain the shared live baseline even after the current scope has frozen.
        # The lease retains its independent immutable scope snapshots.
        native = record.get("native_capture") or {}
        context = next((row for row in reversed(native.get("contextEvents") or [])
                        if row.get("kind") == "combat_context"), None)
        if context is None or not all((context.get("roots") or {}).get(k) for k in ("world", "controller", "ps")):
            return
        key = (native.get("providerId"), deepcopy(context.get("roots")),
               deepcopy(context.get("snapshotChanges")), deepcopy(native.get("cloneAttempt")))
        if key == self.key and (self.snapshot is not None or monotonic() < self.retry_at):
            return
        self.key, self.snapshot = key, None
        frozen = read()
        self.retry_at = monotonic() + 2.0
        domains = frozen.get("domains") or {}
        if (frozen.get("state") == "observed" and len(domains) == 4
                and "inventory_projection" in frozen and "character_projection" in frozen):
            self.snapshot = frozen
        return frozen

    def take_matching(self, attempt, record):
        if self.snapshot is not None and validate_first_hit(self.snapshot, attempt, record) is None:
            return deepcopy(self.snapshot)
        return None
