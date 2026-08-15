"""Store temporary game-state deltas above an immutable inventory snapshot."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .protocols import UserDataDaoMixinHost
from .user_data_support import UserDataValidationError, _decoded, _integer, _json, _utc_now


class InventoryRuntimeStateDaoMixin(UserDataDaoMixinHost):
    """Read/write per-item runtime state without changing snapshot membership."""

    def apply_inventory_runtime_state_delta(
        self,
        snapshot_id: int,
        items: Sequence[Mapping[str, Any]],
        *,
        observed_at_unix_ms: int | None = None,
        sequence: int | None = None,
    ) -> int:
        """Merge observed item state only when ``snapshot_id`` is still current."""

        frozen_snapshot_id = _integer(snapshot_id, "snapshot_id", minimum=1)
        if not items or self.current_inventory_snapshot_id() != frozen_snapshot_id:
            return 0
        observed = (
            _integer(observed_at_unix_ms, "observed_at_unix_ms", minimum=0)
            if observed_at_unix_ms is not None else None
        )
        event_sequence = (
            _integer(sequence, "sequence", minimum=0) if sequence is not None else None
        )
        base_rows = {
            (int(row["uid_serial"]), int(row["uid_slot"])): row
            for row in self.list_inventory_items(frozen_snapshot_id)
        }
        prepared: list[tuple[Any, ...]] = []
        for raw in items:
            uid = raw.get("uid")
            if not isinstance(uid, Mapping):
                continue
            try:
                serial = _integer(uid.get("serial"), "uid.serial", minimum=1)
                slot = _integer(uid.get("slot"), "uid.slot", minimum=1)
            except UserDataValidationError:
                continue
            base = base_rows.get((serial, slot))
            if base is None:
                continue
            equipped = raw.get("equipped")
            if not isinstance(equipped, bool):
                continue
            locked = raw.get("locked")
            discarded = raw.get("discarded")
            character_uid = raw.get("equipped_character_uid") if equipped else None
            placement = raw.get("equipped_placement") if equipped else None
            prepared.append((
                frozen_snapshot_id,
                serial,
                slot,
                int(locked) if isinstance(locked, bool) else int(base["locked"]),
                int(discarded) if isinstance(discarded, bool) else int(base["discarded"]),
                int(equipped),
                _json(dict(character_uid)) if isinstance(character_uid, Mapping) else None,
                raw.get("equipped_character_id") if equipped else None,
                _json(dict(placement)) if isinstance(placement, Mapping) else None,
                observed,
                event_sequence,
                _utc_now(),
            ))
        if not prepared:
            return 0
        connection = self._db()
        try:
            connection.executemany(
                """
                INSERT INTO inventory_item_runtime_state(
                    snapshot_id, uid_serial, uid_slot, locked, discarded, equipped,
                    equipped_character_uid_json, equipped_character_id,
                    equipped_placement_json, observed_at_unix_ms, sequence, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_id, uid_serial, uid_slot) DO UPDATE SET
                    locked = excluded.locked,
                    discarded = excluded.discarded,
                    equipped = excluded.equipped,
                    equipped_character_uid_json = excluded.equipped_character_uid_json,
                    equipped_character_id = excluded.equipped_character_id,
                    equipped_placement_json = excluded.equipped_placement_json,
                    observed_at_unix_ms = excluded.observed_at_unix_ms,
                    sequence = excluded.sequence,
                    updated_at_utc = excluded.updated_at_utc
                WHERE (
                    excluded.sequence IS NOT NULL
                    AND (
                        inventory_item_runtime_state.sequence IS NULL
                        OR excluded.sequence >= inventory_item_runtime_state.sequence
                    )
                ) OR (
                    excluded.sequence IS NULL
                    AND (
                        inventory_item_runtime_state.observed_at_unix_ms IS NULL
                        OR COALESCE(excluded.observed_at_unix_ms, -1)
                           >= inventory_item_runtime_state.observed_at_unix_ms
                    )
                )
                """,
                prepared,
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        return len(prepared)

    def list_inventory_items_with_runtime_state(self, snapshot_id: int) -> list[dict[str, Any]]:
        """Project the current snapshot with non-membership-changing state deltas."""

        frozen_snapshot_id = _integer(snapshot_id, "snapshot_id", minimum=1)
        rows = self.list_inventory_items(frozen_snapshot_id)
        if not rows:
            return rows
        overlays = {
            (int(row["uid_serial"]), int(row["uid_slot"])): row
            for row in self._rows(
                """
                SELECT uid_serial, uid_slot, locked, discarded, equipped,
                       equipped_character_uid_json, equipped_character_id,
                       equipped_placement_json
                FROM inventory_item_runtime_state
                WHERE snapshot_id = ?
                """,
                (frozen_snapshot_id,),
            )
        }
        for row in rows:
            overlay = overlays.get((int(row["uid_serial"]), int(row["uid_slot"])))
            if overlay is None:
                continue
            row["locked"] = bool(overlay["locked"])
            row["discarded"] = bool(overlay["discarded"])
            row["equipped"] = bool(overlay["equipped"])
            row["equipped_character_uid"] = _decoded(
                overlay["equipped_character_uid_json"], None
            )
            row["equipped_character_id"] = overlay["equipped_character_id"]
            row["equipped_placement"] = _decoded(
                overlay["equipped_placement_json"], None
            )
        return rows
