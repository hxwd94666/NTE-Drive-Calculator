# 提供自动装配页面映射共用的名称归一化、缩放和网格坐标函数。
"""Map drive assembly blocks from blueprint grid coordinates to page pixels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.scanner.window_capture import game_content_rect
from src.utils.set_name import normalize_set_display_name


REFERENCE_SCREEN_SIZE = (2560, 1440)
DEFAULT_BOARD_ORIGIN = (1034.0, 315.0)
DEFAULT_CELL_SIZE = (93.0, 93.0)
DEFAULT_PAGE_CONTROLS = {
    "tape_tab": (240.0, 309.0),
    "filter_button": (111.0, 1347.0),
}
DEFAULT_DRIVE_PAGE_CONTROLS = {
    "drive_tab": (554.0, 309.0),
    "filter_button": (111.0, 1347.0),
}
DEFAULT_ASSEMBLY_PAGE_CONTROLS = {
    "unload_existing_drives": (1524.0, 1252.0),
}
DEFAULT_TAPE_FILTER_CONTROLS = {
    "set_select": (2067.0, 393.0),
}
DEFAULT_DRIVE_FILTER_CONTROLS = {
    "set_select": (2067.0, 393.0),
    "shape_select": (2067.0, 540.0),
}
DEFAULT_FILTER_ACTION_CONTROLS = {
    "reset_filter": (1861.0, 1322.0),
}
DEFAULT_TAPE_FILTER_STATUS_CONTROLS = {
    "status_equipped": (1861.0, 618.0),
    "status_locked": (2273.0, 618.0),
    "status_discarded": (1861.0, 704.0),
    "status_other": (2273.0, 704.0),
}
DEFAULT_DRIVE_FILTER_STATUS_CONTROLS = {
    "status_equipped": (1861.0, 765.0),
    "status_locked": (2273.0, 765.0),
    "status_discarded": (1861.0, 851.0),
    "status_other": (2273.0, 851.0),
}
DEFAULT_TAPE_FILTER_QUALITY_CONTROLS = {
    "quality_blue": (1861.0, 843.0),
    "quality_purple": (2273.0, 843.0),
    "quality_orange": (1861.0, 929.0),
}
DEFAULT_TAPE_FILTER_QUALITY_SELECTION_PROBES = {
    "quality_blue": (1721.0, 843.0),
    "quality_purple": (2133.0, 843.0),
    "quality_orange": (1721.0, 929.0),
}
DEFAULT_DRIVE_FILTER_QUALITY_CONTROLS = {
    "quality_blue": (1861.0, 989.0),
    "quality_purple": (2273.0, 989.0),
    "quality_orange": (1861.0, 1075.0),
}
DEFAULT_TAPE_FILTER_MAIN_STAT_CONTROLS = {
    "main_stat_expand": (2067.0, 1071.0),
}
DEFAULT_DRIVE_FILTER_QUALITY_SELECTION_PROBES = {
    "quality_blue": (1721.0, 989.0),
    "quality_purple": (2133.0, 989.0),
    "quality_orange": (1721.0, 1075.0),
}
DEFAULT_TAPE_MAIN_STAT_OCR_REGION = {
    "main_stat_ocr_region": (1640.0, 430.0, 2460.0, 1130.0),
}
DEFAULT_DRIVE_FILTER_SUB_STAT_CONTROLS = {
    "sub_stat_expand": (2067.0, 890.0),
    "sub_stat_count_four": (1861.0, 1202.0),
}
DEFAULT_DRIVE_SUB_STAT_SCROLL = {
    "sub_stat_scroll_start": (2067.0, 1190.0),
    "sub_stat_scroll_end": (2067.0, 395.0),
}
DEFAULT_TAPE_MAIN_STAT_SCROLL = {
    "main_stat_scroll_start": (2067.0, 1190.0),
    "main_stat_scroll_end": (2067.0, 395.0),
}
TAPE_MAIN_STAT_GAMEPAD_ACTION_PAUSE_SECONDS = 0.20
FILTER_NAVIGATION_PAUSE_SECONDS = 0.60
FILTER_OPTION_PAUSE_SECONDS = 0.30
FILTER_DIALOG_CLOSE_SETTLE_SECONDS = 0.80
TAPE_FILTER_RESULT_SETTLE_SECONDS = 0.60
TAPE_MODAL_DISMISS_SETTLE_SECONDS = 0.80
DEFAULT_TAPE_SUB_STAT_FILTER_ENTRY = {
    "sub_stat_scroll_start": (2067.0, 1190.0),
    "sub_stat_scroll_end": (2067.0, 395.0),
    "sub_stat_expand": (2067.0, 898.0),
}
DEFAULT_TAPE_SUB_STAT_SELECTION = {
    "sub_stat_scroll_start": (2067.0, 1190.0),
    "sub_stat_scroll_end": (2067.0, 395.0),
    "sub_stat_count_four": (1861.0, 1202.0),
}
DEFAULT_TAPE_EQUIP_FIRST_RESULT = {
    "confirm_filter": (2273.0, 1322.0),
    "first_tape": (126.0, 430.0),
    "tape_socket": (1267.0, 1090.0),
}
DEFAULT_EQUIPMENT_REUSE_PROMPT = {
    "reuse_prompt_confirm": (1546.0, 953.0),
    "reuse_prompt_probe": (1280.0, 690.0),
}
DEFAULT_DRIVE_EQUIP_FIRST_RESULT = {
    "confirm_filter": (2273.0, 1322.0),
    "first_drive": (126.0, 430.0),
}
DEFAULT_DRIVE_SHAPE_DIALOG_CONTROLS = {
    "confirm_filter": (1564.0, 1186.0),
}
DEFAULT_DRIVE_SHAPE_OPTIONS = {
    "H_2": (799.0, 488.0),
    "V_2": (948.0, 488.0),
    "H_3": (799.0, 745.0),
    "V_3": (948.0, 745.0),
    "L_3_BL": (1095.0, 745.0),
    "L_3_TL": (1243.0, 745.0),
    "L_3_TR": (1392.0, 745.0),
    "L_3_BR": (1542.0, 745.0),
    "H_4": (799.0, 1004.0),
    "V_4": (948.0, 1004.0),
    "Trap_4_H": (1095.0, 1004.0),
    "Trap_4_V": (1243.0, 1004.0),
}
DEFAULT_TAPE_SUB_STAT_OPTIONS = {
    "生命值百分比": (1861.0, 464.0),
    "攻击力百分比": (2273.0, 464.0),
    "防御力百分比": (1861.0, 550.0),
    "生命值": (2273.0, 550.0),
    "攻击力": (1861.0, 636.0),
    "防御力": (2273.0, 636.0),
    "暴击率": (1861.0, 721.0),
    "暴击伤害": (2273.0, 721.0),
    "环合强度": (1861.0, 807.0),
    "倾陷强度": (2273.0, 807.0),
    "通用伤害增强": (1861.0, 893.0),
}
DEFAULT_TAPE_MAIN_STAT_OPTIONS = {
    "生命值百分比": (1861.0, 485.0),
    "攻击力百分比": (2273.0, 485.0),
    "防御力百分比": (1861.0, 570.0),
    "暴击率": (2273.0, 570.0),
    "暴击伤害": (1861.0, 656.0),
    "环合强度": (2273.0, 656.0),
    "倾陷强度": (1861.0, 742.0),
    "治疗加成": (2273.0, 742.0),
    "光属性异能伤害增强": (1861.0, 828.0),
    "灵属性异能伤害增强": (2273.0, 828.0),
    "咒属性异能伤害增强": (1861.0, 914.0),
    "暗属性异能伤害增强": (2273.0, 914.0),
    "魂属性异能伤害增强": (1861.0, 999.0),
    "相属性异能伤害增强": (2273.0, 999.0),
    "心灵伤害增强": (1861.0, 1085.0),
}
TAPE_MAIN_STAT_ALIASES = {
    # 旧库存按数值属性名保存百分号，游戏筛选器则展示为无百分号的主词条名称。
    "生命值%": "生命值百分比",
    "攻击力%": "攻击力百分比",
    "防御力%": "防御力百分比",
    "暴击率%": "暴击率",
    "暴击伤害%": "暴击伤害",
    "光属性异能伤害增强%": "光属性异能伤害增强",
    "灵属性异能伤害增强%": "灵属性异能伤害增强",
    "咒属性异能伤害增强%": "咒属性异能伤害增强",
    "暗属性异能伤害增强%": "暗属性异能伤害增强",
    "魂属性异能伤害增强%": "魂属性异能伤害增强",
    "相属性异能伤害增强%": "相属性异能伤害增强",
    "心灵伤害增强%": "心灵伤害增强",
}
TAPE_SUB_STAT_ALIASES = {
    "生命值%": "生命值百分比",
    "攻击力%": "攻击力百分比",
    "防御力%": "防御力百分比",
    "暴击率%": "暴击率",
    "暴击伤害%": "暴击伤害",
    "伤害增加%": "通用伤害增强",
    "伤害%": "通用伤害增强",
}
TAPE_FILTER_QUALITY_ALIASES = {
    "blue": "quality_blue",
    "蓝色": "quality_blue",
    "purple": "quality_purple",
    "紫色": "quality_purple",
    "gold": "quality_orange",
    "orange": "quality_orange",
    "橙色": "quality_orange",
}
DRIVE_SHAPE_ALIASES = {
    "H": "H_2",
    "V": "V_2",
    "I_2": "V_2",
    "I_3": "V_3",
    "I_4": "V_4",
    "L_3": "L_3_BL",
    "J_3": "L_3_TL",
    "S_3": "L_3_TR",
    "Z_3": "L_3_BR",
    "T_4": "Trap_4_H",
    "J_4": "Trap_4_V",
}
DEFAULT_TAPE_SET_DIALOG_CONTROLS = {
    "confirm_filter": (1564.0, 1186.0),
}
DEFAULT_TAPE_SET_OPTIONS = {
    "迪亚波罗斯": (532.0, 493.0),
    "真红：双生蝶": (762.0, 493.0),
    "守卫王国": (994.0, 493.0),
    "小小大冒险": (1225.0, 493.0),
    "森林萤火之心": (532.0, 727.0),
    "街头拳王": (762.0, 727.0),
    "影之信条": (994.0, 727.0),
    "音速蓝刺猬": (1225.0, 727.0),
    "恶魔之血·诅咒": (532.0, 960.0),
    "失落光芒": (762.0, 960.0),
    "缇娅的夜间酒馆": (994.0, 960.0),
    "静谧山庄": (1225.0, 960.0),
}
TAPE_SET_NAME_ALIASES = {
    "缇娜的夜间酒馆": "缇娅的夜间酒馆",
}


@dataclass(frozen=True)
class PageCalibration:
    """Pixel calibration for the 5x5 assembly board."""

    reference_screen_size: tuple[int, int] = REFERENCE_SCREEN_SIZE
    board_origin: tuple[float, float] = DEFAULT_BOARD_ORIGIN
    cell_size: tuple[float, float] = DEFAULT_CELL_SIZE

    def scaled(
        self,
        screen_size: tuple[int, int] | None = None,
        content_rect: tuple[int, int, int, int] | None = None,
    ) -> "PageCalibration":
        if screen_size is None and content_rect is None:
            return self
        left, top, content_width, content_height = _content_rect_for(screen_size, self.reference_screen_size, content_rect)
        scale_x = content_width / self.reference_screen_size[0]
        scale_y = content_height / self.reference_screen_size[1]
        return PageCalibration(
            reference_screen_size=(content_width, content_height),
            board_origin=(left + self.board_origin[0] * scale_x, top + self.board_origin[1] * scale_y),
            cell_size=(self.cell_size[0] * scale_x, self.cell_size[1] * scale_y),
        )


DEFAULT_PAGE_CALIBRATION = PageCalibration()



def _quality_control_name(quality: str) -> str:
    normalized = str(quality).strip().lower()
    if normalized not in TAPE_FILTER_QUALITY_ALIASES:
        available = "、".join(["Blue/蓝色", "Purple/紫色", "Gold/Orange/橙色"])
        raise ValueError(f"未知品质: {quality}。可用品质: {available}")
    return TAPE_FILTER_QUALITY_ALIASES[normalized]


def _normalize_tape_main_stat(main_stat: str) -> str:
    normalized = str(main_stat).strip()
    normalized = TAPE_MAIN_STAT_ALIASES.get(normalized, normalized)
    if normalized not in DEFAULT_TAPE_MAIN_STAT_OPTIONS:
        available = "、".join(DEFAULT_TAPE_MAIN_STAT_OPTIONS)
        raise ValueError(f"未知卡带主词条: {main_stat}。可用主词条: {available}")
    return normalized


def _normalize_tape_set_name(set_name: str) -> str:
    value = normalize_set_display_name(set_name)
    if value in DEFAULT_TAPE_SET_OPTIONS:
        return value
    if value in TAPE_SET_NAME_ALIASES:
        return TAPE_SET_NAME_ALIASES[value]

    def compact(name: str) -> str:
        return "".join(char for char in name if char not in {" ", ":", "：", "·"})

    matches = [known_name for known_name in DEFAULT_TAPE_SET_OPTIONS if compact(known_name) == compact(value)]
    return matches[0] if len(matches) == 1 else value


def _normalize_tape_sub_stat(sub_stat: str) -> str:
    normalized = str(sub_stat).strip()
    normalized = TAPE_SUB_STAT_ALIASES.get(normalized, normalized)
    if normalized not in DEFAULT_TAPE_SUB_STAT_OPTIONS:
        available = "、".join(DEFAULT_TAPE_SUB_STAT_OPTIONS)
        raise ValueError(f"未知卡带副词条: {sub_stat}。可用副词条: {available}")
    return normalized


def _normalize_drive_shape(drive_type: str) -> str:
    normalized = str(drive_type).strip()
    normalized = DRIVE_SHAPE_ALIASES.get(normalized, normalized)
    if normalized not in DEFAULT_DRIVE_SHAPE_OPTIONS:
        available = "、".join(DEFAULT_DRIVE_SHAPE_OPTIONS)
        raise ValueError(f"未知驱动块外形: {drive_type}。可用外形: {available}")
    return normalized


def _drive_sub_stat_names(sub_stats: Any) -> list[str]:
    if isinstance(sub_stats, dict):
        return [str(name).strip() for name in sub_stats.keys() if str(name).strip()]
    if isinstance(sub_stats, list):
        return [str(name).strip() for name in sub_stats if str(name).strip()]
    return []


def _drive_target_position(
    block: dict[str, Any],
    screen_size: tuple[int, int] | None,
    content_rect: tuple[int, int, int, int] | None,
) -> tuple[int, int]:
    if "pixel_position" in block:
        x, y = block["pixel_position"]
        return int(x), int(y)
    calibration = DEFAULT_PAGE_CALIBRATION.scaled(
        screen_size=screen_size,
        content_rect=content_rect,
    )
    return _map_block_to_page(block, calibration)["pixel_position"]


def _scale_controls(
    controls: dict[str, tuple[float, float]],
    screen_size: tuple[int, int] | None,
    content_rect: tuple[int, int, int, int] | None,
) -> dict[str, tuple[int, int]]:
    left, top, content_width, content_height = _content_rect_for(screen_size, REFERENCE_SCREEN_SIZE, content_rect)
    scale_x = content_width / REFERENCE_SCREEN_SIZE[0]
    scale_y = content_height / REFERENCE_SCREEN_SIZE[1]
    return {
        name: (_round_half_up(left + x * scale_x), _round_half_up(top + y * scale_y))
        for name, (x, y) in controls.items()
    }


def _scale_region(
    region: tuple[float, float, float, float],
    screen_size: tuple[int, int] | None,
    content_rect: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int]:
    left, top, content_width, content_height = _content_rect_for(screen_size, REFERENCE_SCREEN_SIZE, content_rect)
    scale_x = content_width / REFERENCE_SCREEN_SIZE[0]
    scale_y = content_height / REFERENCE_SCREEN_SIZE[1]
    x1, y1, x2, y2 = region
    return (
        _round_half_up(left + x1 * scale_x),
        _round_half_up(top + y1 * scale_y),
        _round_half_up(left + x2 * scale_x),
        _round_half_up(top + y2 * scale_y),
    )


def _content_rect_for(
    screen_size: tuple[int, int] | None,
    reference_size: tuple[int, int],
    content_rect: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int]:
    if content_rect is not None:
        return content_rect
    if screen_size is None:
        return 0, 0, reference_size[0], reference_size[1]
    return _fit_content_rect(screen_size[0], screen_size[1], reference_size)


def _fit_content_rect(target_width: int, target_height: int, base_size: tuple[int, int]) -> tuple[int, int, int, int]:
    return game_content_rect(target_width, target_height, base_size)


def _map_block_to_page(block: dict[str, Any], calibration: PageCalibration) -> dict[str, Any]:
    cells = _cells(block)
    centroid = _grid_centroid(cells)
    pixel_position = _pixel_for_centroid(centroid, calibration)
    mapped = dict(block)
    mapped["shape_centroid"] = centroid
    mapped["grid_centroid"] = centroid
    mapped["pixel_position"] = pixel_position
    mapped["centroid_marker"] = {"label": str(block.get("block_id", "")), "position": pixel_position}
    mapped["board_origin"] = _round_pair(calibration.board_origin)
    mapped["cell_size"] = _clean_pair(calibration.cell_size)
    return mapped


def _cells(block: dict[str, Any]) -> list[tuple[int, int]]:
    cells = block.get("cells", [])
    return [(int(row), int(col)) for row, col in cells]


def _grid_centroid(cells: list[tuple[int, int]]) -> tuple[float, float]:
    if not cells:
        raise ValueError("assembly block has no cells")
    # The centroid of equal-sized occupied grid squares is the average of their centers.
    row = sum(cell[0] for cell in cells) / len(cells)
    col = sum(cell[1] for cell in cells) / len(cells)
    return (round(row, 6), round(col, 6))


def _pixel_for_centroid(centroid: tuple[float, float], calibration: PageCalibration) -> tuple[int, int]:
    row, col = centroid
    origin_x, origin_y = calibration.board_origin
    cell_w, cell_h = calibration.cell_size
    x = origin_x + (col - 0.5) * cell_w
    y = origin_y + (row - 0.5) * cell_h
    return (_round_half_up(x), _round_half_up(y))


def _round_pair(values: tuple[float, float]) -> tuple[int, int]:
    return (_round_half_up(values[0]), _round_half_up(values[1]))


def _clean_pair(values: tuple[float, float]) -> tuple[float | int, float | int]:
    return (_clean_number(values[0]), _clean_number(values[1]))


def _clean_number(value: float) -> float | int:
    rounded = round(value, 6)
    if rounded.is_integer():
        return int(rounded)
    return rounded


def _round_half_up(value: float) -> int:
    return int(value + 0.5001)
