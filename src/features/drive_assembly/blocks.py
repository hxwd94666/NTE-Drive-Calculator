# 从已保存配装矩阵提取驱动块坐标和棋盘相对位置。
"""Extract numbered drive blocks from equipped_state blueprint layouts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.features.drive_assembly.duplicate_marker import mark_duplicate_drive_blocks, mark_duplicate_tape_filters


EMPTY_CELLS = {"", "0", "0.0", "XX", "-1", "None", "none", "null"}
SHAPE_FOOTPRINTS: dict[str, tuple[tuple[int, int], ...]] = {
    "H_2": ((0, 0), (0, 1)),
    "V_2": ((0, 0), (1, 0)),
    "H_3": ((0, 0), (0, 1), (0, 2)),
    "V_3": ((0, 0), (1, 0), (2, 0)),
    "L_3_TL": ((0, 0), (0, 1), (1, 0)),
    "L_3_TR": ((0, 0), (0, 1), (1, 1)),
    "L_3_BL": ((0, 0), (1, 0), (1, 1)),
    "L_3_BR": ((0, 1), (1, 0), (1, 1)),
    "H_4": ((0, 0), (0, 1), (0, 2), (0, 3)),
    "V_4": ((0, 0), (1, 0), (2, 0), (3, 0)),
    "Trap_4_H": ((0, 1), (0, 2), (1, 0), (1, 1)),
    "Trap_4_V": ((0, 1), (1, 0), (1, 1), (2, 0)),
}


def load_drive_blocks(equipped_state_path: str | Path) -> list[dict[str, Any]]:
    """Read an equipped_state JSON file and return extracted drive blocks."""

    path = Path(equipped_state_path)
    with open(path, "r", encoding="utf-8") as file:
        state = json.load(file)
    return extract_drive_blocks_from_state(state)


def load_tape_filters(equipped_state_path: str | Path) -> list[dict[str, Any]]:
    """Read an equipped_state JSON file and return tape filter requirements."""

    path = Path(equipped_state_path)
    with open(path, "r", encoding="utf-8") as file:
        state = json.load(file)
    return extract_tape_filters_from_state(state)


def extract_drive_blocks_from_state(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return numbered drive blocks from saved role blueprint layouts.

    Coordinates are 1-based with the board's top-left cell represented as
    ``(1, 1)``. The left/up counts are board-relative occupied-cell counts for
    the block anchor cell, not counts within the block itself.
    """

    blocks: list[dict[str, Any]] = []
    next_id = 1
    for role_name, role_state in (state or {}).items():
        if not isinstance(role_state, dict):
            continue
        board = _normalized_board(role_state.get("blueprint_layout"))
        if not board:
            continue
        drives = [drive for drive in role_state.get("equipped_drives", []) or [] if isinstance(drive, dict)]
        matrix_groups = _matrix_groups_in_scan_order(board)
        matrix_names = [matrix_name for matrix_name, _cells in matrix_groups]
        matched_drives = _match_drives_to_matrix_names(drives, matrix_names)
        for block_index, (matrix_name, cells) in enumerate(matrix_groups):
            drive = matched_drives[block_index]
            if _is_empty_equipped_drive(drive):
                # 优化替换留下的空位保存在配装页供后续填充，但不应进入游戏内自动装配。
                continue
            drive_type = _drive_type_for(drive, matrix_name)
            top_left = _top_left_for(cells, drive_type)
            block = {
                "block_id": next_id,
                "role_name": role_name,
                "blueprint_role_name": role_name,
                "matrix_name": matrix_name,
                "drive_type": drive_type,
                "cells": cells,
                "top_left": top_left,
                "left_count": _occupied_left_count(board, top_left),
                "up_count": _occupied_up_count(board, top_left),
            }
            if drive is not None:
                block["drive"] = drive
            blocks.append(block)
            next_id += 1
    return mark_duplicate_drive_blocks(blocks)


def _is_empty_equipped_drive(drive: dict[str, Any] | None) -> bool:
    return isinstance(drive, dict) and str(drive.get("uid") or "").startswith("empty_")


def extract_tape_filters_from_state(state: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Return tape set/main-stat/quality filters from saved role blueprints."""

    filters: list[dict[str, Any]] = []
    for role_name, role_state in (state or {}).items():
        if not isinstance(role_state, dict):
            continue
        tape = role_state.get("equipped_tape") or role_state.get("tape")
        if not isinstance(tape, dict):
            continue
        set_name = str(tape.get("set_name") or tape.get("display_name") or "").strip()
        main_stat = _tape_main_stat_name(tape.get("main_stats"))
        if not set_name or not main_stat:
            continue
        filters.append(
            {
                "role_name": role_name,
                "blueprint_role_name": role_name,
                "set_name": set_name,
                "main_stat": main_stat,
                "sub_stats": _tape_sub_stat_names(tape.get("sub_stats")),
                "quality": str(tape.get("quality") or "").strip(),
                "tape": tape,
            }
        )
    return mark_duplicate_tape_filters(filters)


def _tape_main_stat_name(main_stats: Any) -> str:
    if isinstance(main_stats, dict):
        return str(next(iter(main_stats.keys()), "")).strip()
    return str(main_stats or "").strip()


def _tape_sub_stat_names(sub_stats: Any) -> list[str]:
    if isinstance(sub_stats, dict):
        return [str(name).strip() for name in sub_stats.keys() if str(name).strip()]
    if isinstance(sub_stats, list):
        return [str(name).strip() for name in sub_stats if str(name).strip()]
    return []


def _normalized_board(raw_board: Any) -> list[list[str]]:
    if not isinstance(raw_board, list):
        return []
    board: list[list[str]] = []
    for row in raw_board:
        if not isinstance(row, list):
            continue
        board.append([_cell_name(cell) for cell in row])
    return board


def _cell_name(cell: Any) -> str:
    if cell == -1:
        return "XX"
    if cell == 0:
        return "0"
    return str(cell)


def _is_occupied(cell: str) -> bool:
    return str(cell) not in EMPTY_CELLS


def _drive_type_for(drive: dict[str, Any] | None, matrix_name: str) -> str:
    if not isinstance(drive, dict):
        return matrix_name
    for key in ("drive_type", "shape_id", "type"):
        value = drive.get(key)
        if value:
            return str(value)
    return matrix_name


def _match_drives_to_matrix_names(
    drives: list[dict[str, Any]],
    matrix_names: list[str],
) -> list[dict[str, Any] | None]:
    """Match saved drive details to blueprint groups by shape before positional fallback."""

    unmatched_indexes = set(range(len(drives)))
    matches: list[dict[str, Any] | None] = [None] * len(matrix_names)

    for matrix_index, matrix_name in enumerate(matrix_names):
        target = _normalize_shape_id(matrix_name)
        for drive_index, drive in enumerate(drives):
            if drive_index not in unmatched_indexes:
                continue
            if _normalize_shape_id(_drive_type_for(drive, "")) == target:
                matches[matrix_index] = drive
                unmatched_indexes.remove(drive_index)
                break

    remaining_drives = (drives[index] for index in range(len(drives)) if index in unmatched_indexes)
    for matrix_index, match in enumerate(matches):
        if match is None:
            matches[matrix_index] = next(remaining_drives, None)
    return matches


def _normalize_shape_id(value: Any) -> str:
    return str(value or "").strip().upper()


def _top_left_for(cells: list[tuple[int, int]], drive_type: str) -> tuple[int, int]:
    if _uses_empty_top_left_anchor(drive_type):
        return (min(row for row, _col in cells), min(col for _row, col in cells))
    return min(cells, key=lambda cell: (cell[0], cell[1]))


def _uses_empty_top_left_anchor(drive_type: str) -> bool:
    normalized = drive_type.upper()
    return normalized in {"V", "H"} or normalized.endswith("_V") or normalized.endswith("_H")


def _matrix_names_in_scan_order(board: list[list[str]]) -> list[str]:
    seen = set()
    names: list[str] = []
    for row in board:
        for cell in row:
            if not _is_occupied(cell) or cell in seen:
                continue
            seen.add(cell)
            names.append(cell)
    return names


def _matrix_groups_in_scan_order(board: list[list[str]]) -> list[tuple[str, list[tuple[int, int]]]]:
    """Return independently placeable same-name shape groups in scan order.

    Blueprint cells use a shape id as their display value.  A role can contain
    multiple copies of the same shape, so collecting every matching cell under
    one id produces a false centroid between those copies.  Known shape
    footprints are therefore partitioned with an exact cover before blocks are
    matched to their saved drive records.
    """

    groups: list[tuple[str, list[tuple[int, int]]]] = []
    for matrix_name in _matrix_names_in_scan_order(board):
        cells = _cells_for_name(board, matrix_name)
        split_cells = _split_cells_by_shape_footprint(board, matrix_name, cells)
        groups.extend((matrix_name, group_cells) for group_cells in split_cells)
    return groups


def _split_cells_by_shape_footprint(
    board: list[list[str]],
    matrix_name: str,
    cells: list[tuple[int, int]],
) -> list[list[tuple[int, int]]]:
    footprint = SHAPE_FOOTPRINTS.get(_normalize_shape_id(matrix_name))
    if not footprint or len(cells) % len(footprint):
        return [cells]

    remaining = set(cells)
    candidates = _shape_placement_candidates(board, matrix_name, footprint, remaining)
    placements_by_cell: dict[tuple[int, int], list[frozenset[tuple[int, int]]]] = {
        cell: [] for cell in remaining
    }
    for placement in candidates:
        for cell in placement:
            placements_by_cell[cell].append(placement)

    solution = _exact_shape_cover(remaining, placements_by_cell)
    if solution is None:
        return [cells]
    return [sorted(placement) for placement in sorted(solution, key=lambda group: min(group))]


def _shape_placement_candidates(
    board: list[list[str]],
    matrix_name: str,
    footprint: tuple[tuple[int, int], ...],
    cells: set[tuple[int, int]],
) -> list[frozenset[tuple[int, int]]]:
    candidates: list[frozenset[tuple[int, int]]] = []
    for row in range(1, len(board) + 1):
        for col in range(1, max((len(line) for line in board), default=0) + 1):
            placement = frozenset((row + row_offset, col + col_offset) for row_offset, col_offset in footprint)
            if placement <= cells:
                candidates.append(placement)
    return candidates


def _exact_shape_cover(
    remaining: set[tuple[int, int]],
    placements_by_cell: dict[tuple[int, int], list[frozenset[tuple[int, int]]]],
) -> list[frozenset[tuple[int, int]]] | None:
    if not remaining:
        return []
    pivot = min(remaining)
    for placement in placements_by_cell[pivot]:
        if not placement <= remaining:
            continue
        rest = _exact_shape_cover(remaining - placement, placements_by_cell)
        if rest is not None:
            return [placement, *rest]
    return None


def _cells_for_name(board: list[list[str]], matrix_name: str) -> list[tuple[int, int]]:
    cells: list[tuple[int, int]] = []
    for row_index, row in enumerate(board, start=1):
        for col_index, cell in enumerate(row, start=1):
            if cell == matrix_name:
                cells.append((row_index, col_index))
    return cells


def _occupied_left_count(board: list[list[str]], top_left: tuple[int, int]) -> int:
    row, col = top_left
    if row < 1 or row > len(board):
        return 0
    return sum(1 for cell in board[row - 1][: col - 1] if _is_occupied(cell))


def _occupied_up_count(board: list[list[str]], top_left: tuple[int, int]) -> int:
    row, col = top_left
    count = 0
    for board_row in board[: row - 1]:
        if col - 1 < len(board_row) and _is_occupied(board_row[col - 1]):
            count += 1
    return count
