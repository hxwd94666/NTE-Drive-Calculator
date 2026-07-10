# 从已保存配装矩阵提取驱动块坐标和棋盘相对位置。
"""Extract numbered drive blocks from equipped_state blueprint layouts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


EMPTY_CELLS = {"", "0", "0.0", "XX", "-1", "None", "none", "null"}


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
        for block_index, matrix_name in enumerate(_matrix_names_in_scan_order(board)):
            cells = _cells_for_name(board, matrix_name)
            drive_type = _drive_type_for(drives, block_index, matrix_name)
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
            if block_index < len(drives):
                block["drive"] = drives[block_index]
            blocks.append(block)
            next_id += 1
    return blocks


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
                "quality": str(tape.get("quality") or "Gold"),
                "tape": tape,
            }
        )
    return filters


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


def _drive_type_for(drives: list[dict[str, Any]], block_index: int, matrix_name: str) -> str:
    if block_index >= len(drives):
        return matrix_name
    drive = drives[block_index]
    for key in ("drive_type", "shape_id", "type"):
        value = drive.get(key)
        if value:
            return str(value)
    return matrix_name


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
