# 使用只读官方静态数据生成角色图纸候选与盘面。
"""Pure local blueprint generation over the static-data DAO contract."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from pathlib import Path

from src.models.equipment import DriveShape
from src.solver.blueprint_utils import dedupe_blueprints_by_piece_signature
from src.solver.combinatorics import PuzzleCombinatorics
from src.solver.dfs_puzzle import DFSPuzzleSolver
from src.services.character_shape_bonus_service import get_effective_character_shape_bonus
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao


OFFICIAL_SHAPE_LABELS = {
    "EquipmentGeometry_Hen2": "H_2",
    "EquipmentGeometry_Hen3": "H_3",
    "EquipmentGeometry_Hen4": "H_4",
    "EquipmentGeometry_Shu2": "V_2",
    "EquipmentGeometry_Shu3": "V_3",
    "EquipmentGeometry_Shu4": "V_4",
    "EquipmentGeometry_Z3": "Trap_4_H",
    "EquipmentGeometry_Z4": "Trap_4_V",
    "EquipmentGeometry_ZhiJiao1": "L_3_BL",
    "EquipmentGeometry_ZhiJiao2": "L_3_TL",
    "EquipmentGeometry_ZhiJiao3": "L_3_TR",
    "EquipmentGeometry_ZhiJiao4": "L_3_BR",
}


def official_shape_matrix(shape: dict) -> list[list[int]]:
    cells = list(shape.get("cells") or [])
    if not cells:
        raise ValueError(
            f"官方形状 {shape.get('shape_id') or '未知'} 未提供格子数据"
        )
    rows = [int(cell["x"]) for cell in cells]
    columns = [int(cell["y"]) for cell in cells]
    min_row, min_column = min(rows), min(columns)
    matrix = [
        [0] * (max(columns) - min_column + 1)
        for _ in range(max(rows) - min_row + 1)
    ]
    for cell in cells:
        matrix[int(cell["x"]) - min_row][int(cell["y"]) - min_column] = 1
    return matrix


def _official_shape_models(
    shapes: list[dict], module_items: list[dict]
) -> dict[str, DriveShape]:
    names_by_geometry: dict[str, str] = {}
    for item in module_items:
        geometry_id = str(item.get("geometry_id") or "")
        if geometry_id and geometry_id not in names_by_geometry:
            names_by_geometry[geometry_id] = str(
                item.get("name_zh") or geometry_id
            )
    return {
        str(shape["shape_id"]): DriveShape(
            shape_id=str(shape["shape_id"]),
            label=names_by_geometry.get(
                str(shape["shape_id"]), str(shape["shape_id"])
            ),
            matrix=official_shape_matrix(shape),
            area=int(shape["cell_count"]),
            description=str(shape["shape_id"]),
        )
        for shape in shapes
    }


def official_board(plan: dict) -> list[list[int]]:
    board = [[-1] * 5 for _ in range(5)]
    for cell in plan.get("cells") or []:
        row, column = int(cell["row"]) - 1, int(cell["column"]) - 1
        if 0 <= row < 5 and 0 <= column < 5:
            board[row][column] = 0
    playable = sum(value == 0 for row in board for value in row)
    if playable != 20:
        raise ValueError(
            f"{plan.get('character_name_zh') or plan.get('character_id')} "
            f"的官方盘面应为 20 格，实际为 {playable} 格"
        )
    return board


def custom_board(cells: Iterable[dict]) -> list[list[int]]:
    """Map an account-owned 5×5 board to the solver representation."""

    board = [[-1] * 5 for _ in range(5)]
    enabled_count = 0
    for cell in cells:
        row = int(cell.get("row_number", cell.get("row", 0))) - 1
        column = int(cell.get("column_number", cell.get("column", 0))) - 1
        if not (0 <= row < 5 and 0 <= column < 5):
            continue
        if bool(cell.get("is_enabled")):
            board[row][column] = 0
            enabled_count += 1
    if enabled_count != 20:
        raise ValueError(f"自建角色底盘应为 20 格，实际为 {enabled_count} 格")
    return board


def _solve_board_candidates(
    *,
    board: list[list[int]],
    suit_shape_ids: list[str],
    preferred_label: str,
    combinatorics: PuzzleCombinatorics,
    solver: DFSPuzzleSolver,
) -> list[dict]:
    candidates: list[dict] = []
    for extra_piece_ids in combinatorics.generate_piece_combinations(
        suit_shape_ids, preferred_label
    ):
        solved_boards: list[list[list[object]]] = []
        solver.solve(
            board,
            suit_shape_ids + extra_piece_ids,
            solved_boards,
            max_solutions=1,
        )
        if solved_boards:
            candidates.append(
                {
                    "set_pieces": list(suit_shape_ids),
                    "extra_pieces": list(extra_piece_ids),
                    "board": _display_board(solved_boards[0]),
                }
            )
    return dedupe_blueprints_by_piece_signature(candidates)


def _preferred_extra_label(
    plan: dict,
    item_by_id: dict[str, dict],
    suit_shape_ids: list[str],
    shape_models: dict[str, DriveShape],
) -> str:
    recommended = [
        str(item_by_id[item_id].get("geometry_id") or "")
        for item_id in plan.get("module_item_ids") or []
        if item_id in item_by_id
    ]
    remaining = Counter(recommended)
    for shape_id in suit_shape_ids:
        remaining[str(shape_id)] -= 1
    candidates = [
        shape_id
        for shape_id, count in remaining.items()
        if count > 0 and shape_id in shape_models
    ]
    if not candidates:
        return ""
    preferred_shape_id = max(
        candidates, key=lambda shape_id: (remaining[shape_id], shape_id)
    )
    return shape_models[preferred_shape_id].label


def _public_extra_shape_label(
    shape_bonus: dict,
    shape_models: dict[str, DriveShape],
) -> str:
    """Map the public Type-N shape choice to a solver-visible shape label."""

    grid_count = int(shape_bonus.get("shape_grid_count") or 0)
    if grid_count <= 0:
        return ""
    matching = sorted(
        (shape for shape in shape_models.values() if shape.area == grid_count),
        key=lambda shape: shape.shape_id,
    )
    return matching[0].label if matching else ""


def _display_board(board: list[list[object]]) -> list[list[str]]:
    return [
        [
            "XX"
            if str(cell) == "-1"
            else OFFICIAL_SHAPE_LABELS.get(str(cell), str(cell))
            for cell in row
        ]
        for row in board
    ]


def solve_blueprints_from_static(
    static_dao: StaticGameDataDao,
    *,
    custom_characters: Iterable[dict] = (),
    shared_database_path: str | Path | None = None,
) -> dict[str, dict]:
    module_items = static_dao.list_equipment_items("module")
    core_items = {
        item["item_id"]: item
        for item in static_dao.list_equipment_items("core")
    }
    item_by_id = {item["item_id"]: item for item in module_items}
    shape_models = _official_shape_models(
        static_dao.list_shapes(), module_items
    )
    combinatorics = PuzzleCombinatorics(shape_models)
    solver = DFSPuzzleSolver(shape_models)
    results: dict[str, dict] = {}

    for character in static_dao.list_characters():
        plan = static_dao.get_equipment_plan(int(character["character_id"]))
        if plan is None:
            continue
        core = core_items.get(str(plan.get("core_item_id") or ""))
        suit = static_dao.get_suit(str((core or {}).get("suit_id") or ""))
        set_piece_ids = [
            shape_id
            for shape_id in (suit or {}).get("required_shape_ids", [])
            if shape_id in shape_models
        ]
        if not suit or not set_piece_ids:
            continue

        board = official_board(plan)
        shape_bonus = get_effective_character_shape_bonus(
            static_dao,
            int(character["character_id"]),
            shared_database_path=shared_database_path,
        ) or {}
        preferred_label = _public_extra_shape_label(
            shape_bonus,
            shape_models,
        ) or _preferred_extra_label(
            plan, item_by_id, set_piece_ids, shape_models
        )
        candidates = _solve_board_candidates(
            board=board,
            suit_shape_ids=set_piece_ids,
            preferred_label=preferred_label,
            combinatorics=combinatorics,
            solver=solver,
        )
        if not candidates:
            continue
        role_name = str(
            plan.get("character_name_zh") or character["character_id"]
        )
        results[role_name] = {
            "character_id": int(character["character_id"]),
            "role_name": role_name,
            "core_name": str(
                plan.get("core_name_zh")
                or plan.get("core_item_id")
                or "未知卡带"
            ),
            "core_level": int(plan.get("core_level") or 0),
            "suit_name": str(suit.get("name_zh") or suit["suit_id"]),
            "preferred_extra_label": preferred_label or "无特定偏好",
            "blueprints": candidates,
        }
    for custom in custom_characters:
        character_id = int(custom["character_id"])
        role_name = str(custom.get("name_zh") or character_id)
        target_suit_id = str(custom.get("target_suit_id") or "")
        suit = static_dao.get_suit(target_suit_id) if target_suit_id else None
        set_piece_ids = [
            shape_id
            for shape_id in (suit or {}).get("required_shape_ids", [])
            if shape_id in shape_models
        ]
        if not suit or not set_piece_ids:
            continue
        board = custom_board(custom.get("board_cells") or ())
        candidates = _solve_board_candidates(
            board=board,
            suit_shape_ids=set_piece_ids,
            preferred_label="",
            combinatorics=combinatorics,
            solver=solver,
        )
        if not candidates:
            continue
        results[role_name] = {
            "character_id": character_id,
            "role_name": role_name,
            "core_name": "未配置卡带",
            "core_level": 0,
            "suit_name": str(suit.get("name_zh") or suit["suit_id"]),
            "preferred_extra_label": "无特定偏好",
            "blueprints": candidates,
            "is_custom": True,
        }

    return results

