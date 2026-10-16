# 核对 Rust 已落位的完整驱动方案，只做身份和占用集合校验，不枚举或搜索锚点。
from __future__ import annotations

from collections.abc import Mapping


def validate_native_layout(state, inventory, shapes, *, parse_uid):
    """返回冻结快照上的驱动指令；畸形证明直接失败，调用方不得退回旧摆盘搜索。"""
    proof = state.get("native_layout")
    if (not isinstance(proof, Mapping) or set(proof) != {"version", "tape_uid", "drives"}
            or type(proof["version"]) is not int or proof["version"] != 1
            or proof["tape_uid"] != (state.get("equipped_tape") or {}).get("uid")
            or not isinstance(proof["drives"], list) or not 1 <= len(proof["drives"]) <= 25):
        raise ValueError("原生布局证明缺失或版本不一致")
    board = state.get("blueprint_layout")
    if (not isinstance(board, list) or not 1 <= len(board) <= 5 or not isinstance(board[0], list)
            or not 1 <= len(board[0]) <= 5
            or any(not isinstance(row, list) or len(row) != len(board[0]) for row in board)):
        raise ValueError("原生布局棋盘尺寸无效")
    drives = state.get("equipped_drives")
    if not isinstance(drives, list) or len(drives) != len(proof["drives"]):
        raise ValueError("原生布局驱动集合不一致")
    by_uid = {d.get("uid"): d for d in drives if isinstance(d, Mapping)}
    if len(by_uid) != len(drives):
        raise ValueError("原生布局存在重复驱动")
    expected = {(r + 1, c + 1) for r, row in enumerate(board) for c, cell in enumerate(row)
                if cell != "XX" and not (type(cell) is int and cell < 0)}
    occupied, used, assignments = set(), set(), []
    for placed in proof["drives"]:
        if (not isinstance(placed, Mapping)
                or set(placed) != {"uid", "shape_id", "target_row", "target_column"}
                or placed["uid"] not in by_uid or placed["uid"] in used
                or placed["shape_id"] != by_uid[placed["uid"]].get("shape_id")
                or any(type(placed[key]) is not int or not 1 <= placed[key] <= 5
                       for key in ("target_row", "target_column"))):
            raise ValueError("原生布局驱动身份或锚点无效")
        slot, serial = parse_uid(placed["uid"], expected_kind="module")
        item = inventory.get((slot, serial))
        if item is None or item.get("kind") != "module" or by_uid[placed["uid"]].get("virtual"):
            raise ValueError("原生布局驱动不属于冻结真实库存")
        geometry = str(item.get("geometry") or "")
        shape_id = geometry if geometry.startswith("EquipmentGeometry_") else f"EquipmentGeometry_{geometry}"
        cells = (shapes.get(shape_id) or {}).get("cells") or []
        if (not cells or any(type(cell.get(key)) is not int for cell in cells for key in ("x", "y"))):
            raise ValueError("原生布局缺少正式形状坐标")
        offsets = {(cell["x"], cell["y"]) for cell in cells}
        if len(offsets) != len(cells) or len(cells) != item.get("grid_count"):
            raise ValueError("原生布局形状面积不一致")
        projected = {(placed["target_row"] + x, placed["target_column"] + y) for x, y in offsets}
        if (not projected <= expected or occupied.intersection(projected)
                or any(board[r - 1][c - 1] != placed["shape_id"] for r, c in projected)):
            raise ValueError("原生布局占用格与冻结棋盘不一致")
        occupied.update(projected)
        used.add(placed["uid"])
        assignments.append({"uid_slot": slot, "uid_serial": serial, "kind": "module",
                            "target_row": placed["target_row"], "target_column": placed["target_column"],
                            "rotation": 0, "geometry": item["geometry"], "grid_count": item["grid_count"]})
    if occupied != expected or used != set(by_uid):
        raise ValueError("原生布局存在未覆盖格位或遗漏驱动")
    return assignments
