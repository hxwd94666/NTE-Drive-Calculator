# 按冻结快照解析角色配装，并将完整替换候选池与普通详情读取分离。
"""Saved equipment contexts retain source snapshots; replacement pools load on demand."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from src.domain.inventory.source_capabilities import is_visual_inventory_source
from src.storage.sqlite.user_data_dao import UserDataDao
from src.domain.equipment.virtual_equipment import virtual_equipment_inventory_item
from src.services.official_role.static_source import frozen_static_source
from src.domain.equipment.virtual_equipment import is_virtual_equipment_assignment


def _resolved_plan_items(
    user_dao: UserDataDao,
    plan: Mapping[str, Any] | None,
    *,
    snapshot_items: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Resolve a plan while reusing an already loaded source snapshot.

    The detail model needs both the saved assignments and the replacement pool.
    They are two views of one immutable snapshot, so issuing a second full
    ``list_inventory_items`` query only adds UI latency and can obscure the
    snapshot boundary during a concurrent sync.
    """

    if not plan or plan.get("source_snapshot_id") is None:
        return []
    items = (
        [dict(item) for item in snapshot_items]
        if snapshot_items is not None
        else user_dao.list_inventory_items(int(plan["source_snapshot_id"]))
    )
    by_uid = {(int(item["uid_serial"]), int(item["uid_slot"])): item for item in items}
    resolved = []
    for assignment in plan.get("assignments") or []:
        raw_assignment = dict(assignment.get("raw_assignment") or assignment)
        raw_assignment.update({
            "uid_serial": int(assignment["uid_serial"]),
            "uid_slot": int(assignment["uid_slot"]),
            "kind": assignment["kind"],
            "target_row": assignment.get("target_row"),
            "target_column": assignment.get("target_column"),
        })
        item = (
            virtual_equipment_inventory_item(raw_assignment)
            if is_virtual_equipment_assignment(raw_assignment)
            else by_uid.get(
                (int(assignment["uid_serial"]), int(assignment["uid_slot"]))
            )
        )
        if item is not None:
            row = dict(item)
            row["equipped"] = False
            row["equipped_character_id"] = None
            row["equipped_character_name"] = ""
            row.pop("equipped_character_icon_path", None)
            row["target_row"] = assignment.get("target_row")
            row["target_column"] = assignment.get("target_column")
            resolved.append(row)
    return resolved

def _assignment_uids(plan) -> tuple[tuple[int, int], ...]:
    return tuple((int(row["uid_serial"]), int(row["uid_slot"]))
                 for row in plan.get("assignments") or ()
                 if not is_virtual_equipment_assignment(row.get("raw_assignment") or row))


def _display_loadout_slot_name(
    character: Mapping[str, Any],
    slot: Mapping[str, Any],
) -> str:
    """Keep an unrenamed primary slot labeled with its character name."""

    slot_name = str(slot.get("slot_name") or "").strip()
    if (
        str(slot.get("slot_key") or "") == "primary"
        and slot_name == "主力"
    ):
        return str(character.get("name_zh") or slot_name)
    return slot_name


def _mark_equipment_level_known(
    items: Sequence[dict[str, Any]],
    snapshot_source: object,
) -> None:
    """Carry a snapshot's observable level capability into role-card views."""

    level_known = not is_visual_inventory_source(snapshot_source)
    for item in items:
        item["level_known"] = level_known


def load_role_inventory_contexts(user_dao, static_dao, catalog, character, character_id, cached,
                                 *, include_candidates: bool = True) -> dict[str, Any]:
    current_items: list[dict[str, Any]] = []
    saved_plan: Mapping[str, Any] | None = None
    saved_items: list[dict[str, Any]] = []
    replacement_items: list[dict[str, Any]] = []
    extra_saved_contexts: dict[str, dict[str, Any]] = {}
    selected_slot: Mapping[str, Any] | None = None
    selected_slot_name = ""
    current_snapshot_id = cached(
        "current_inventory_snapshot_id",
        user_dao.current_inventory_snapshot_id,
    )
    current_snapshot = (
        cached(
            ("inventory_snapshot_summary", int(current_snapshot_id)),
            lambda: user_dao.inventory_snapshot_summary(current_snapshot_id),
        )
        if current_snapshot_id is not None
        else None
    )
    current_items = (user_dao.list_inventory_items(current_snapshot_id, equipped=True, character_id=character_id)
                     if current_snapshot_id is not None else [])
    _mark_equipment_level_known(
        current_items,
        (current_snapshot or {}).get("source"),
    )
    slot_plans = [
        row
        for row in cached(
            "current_loadout_slot_plans",
            user_dao.list_current_loadout_slot_plans,
        )
        if int(row["slot"]["character_id"]) == character_id
    ]
    slot_plans.sort(
        key=lambda row: (
            str(row["slot"].get("slot_key") or "") != "primary",
            int(row["slot"].get("sort_order") or 0),
            int(row["slot"]["slot_id"]),
        )
    )
    selected_slot = slot_plans[0] if slot_plans else None
    saved_plan = selected_slot["plan"] if selected_slot is not None else None
    selected_slot_name = (
        _display_loadout_slot_name(character, selected_slot["slot"])
        if selected_slot is not None
        else ""
    )
    replacement_items = (
        [
            dict(item)
            for item in cached(
                (
                    "inventory_items",
                    int(saved_plan["source_snapshot_id"]), include_candidates,
                    None if include_candidates else _assignment_uids(saved_plan),
                ),
                lambda: user_dao.list_inventory_items(
                    int(saved_plan["source_snapshot_id"]),
                    uids=None if include_candidates else _assignment_uids(saved_plan),
                ),
            )
        ]
        if saved_plan and saved_plan.get("source_snapshot_id") is not None
        else []
    )
    saved_snapshot = (
        cached(
            (
                "inventory_snapshot_summary",
                int(saved_plan["source_snapshot_id"]),
            ),
            lambda: user_dao.inventory_snapshot_summary(
                int(saved_plan["source_snapshot_id"])
            ),
        )
        if saved_plan and saved_plan.get("source_snapshot_id") is not None
        else None
    )
    _mark_equipment_level_known(
        replacement_items,
        (saved_snapshot or {}).get("source"),
    )
    saved_items = _resolved_plan_items(
        user_dao,
        saved_plan,
        snapshot_items=replacement_items,
    )
    for row in slot_plans[1:]:
        slot = row["slot"]
        plan = row["plan"]
        source_snapshot_id = plan.get("source_snapshot_id")
        slot_items = (
            [
                dict(item)
                for item in cached(
                    ("inventory_items", int(source_snapshot_id), include_candidates,
                     None if include_candidates else _assignment_uids(plan)),
                    lambda: user_dao.list_inventory_items(
                        int(source_snapshot_id),
                        uids=None if include_candidates else _assignment_uids(plan),
                    ),
                )
            ]
            if source_snapshot_id is not None
            else []
        )
        slot_snapshot = (
            cached(
                ("inventory_snapshot_summary", int(source_snapshot_id)),
                lambda: user_dao.inventory_snapshot_summary(
                    int(source_snapshot_id)
                ),
            )
            if source_snapshot_id is not None
            else None
        )
        _mark_equipment_level_known(
            slot_items,
            (slot_snapshot or {}).get("source"),
        )
        slot_display_name = _display_loadout_slot_name(character, slot)
        extra_saved_contexts[f"saved:{slot['slot_id']}"] = {
            "title": f"已保存配装 · {slot_display_name}",
            "items": _resolved_plan_items(
                user_dao,
                plan,
                snapshot_items=slot_items,
            ),
            "calculation_items": (),
            "plan": plan,
            "replacement_items": slot_items if include_candidates else [],
            "slot_id": int(slot["slot_id"]),
            "slot_name": slot_display_name,
        }
    if include_candidates:
        pools = [replacement_items, *(context["replacement_items"] for context in extra_saved_contexts.values())]
        _annotate_candidate_pools(user_dao, static_dao, catalog, pools, cached)

    return {
        "current_items": current_items, "saved_plan": saved_plan, "saved_items": saved_items,
        "replacement_items": replacement_items if include_candidates else [],
        "extra_saved_contexts": extra_saved_contexts, "selected_slot": selected_slot,
        "selected_slot_name": selected_slot_name,
    }


def _annotate_candidate_pools(user_dao, static_dao, catalog, candidate_pools, cached) -> None:
    characters = cached(
        ("characters", str(static_dao.database_path)),
        lambda: {
            int(row["character_id"]): row
            for row in static_dao.list_characters()
        },
    )
    owner_by_uid: dict[tuple[int, int], int] = {}
    for row in cached(
        "current_loadout_equipment_owners",
        user_dao.list_current_loadout_equipment_owners,
    ):
        owner_by_uid.setdefault(
            (int(row["uid_slot"]), int(row["uid_serial"])),
            int(row["character_id"]),
        )
    locked_uids = {
        (int(row["uid_slot"]), int(row["uid_serial"]))
        for row in cached(
            "allocation_locked_equipment_owners",
            user_dao.list_allocation_locked_equipment_owners,
        )
    }
    for candidate_pool in candidate_pools:
        for item in candidate_pool:
            uid = (int(item["uid_slot"]), int(item["uid_serial"]))
            item["allocation_reserved"] = uid in locked_uids
            owner_id = owner_by_uid.get(uid)
            item["equipped"] = False
            item["equipped_character_id"] = None
            item["equipped_character_name"] = ""
            item.pop("equipped_character_icon_path", None)
            if owner_id is None:
                continue
            owner = characters.get(owner_id) or {}
            item["equipped"] = True
            item["equipped_character_id"] = owner_id
            item["equipped_character_name"] = str(
                owner.get("name_zh") or owner_id
            )
            owner_icon = catalog.character_icon(owner_id)
            if owner_icon is not None:
                item["equipped_character_icon_path"] = str(owner_icon)


def load_role_replacement_detail(
    user_database_path, static_database_path, asset_root, detail, context_key, *, static_schema_version,
    static_release, static_read_unit=None,
):
    """Read only the selected plan's frozen candidate snapshot, not current inventory."""
    from src.integrations.game_assets.catalog import GameUiAssetCatalog
    from src.integrations.static_catalog_release import static_catalog_read_unit
    from src.storage.sqlite.user_data_dao import UserDataDao
    from src.integrations.official_role.read_inputs import _asset_root

    # 候选沿用页面已冻结的库与 schema；缺项或不一致在任何读取之前失败。
    static_database_path, static_schema_version = frozen_static_source(
        detail, static_database_path=static_database_path, static_schema_version=static_schema_version,
        static_release=static_release,
    )
    context = (detail.get("equipment_contexts") or {}).get(context_key) or {}
    plan = context.get("plan") or {}
    snapshot_id = plan.get("source_snapshot_id")
    if snapshot_id is None:
        raise ValueError("所选配装缺少来源快照，请重新加载角色资料。")
    with (
        static_catalog_read_unit(static_database_path, expected_schema_version=static_schema_version,
                                 expected_release=static_release, read_unit=static_read_unit) as read_unit,
        UserDataDao(user_database_path) as user_dao,
    ):
        static_dao = read_unit.dao
        pool = user_dao.list_inventory_items(int(snapshot_id))
        snapshot = user_dao.inventory_snapshot_summary(int(snapshot_id)) or {}
        _mark_equipment_level_known(pool, snapshot.get("source"))
        _annotate_candidate_pools(user_dao, static_dao, GameUiAssetCatalog(_asset_root(asset_root)), [pool],
                                  lambda _key, factory: factory())
    return {
        **detail,
        "replacement_items": pool,
        "replacement_candidates_loaded": True,
        "equipment_contexts": {**detail["equipment_contexts"], context_key: {**context, "replacement_items": pool}},
    }
