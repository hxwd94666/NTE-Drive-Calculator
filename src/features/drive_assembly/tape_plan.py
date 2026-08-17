# 构建游戏界面自动装配的卡带筛选与装备动作序列。
"""Tape-filter action planning for game-page automatic assembly."""

from __future__ import annotations

from typing import Any

from src.features.drive_assembly.page_mapping import (
    map_filter_reset,
    map_page_controls,
    map_tape_equip_first_result,
    map_tape_filter_controls,
    map_tape_filter_refinement,
    map_tape_main_stat_mouse_open,
    map_tape_main_stat_selection,
    map_tape_set_selection,
    map_tape_sub_stat_filter_entry,
    map_tape_sub_stat_selection,
)


def tape_install_sequence(
    tape_filter: dict[str, Any],
    screen_size: tuple[int, int] | None,
    content_rect: tuple[int, int, int, int] | None,
) -> list[dict[str, Any]]:
    sequence: list[dict[str, Any]] = []
    sequence.extend(map_page_controls(screen_size, content_rect)["click_sequence"])
    sequence.extend(map_filter_reset(screen_size, content_rect)["reset_sequence"])
    sequence.extend(map_tape_filter_controls(screen_size, content_rect)["set_filter_sequence"])
    sequence.extend(map_tape_set_selection(tape_filter["set_name"], screen_size, content_rect)["selection_sequence"])
    quality = str(tape_filter.get("quality") or "").strip()
    sequence.extend(
        map_tape_filter_refinement(
            [quality] if quality else [],
            screen_size,
            content_rect,
            include_main_stat_expand=False,
            include_status_filters=_is_duplicate_tape_filter(tape_filter),
        )["refinement_sequence"]
    )
    sequence.extend(map_tape_main_stat_mouse_open(screen_size, content_rect)["open_sequence"])
    main_stat_selection = map_tape_main_stat_selection(tape_filter["main_stat"], screen_size, content_rect)
    sequence.extend(main_stat_selection["selection_sequence"])
    sequence.extend(map_tape_sub_stat_filter_entry(screen_size, content_rect)["entry_sequence"])
    sequence.extend(
        map_tape_sub_stat_selection(tape_filter.get("sub_stats", []), screen_size, content_rect)["selection_sequence"]
    )
    sequence.extend(map_tape_equip_first_result(screen_size, content_rect)["equip_sequence"])
    return sequence


def _is_duplicate_tape_filter(tape_filter: dict[str, Any]) -> bool:
    raw_tape = tape_filter.get("tape")
    tape: dict[str, Any] = raw_tape if isinstance(raw_tape, dict) else {}
    return bool(
        tape_filter.get("is_duplicate_tape")
        or tape_filter.get("is_duplicate_equipment")
        or tape.get("is_duplicate_tape")
        or tape.get("is_duplicate_equipment")
        or int(tape_filter.get("duplicate_count") or 0) > 1
        or int(tape.get("duplicate_count") or 0) > 1
    )
