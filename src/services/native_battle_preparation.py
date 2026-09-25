# 以局部场景与配置修订触发战报快照预备，缓存仅供首击证据核验。
from copy import deepcopy
from time import monotonic

from src.services.native_battle_scopes import team_configuration_unchanged, validate_first_hit
from src.services.battle_capture_build_context import PANEL_DOMAINS


def first_hit_snapshot_ready(snapshot, attempt, record):
    """Only a complete read with matching first-hit evidence can become frozen."""
    return (snapshot.get("state") == "observed" and {"character", "inventory", "team"} <= (snapshot.get("domains") or {}).keys()
            and "inventory_projection" in snapshot and "character_projection" in snapshot
            and validate_first_hit(snapshot, attempt, record) is None)


def retain_scope_pending_snapshot(previous, candidate, attempt, record):
    """A later half's read cannot replace this half's unresolved observations."""
    def matches_first_domain(snapshot, domain):
        actual = (snapshot.get("domains") or {}).get(domain) or {}
        reference = (attempt.get("firstSnapshotRefs") or {}).get(domain) or {}
        expected = reference or (attempt.get("firstChanges") or {}).get(domain) or {}
        return (bool(expected.get("revision")) and actual.get("revision") == expected["revision"]
                and actual.get("providerId") == (record.get("native_capture") or {}).get("providerId")
                and (not reference or all(actual.get(k) == reference.get(k)
                                         for k in ("providerId", "domain", "domainKey"))))

    if (previous and previous.get("state") == "observed"
            and "character_projection" in previous and "inventory_projection" in previous
            and ((any(matches_first_domain(previous, domain) and not matches_first_domain(candidate, domain)
                     for domain in ("character", "inventory", "team")))
                 or (team_configuration_unchanged(previous, attempt, record)
                     and not team_configuration_unchanged(candidate, attempt, record)))):
        return deepcopy(previous)
    return candidate


class NativeBattlePreparation:
    def __init__(self):
        self.key = None
        self.snapshot = None
        self.retry_at = 0.0
        self.prior_character = None

    def prepare(self, record, attempts, read):
        # Prepare only while the lease still needs evidence for a scope.
        # Completed scopes retain their independent immutable snapshots.
        native = record.get("native_capture") or {}
        context = next((row for row in reversed(native.get("contextEvents") or [])
                        if row.get("kind") == "combat_context"), None)
        if context is None or not all((context.get("roots") or {}).get(k) for k in ("world", "controller", "ps")):
            return
        key = (native.get("providerId"), deepcopy(context.get("roots")),
               {domain: deepcopy((context.get("snapshotChanges") or {}).get(domain))
                for domain in (*PANEL_DOMAINS, "environment")},
               deepcopy(context.get("environment")), deepcopy(native.get("cloneAttempt")))
        if key == self.key and (self.snapshot is not None or monotonic() < self.retry_at):
            return
        previous = (self.snapshot or {}).get("domains", {}).get("character") or self.prior_character
        self.prior_character = previous
        self.key, self.snapshot = key, None
        frozen = read()
        self.retry_at = monotonic() + 2.0
        domains = frozen.get("domains") or {}
        if (frozen.get("state") == "observed" and {"character", "inventory", "team"} <= domains.keys()
                and "inventory_projection" in frozen and "character_projection" in frozen):
            if previous and previous.get("revision") != domains["character"].get("revision"):
                frozen["prior_character_observation"] = deepcopy(previous)
            self.snapshot = frozen
        return frozen

    def take_matching(self, attempt, record):
        if self.snapshot is not None and first_hit_snapshot_ready(self.snapshot, attempt, record):
            return deepcopy(self.snapshot)
        return None


def observe_pinned_scopes(lease, record, attempts, final, stop_requested):
    from copy import deepcopy
    changed = False
    for scope in tuple(lease._scope_snapshots):
        if scope not in attempts or lease._scope_snapshots[scope]["attempt_id"] != attempts[scope]["attemptId"]:
            del lease._scope_snapshots[scope]
            changed = True
    for scope, attempt in attempts.items():
        if scope in lease._scope_snapshots:
            continue
        frozen = lease._read_battle_snapshot(stop_requested, references=attempt.get("firstSnapshotRefs") or {})
        if not final and frozen.get("state") == "unavailable":
            continue
        lease._scope_snapshots[scope] = {"attempt_id": attempt["attemptId"], "snapshot": frozen}
        changed = True
    # Warm the live cache for the next first hit. Its bytes never replace a
    # pinned attempt, and completion of this read is not a capture prerequisite.
    if not final:
        lease._preparation.prepare(record, attempts, lambda: lease._read_battle_snapshot(stop_requested))
    return {"state": "scoped", "scopes": deepcopy(lease._scope_snapshots)} if changed else None
