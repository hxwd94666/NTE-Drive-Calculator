# 批量装配必须显式勾选角色，每个角色只允许选择一个已保存槽位。
from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QMessageBox
from src.ui.common.text_catalog import resolve_text_catalog
from src.domain.equipment.loadouts.loadout_plan_scores import persisted_loadout_total_score

from src.features.inventory.equipment.management.batch_selection_dialog import BatchChoice, choose_batch_items
from src.features.inventory.equipment.assembly.assembly_surface import release_assembly_surface


def assembly_account_identity(parent):
    context = getattr(parent, "app_context", None)
    account = getattr(context, "account", None)
    return (getattr(context, "generation", None), getattr(account, "active_account_id", None),
            str(getattr(account, "user_database_path", getattr(parent, "user_database_path", ""))))


def select_assembly_slot_ids(parent: Any, selections: tuple[Any, ...], *, native=False, selected_slot_ids=()) -> list[int] | None:
    text = resolve_text_catalog(getattr(parent, "text_catalog", None)).render
    by_character: dict[int, list[Any]] = {}
    for selection in selections:
        by_character.setdefault(int(selection.character_id), []).append(selection)
    if not by_character:
        QMessageBox.information(parent, text("assembly.select_title"), text("assembly.no_saved_slots"))
        return None
    choices = []
    for character_id, slots in by_character.items():
        default = next((row for row in slots if row.slot_key == "primary"), slots[0])
        choices.append(BatchChoice(character_id, str(default.role_name),
            options=tuple((int(slot.slot_id), str(slot.slot_name)) for slot in slots),
            default_id=int(default.slot_id), character_id=character_id,
            option_scores=tuple((int(slot.slot_id), persisted_loadout_total_score(slot.plan)) for slot in slots)))
    identity = assembly_account_identity(parent)
    release_assembly_surface(parent)
    selected = choose_batch_items(parent, choices, title=text("assembly.fast.title" if native else "selector.choose_title"),
        description="" if native else text("selector.choose_description"),
        accept_text=text("assembly.fast.start" if native else "common.next"), retain=native, show_hints=not native,
        initial_ids=tuple(selected_slot_ids))
    # 模态选择期间迟到的账号切换回调也会使这次选择失效，不将槽位ID用于新账号。
    return selected if assembly_account_identity(parent) == identity else None
