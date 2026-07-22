# 使用官方 SQLite 图纸约束生成本地图纸方案。
"""MainWindow methods for the SQLite-backed local blueprint solver page."""

from __future__ import annotations

from collections import Counter

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget

from src.app.theme import themed_style
from src.app.workers import WorkerThread
from src.models.equipment import DriveShape
from src.solver.blueprint_utils import dedupe_blueprints_by_piece_signature
from src.solver.combinatorics import PuzzleCombinatorics
from src.solver.dfs_puzzle import DFSPuzzleSolver
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.ui.puzzle_board import PuzzleBoardWidget, get_shape_pixmap as _get_shape_pixmap
from src.ui.widgets import match_pinyin as _match_pinyin

from src.ui.main_window_method_install import install_methods as _install_main_window_methods

__all__ = ['_page_blueprint', '_refresh_blueprints', '_compute_blueprints', '_render_blueprints', '_draw_blueprints', '_filter_blueprints']

_OFFICIAL_SHAPE_LABELS = {
    "EquipmentGeometry_Hen2": "H_2", "EquipmentGeometry_Hen3": "H_3", "EquipmentGeometry_Hen4": "H_4",
    "EquipmentGeometry_Shu2": "V_2", "EquipmentGeometry_Shu3": "V_3", "EquipmentGeometry_Shu4": "V_4",
    "EquipmentGeometry_Z3": "Trap_4_H", "EquipmentGeometry_Z4": "Trap_4_V",
    "EquipmentGeometry_ZhiJiao1": "L_3_BL", "EquipmentGeometry_ZhiJiao2": "L_3_TL",
    "EquipmentGeometry_ZhiJiao3": "L_3_TR", "EquipmentGeometry_ZhiJiao4": "L_3_BR",
}


def install_methods(app_module, window_cls):
    """Install this feature's extracted MainWindow methods."""
    _install_main_window_methods(app_module, window_cls, __all__, globals())


def _official_shape_matrix(shape: dict) -> list[list[int]]:
    """将官方 geometry 的相对格坐标规范化为求解器矩阵。"""
    cells = list(shape.get("cells") or [])
    if not cells:
        raise ValueError(f"官方形状 {shape.get('shape_id') or '未知'} 未提供格子数据")
    rows = [int(cell["x"]) for cell in cells]
    columns = [int(cell["y"]) for cell in cells]
    min_row, min_column = min(rows), min(columns)
    matrix = [[0] * (max(columns) - min_column + 1) for _ in range(max(rows) - min_row + 1)]
    for cell in cells:
        matrix[int(cell["x"]) - min_row][int(cell["y"]) - min_column] = 1
    return matrix


def _official_shape_models(shapes: list[dict], module_items: list[dict]) -> dict[str, DriveShape]:
    """建立以官方 geometry ID 为键的本地图纸求解形状模型。"""
    names_by_geometry: dict[str, str] = {}
    for item in module_items:
        geometry_id = str(item.get("geometry_id") or "")
        if geometry_id and geometry_id not in names_by_geometry:
            names_by_geometry[geometry_id] = str(item.get("name_zh") or geometry_id)
    return {
        str(shape["shape_id"]): DriveShape(
            shape_id=str(shape["shape_id"]),
            label=names_by_geometry.get(str(shape["shape_id"]), str(shape["shape_id"])),
            matrix=_official_shape_matrix(shape),
            area=int(shape["cell_count"]),
            description=str(shape["shape_id"]),
        )
        for shape in shapes
    }


def _official_board(plan: dict) -> list[list[int]]:
    """从官方角色图纸盘面生成 5×5 的可放置/禁用棋盘。"""
    board = [[-1] * 5 for _ in range(5)]
    for cell in plan.get("cells") or []:
        row, column = int(cell["row"]) - 1, int(cell["column"]) - 1
        if 0 <= row < 5 and 0 <= column < 5:
            board[row][column] = 0
    playable = sum(value == 0 for row in board for value in row)
    if playable != 20:
        raise ValueError(f"{plan.get('character_name_zh') or plan.get('character_id')} 的官方盘面应为 20 格，实际为 {playable} 格")
    return board


def _preferred_extra_label(plan: dict, item_by_id: dict[str, dict], suit_shape_ids: list[str], shape_models: dict[str, DriveShape]) -> str:
    """从官方推荐模块中推导原图纸算法的散件偏好，而不读取旧 roles.json。"""
    recommended = [
        str(item_by_id[item_id].get("geometry_id") or "")
        for item_id in plan.get("module_item_ids") or []
        if item_id in item_by_id
    ]
    remaining = Counter(recommended)
    for shape_id in suit_shape_ids:
        remaining[str(shape_id)] -= 1
    candidates = [shape_id for shape_id, count in remaining.items() if count > 0 and shape_id in shape_models]
    if not candidates:
        return ""
    preferred_shape_id = max(candidates, key=lambda shape_id: (remaining[shape_id], shape_id))
    return shape_models[preferred_shape_id].label


def _display_board(board: list[list[object]]) -> list[list[str]]:
    """将官方 geometry ID 转成配装页棋盘可识别的图形标签。"""
    return [
        ["XX" if str(cell) == "-1" else _OFFICIAL_SHAPE_LABELS.get(str(cell), str(cell)) for cell in row]
        for row in board
    ]


def solve_blueprints_from_static(static_dao: StaticGameDataDao) -> dict[str, dict]:
    """用官方 SQLite 的形状、套装、卡带和盘面约束生成项目本地求解图纸。"""
    module_items = static_dao.list_equipment_items("module")
    core_items = {item["item_id"]: item for item in static_dao.list_equipment_items("core")}
    item_by_id = {item["item_id"]: item for item in module_items}
    shape_models = _official_shape_models(static_dao.list_shapes(), module_items)
    combinatorics = PuzzleCombinatorics(shape_models)
    solver = DFSPuzzleSolver(shape_models)
    results: dict[str, dict] = {}

    for character in static_dao.list_characters():
        plan = static_dao.get_equipment_plan(int(character["character_id"]))
        if plan is None:
            continue
        core = core_items.get(str(plan.get("core_item_id") or ""))
        suit = static_dao.get_suit(str((core or {}).get("suit_id") or ""))
        set_piece_ids = [shape_id for shape_id in (suit or {}).get("required_shape_ids", []) if shape_id in shape_models]
        if not suit or not set_piece_ids:
            continue

        board = _official_board(plan)
        preferred_label = _preferred_extra_label(plan, item_by_id, set_piece_ids, shape_models)
        candidates: list[dict] = []
        for extra_piece_ids in combinatorics.generate_piece_combinations(set_piece_ids, preferred_label):
            solved_boards: list[list[list[object]]] = []
            solver.solve(board, set_piece_ids + extra_piece_ids, solved_boards, max_solutions=1)
            if solved_boards:
                candidates.append({
                    "set_pieces": list(set_piece_ids),
                    "extra_pieces": list(extra_piece_ids),
                    "board": _display_board(solved_boards[0]),
                })
        candidates = dedupe_blueprints_by_piece_signature(candidates)
        if not candidates:
            continue
        role_name = str(plan.get("character_name_zh") or character["character_id"])
        results[role_name] = {
            "character_id": int(character["character_id"]),
            "role_name": role_name,
            "core_name": str(plan.get("core_name_zh") or plan.get("core_item_id") or "未知卡带"),
            "core_level": int(plan.get("core_level") or 0),
            "suit_name": str(suit.get("name_zh") or suit["suit_id"]),
            "preferred_extra_label": preferred_label or "无特定偏好",
            "blueprints": candidates,
        }
    return results


def _page_blueprint(self):
    page = QWidget()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(page)
    layout = QVBoxLayout(page)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(12)
    header = QHBoxLayout()
    self._bp_search = QLineEdit()
    self._bp_search.setPlaceholderText("搜索角色图纸（支持拼音）…")
    self._bp_search.setClearButtonEnabled(True)
    self._bp_search.textChanged.connect(self._filter_blueprints)
    header.addWidget(self._bp_search, 1)
    refresh_button = QPushButton("生成图纸")
    refresh_button.setObjectName("btnAction")
    refresh_button.clicked.connect(self._refresh_blueprints)
    header.addWidget(refresh_button)
    layout.addLayout(header)
    self._bp_status = QLabel("图纸由本地求解器生成，形状、套装、卡带和角色盘面均取自官方静态数据库。")
    self._bp_status.setStyleSheet(themed_style("color:#8b949e"))
    layout.addWidget(self._bp_status)
    self._bp_content = QWidget()
    self._bp_content_layout = QVBoxLayout(self._bp_content)
    self._bp_content_layout.setSpacing(12)
    self._bp_content_layout.setAlignment(Qt.AlignTop)
    layout.addWidget(self._bp_content)
    layout.addStretch()
    self._bp_data = {}
    return scroll


def _refresh_blueprints(self):
    while self._bp_content_layout.count():
        item = self._bp_content_layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
    self._bp_status.setText("正在使用官方数据生成图纸…")
    self._bp_content_layout.addWidget(QLabel("正在组合驱动并求解盘面…"))
    self._bp_worker = WorkerThread(target=self._compute_blueprints, parent=self)
    self._bp_worker.result_ready.connect(self._render_blueprints)
    self._bp_worker.error.connect(lambda error: self._bp_status.setText(f"生成图纸失败：{error}"))
    self._bp_worker.start()


def _compute_blueprints(self):
    """在工作线程内运行 SQLite 驱动的本地图纸求解。"""
    with StaticGameDataDao() as static_dao:
        return solve_blueprints_from_static(static_dao)


def _render_blueprints(self, data):
    self._bp_data = data or {}
    plan_count = sum(len(entry["blueprints"]) for entry in self._bp_data.values())
    self._bp_status.setText(f"已为 {len(self._bp_data)} 名角色生成 {plan_count} 个图纸方案。")
    self._draw_blueprints()


def _draw_blueprints(self, filter_text=""):
    while self._bp_content_layout.count():
        item = self._bp_content_layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
    if not self._bp_data:
        self._bp_content_layout.addWidget(QLabel("暂无可生成图纸的角色，请点击“生成图纸”。"))
        return
    search_text = filter_text.strip()
    shown = 0
    for role_name, role_data in sorted(self._bp_data.items()):
        if search_text and not _match_pinyin(role_name, search_text):
            continue
        shown += 1
        group = QGroupBox(f"{role_name}  —  {role_data['suit_name']}  ({len(role_data['blueprints'])} 套图纸)")
        group.setStyleSheet(themed_style(
            "QGroupBox{font-size:13px;font-weight:600;color:#58a6ff;"
            "border:1px solid #21262d;border-radius:8px;padding-top:16px}"
        ))
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(8)
        visible_blueprints = role_data["blueprints"][:3]
        for index, blueprint in enumerate(visible_blueprints, start=1):
            row = QHBoxLayout()
            row.setSpacing(10)
            row.addWidget(PuzzleBoardWidget(blueprint["board"], cell_size=28), 0, Qt.AlignTop)
            extras = QWidget()
            extras_layout = QVBoxLayout(extras)
            extras_layout.setContentsMargins(0, 0, 0, 0)
            extras_layout.setSpacing(2)
            extras_layout.addWidget(QLabel(f"方案 {index} · 额外形状"))
            image_row = QHBoxLayout()
            image_row.setSpacing(4)
            for shape_id in blueprint.get("extra_pieces", [])[:3]:
                shape_label = _OFFICIAL_SHAPE_LABELS.get(str(shape_id), str(shape_id))
                image = QLabel()
                image.setPixmap(_get_shape_pixmap(shape_label, 48))
                image.setToolTip(shape_label)
                image.setFixedSize(52, 52)
                image.setScaledContents(True)
                image_row.addWidget(image)
            image_row.addStretch()
            extras_layout.addLayout(image_row)
            row.addWidget(extras, 1)
            group_layout.addLayout(row)
        hidden_count = len(role_data["blueprints"]) - len(visible_blueprints)
        if hidden_count > 0:
            more = QLabel(f"仅展示前 3 套图纸；另有 {hidden_count} 套可行方案未展示。")
            more.setStyleSheet(themed_style("color:#8b949e;font-size:11px"))
            group_layout.addWidget(more)
        self._bp_content_layout.addWidget(group)
    if not shown:
        self._bp_content_layout.addWidget(QLabel("没有匹配的角色图纸。"))


def _filter_blueprints(self, text):
    self._draw_blueprints(text)
