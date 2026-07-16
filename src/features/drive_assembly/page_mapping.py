# 将装配块图纸坐标映射到实际装配页面像素坐标。
"""Map drive assembly blocks from blueprint grid coordinates to page pixels."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
    "生命值%": "生命值百分比",
    "攻击力%": "攻击力百分比",
    "防御力%": "防御力百分比",
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


def map_blocks_to_page(
    blocks: list[dict[str, Any]],
    screen_size: tuple[int, int] | None = None,
    calibration: PageCalibration = DEFAULT_PAGE_CALIBRATION,
    content_rect: tuple[int, int, int, int] | None = None,
) -> list[dict[str, Any]]:
    """Return copies of assembly blocks with centroid and pixel coordinates."""

    page = calibration.scaled(screen_size, content_rect)
    return [_map_block_to_page(block, page) for block in blocks]


def map_page_controls(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return page control pixel positions for opening tape filters."""

    controls = _scale_controls(DEFAULT_PAGE_CONTROLS, screen_size, content_rect)
    controls["click_sequence"] = [
        {"name": "tape_tab", "position": controls["tape_tab"]},
        {"name": "filter_button", "position": controls["filter_button"]},
    ]
    return controls


def map_drive_page_controls(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return page control pixel positions for opening drive filters."""

    controls = _scale_controls(DEFAULT_DRIVE_PAGE_CONTROLS, screen_size, content_rect)
    controls["click_sequence"] = [
        {"name": "drive_tab", "position": controls["drive_tab"]},
        {"name": "filter_button", "position": controls["filter_button"]},
    ]
    return controls


def map_assembly_page_prepare_controls(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return controls used immediately after entering the assembly page."""

    controls = _scale_controls(DEFAULT_ASSEMBLY_PAGE_CONTROLS, screen_size, content_rect)
    prompt = _scale_controls(DEFAULT_EQUIPMENT_REUSE_PROMPT, screen_size, content_rect)
    controls["unload_prompt_confirm"] = prompt["reuse_prompt_confirm"]
    controls["unload_prompt_probe"] = prompt["reuse_prompt_probe"]
    controls["prepare_sequence"] = [
        {"name": "unload_existing_drives", "position": controls["unload_existing_drives"]},
        {"name": "wait_for_unload_existing_drives_prompt", "wait_seconds": 1.0},
        {
            "name": "confirm_unload_existing_drives_prompt",
            "optional_confirm_position": controls["unload_prompt_confirm"],
            "modal_probe_position": controls["unload_prompt_probe"],
            "brightness_threshold": 150,
        },
    ]
    return controls


def map_tape_filter_controls(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return tape filter panel positions for choosing the required set."""

    controls = _scale_controls(DEFAULT_TAPE_FILTER_CONTROLS, screen_size, content_rect)
    controls["set_filter_sequence"] = [
        {"name": "set_select", "position": controls["set_select"]},
    ]
    return controls


def map_drive_shape_selection(
    drive_type: str,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return click positions for selecting a drive block shape."""

    normalized = _normalize_drive_shape(drive_type)
    filter_controls = _scale_controls(DEFAULT_DRIVE_FILTER_CONTROLS, screen_size, content_rect)
    shape_option = _scale_controls({normalized: DEFAULT_DRIVE_SHAPE_OPTIONS[normalized]}, screen_size, content_rect)[
        normalized
    ]
    dialog_controls = _scale_controls(DEFAULT_DRIVE_SHAPE_DIALOG_CONTROLS, screen_size, content_rect)
    result: dict[str, Any] = {
        "drive_type": normalized,
        "shape_select": filter_controls["shape_select"],
        "shape_option": shape_option,
        "confirm_filter": dialog_controls["confirm_filter"],
    }
    result["selection_sequence"] = [
        {"name": "shape_select", "position": result["shape_select"]},
        {"name": "shape_option", "drive_type": normalized, "position": result["shape_option"]},
        {"name": "confirm_shape_filter", "position": result["confirm_filter"]},
    ]
    return result


def map_drive_set_selection(
    set_name: str,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return click positions for selecting a drive set from the filter panel."""
    normalized_name = str(set_name).strip()
    if normalized_name not in DEFAULT_TAPE_SET_OPTIONS:
        available = ", ".join(DEFAULT_TAPE_SET_OPTIONS)
        raise ValueError(f"unknown drive set: {set_name}. available sets: {available}")
    filter_controls = _scale_controls(DEFAULT_DRIVE_FILTER_CONTROLS, screen_size, content_rect)
    set_option = _scale_controls({normalized_name: DEFAULT_TAPE_SET_OPTIONS[normalized_name]}, screen_size, content_rect)[
        normalized_name
    ]
    dialog_controls = _scale_controls(DEFAULT_TAPE_SET_DIALOG_CONTROLS, screen_size, content_rect)
    result: dict[str, Any] = {
        "set_name": normalized_name,
        "set_select": filter_controls["set_select"],
        "set_option": set_option,
        "confirm_filter": dialog_controls["confirm_filter"],
    }
    result["selection_sequence"] = [
        {"name": "drive_set_select", "set_name": normalized_name, "position": result["set_select"]},
        {"name": "drive_set_option", "set_name": normalized_name, "position": result["set_option"]},
        {"name": "confirm_drive_set_filter", "position": result["confirm_filter"]},
    ]
    return result


def map_filter_reset(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return the filter reset button used before every new drive search."""

    controls = _scale_controls(DEFAULT_FILTER_ACTION_CONTROLS, screen_size, content_rect)
    controls["reset_sequence"] = [{"name": "reset_filter", "position": controls["reset_filter"]}]
    return controls


def map_tape_set_selection(
    set_name: str,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return click positions for selecting a tape set in the set filter dialog."""

    normalized_name = str(set_name).strip()
    if normalized_name not in DEFAULT_TAPE_SET_OPTIONS:
        available = "、".join(DEFAULT_TAPE_SET_OPTIONS)
        raise ValueError(f"未知套装: {set_name}。可用套装: {available}")
    set_option = _scale_controls({normalized_name: DEFAULT_TAPE_SET_OPTIONS[normalized_name]}, screen_size, content_rect)[
        normalized_name
    ]
    controls = _scale_controls(DEFAULT_TAPE_SET_DIALOG_CONTROLS, screen_size, content_rect)
    result = {
        "set_name": normalized_name,
        "set_option": set_option,
        "confirm_filter": controls["confirm_filter"],
    }
    result["selection_sequence"] = [
        {"name": "set_option", "set_name": normalized_name, "position": result["set_option"]},
        {"name": "confirm_filter", "position": result["confirm_filter"]},
    ]
    return result


def map_tape_filter_refinement(
    qualities: list[str] | tuple[str, ...],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    include_main_stat_expand: bool = True,
    include_status_filters: bool = False,
) -> dict[str, Any]:
    """Return filter positions after the set has been selected."""

    status_controls = _scale_controls(DEFAULT_TAPE_FILTER_STATUS_CONTROLS, screen_size, content_rect)
    quality_controls = _scale_controls(DEFAULT_TAPE_FILTER_QUALITY_CONTROLS, screen_size, content_rect)
    quality_probes = _scale_controls(DEFAULT_TAPE_FILTER_QUALITY_SELECTION_PROBES, screen_size, content_rect)
    result: dict[str, Any] = {}
    for name in ("status_locked", "status_discarded", "status_other"):
        result[name] = status_controls[name]
    sequence: list[dict[str, Any]] = []
    if include_status_filters:
        sequence.extend(
            [
                {"name": "status_locked", "position": result["status_locked"]},
                {"name": "status_discarded", "position": result["status_discarded"]},
                {"name": "status_other", "position": result["status_other"]},
            ]
        )
    for quality in qualities:
        control_name = _quality_control_name(quality)
        result[control_name] = quality_controls[control_name]
        sequence.append({"name": control_name, "quality": quality, "position": result[control_name]})
        sequence.append(
            {
                "name": "verify_quality_selected",
                "quality": quality,
                "selection_probe_position": quality_probes[control_name],
                "retry_position": result[control_name],
            }
        )
    if include_main_stat_expand:
        main_stat_controls = _scale_controls(DEFAULT_TAPE_FILTER_MAIN_STAT_CONTROLS, screen_size, content_rect)
        result["main_stat_expand"] = main_stat_controls["main_stat_expand"]
        sequence.append({"name": "main_stat_expand", "position": result["main_stat_expand"]})
        sequence.append({"name": "wait_after_main_stat_expand", "wait_seconds": 0.5})
    result["refinement_sequence"] = sequence
    return result


def map_drive_filter_refinement(
    qualities: list[str] | tuple[str, ...],
    sub_stats: list[str] | tuple[str, ...],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 500,
    include_status_filters: bool = True,
    bottom_scroll_count: int = 1,
) -> dict[str, Any]:
    """Return drive filter positions after the shape has been selected."""

    status_controls = _scale_controls(DEFAULT_DRIVE_FILTER_STATUS_CONTROLS, screen_size, content_rect)
    quality_controls = _scale_controls(DEFAULT_DRIVE_FILTER_QUALITY_CONTROLS, screen_size, content_rect)
    quality_probes = _scale_controls(DEFAULT_DRIVE_FILTER_QUALITY_SELECTION_PROBES, screen_size, content_rect)
    sub_stat_controls = _scale_controls(DEFAULT_DRIVE_FILTER_SUB_STAT_CONTROLS, screen_size, content_rect)
    scroll_controls = _scale_controls(DEFAULT_DRIVE_SUB_STAT_SCROLL, screen_size, content_rect)
    normalized_stats = [_normalize_tape_sub_stat(stat) for stat in sub_stats]
    option_controls = _scale_controls(
        {stat: DEFAULT_TAPE_SUB_STAT_OPTIONS[stat] for stat in normalized_stats},
        screen_size,
        content_rect,
    )
    result: dict[str, Any] = {}
    for name in ("status_locked", "status_discarded", "status_other"):
        result[name] = status_controls[name]
    sequence = []
    if include_status_filters:
        sequence.extend(
            [
                {"name": "status_locked", "position": result["status_locked"]},
                {"name": "status_discarded", "position": result["status_discarded"]},
                {"name": "status_other", "position": result["status_other"]},
            ]
        )
    for quality in qualities:
        control_name = _quality_control_name(quality)
        result[control_name] = quality_controls[control_name]
        sequence.append({"name": control_name, "quality": quality, "position": result[control_name]})
        sequence.append(
            {
                "name": "verify_quality_selected",
                "quality": quality,
                "selection_probe_position": quality_probes[control_name],
                "retry_position": result[control_name],
            }
        )
    result["sub_stat_expand"] = sub_stat_controls["sub_stat_expand"]
    result["sub_stat_scroll_start"] = scroll_controls["sub_stat_scroll_start"]
    result["sub_stat_scroll_end"] = scroll_controls["sub_stat_scroll_end"]
    result["sub_stat_options"] = option_controls
    result["sub_stat_count_four"] = sub_stat_controls["sub_stat_count_four"]
    sequence.extend(
        {
            "name": "drive_filter_scroll_to_bottom",
            "from": result["sub_stat_scroll_start"],
            "to": result["sub_stat_scroll_end"],
            "duration_ms": duration_ms,
        }
        for _index in range(max(1, int(bottom_scroll_count)))
    )
    sequence.append({"name": "sub_stat_expand", "position": result["sub_stat_expand"]})
    sequence.append({"name": "wait_after_drive_sub_stat_expand", "wait_seconds": 0.5})
    sequence.extend(
        {
            "name": "drive_sub_stat_scroll_to_bottom",
            "from": result["sub_stat_scroll_start"],
            "to": result["sub_stat_scroll_end"],
            "duration_ms": duration_ms,
        }
        for _index in range(max(1, int(bottom_scroll_count)))
    )
    sequence.extend(
        {"name": "sub_stat_option", "sub_stat": stat, "position": option_controls[stat]} for stat in normalized_stats
    )
    sequence.append({"name": "sub_stat_count_four", "position": result["sub_stat_count_four"]})
    result["refinement_sequence"] = sequence
    return result


def map_tape_main_stat_scroll(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 500,
) -> dict[str, Any]:
    """Return the drag action that scrolls main stat options to the second page."""

    controls = _scale_controls(DEFAULT_TAPE_MAIN_STAT_SCROLL, screen_size, content_rect)
    result: dict[str, Any] = {
        "main_stat_scroll_start": controls["main_stat_scroll_start"],
        "main_stat_scroll_end": controls["main_stat_scroll_end"],
    }
    result["scroll_sequence"] = [
        {
            "name": "main_stat_scroll_to_second_page",
            "from": result["main_stat_scroll_start"],
            "to": result["main_stat_scroll_end"],
            "duration_ms": duration_ms,
        }
    ]
    return result


def map_tape_main_stat_gamepad_open() -> dict[str, Any]:
    """Return gamepad actions that open the tape main-stat list."""

    sequence: list[dict[str, Any]] = []
    sequence.extend(
        {
            "name": "main_stat_gamepad_down_to_expand",
            "gamepad_stick": "left_down",
            "post_action_pause_seconds": TAPE_MAIN_STAT_GAMEPAD_ACTION_PAUSE_SECONDS,
        }
        for _index in range(7)
    )
    sequence.append(
        {
            "name": "main_stat_gamepad_confirm_expand",
            "gamepad_button": "a",
            "post_action_pause_seconds": TAPE_MAIN_STAT_GAMEPAD_ACTION_PAUSE_SECONDS,
        }
    )
    sequence.extend(
        {
            "name": "main_stat_gamepad_down_to_options",
            "gamepad_stick": "left_down",
            "post_action_pause_seconds": TAPE_MAIN_STAT_GAMEPAD_ACTION_PAUSE_SECONDS,
        }
        for _index in range(3)
    )
    return {"open_sequence": sequence}


def map_tape_main_stat_selection(
    main_stat: str,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return the click position for the tape main stat required by the blueprint."""

    normalized = _normalize_tape_main_stat(main_stat)
    controls = _scale_controls({normalized: DEFAULT_TAPE_MAIN_STAT_OPTIONS[normalized]}, screen_size, content_rect)
    region = _scale_region(DEFAULT_TAPE_MAIN_STAT_OCR_REGION["main_stat_ocr_region"], screen_size, content_rect)
    result: dict[str, Any] = {
        "main_stat": normalized,
        "main_stat_option": controls[normalized],
        "main_stat_ocr_region": region,
    }
    result["selection_sequence"] = [
        {"name": "main_stat_option", "main_stat": normalized, "position": result["main_stat_option"]}
    ]
    result["ocr_selection_sequence"] = [
        {
            "name": "main_stat_option",
            "main_stat": normalized,
            "ocr_target_text": normalized,
            "ocr_search_region": region,
            "fallback_position": result["main_stat_option"],
        }
    ]
    return result


def map_tape_sub_stat_filter_entry(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    scroll_count: int = 1,
    duration_ms: int = 500,
) -> dict[str, Any]:
    """Return drag actions for reaching and opening the tape sub-stat filter."""

    controls = _scale_controls(DEFAULT_TAPE_SUB_STAT_FILTER_ENTRY, screen_size, content_rect)
    result: dict[str, Any] = {
        "sub_stat_scroll_start": controls["sub_stat_scroll_start"],
        "sub_stat_scroll_end": controls["sub_stat_scroll_end"],
        "sub_stat_expand": controls["sub_stat_expand"],
    }
    sequence: list[dict[str, Any]] = [
        {
            "name": "sub_stat_scroll_to_expand",
            "from": result["sub_stat_scroll_start"],
            "to": result["sub_stat_scroll_end"],
            "duration_ms": duration_ms,
        }
        for _index in range(scroll_count)
    ]
    sequence.append({"name": "sub_stat_expand", "position": result["sub_stat_expand"]})
    sequence.append({"name": "wait_after_sub_stat_expand", "wait_seconds": 0.5})
    result["entry_sequence"] = sequence
    return result


def map_tape_sub_stat_selection(
    sub_stats: list[str] | tuple[str, ...],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 500,
) -> dict[str, Any]:
    """Return clicks for tape sub-stats and the fixed sub-stat count of four."""

    scroll_controls = _scale_controls(DEFAULT_TAPE_SUB_STAT_SELECTION, screen_size, content_rect)
    normalized_stats = [_normalize_tape_sub_stat(stat) for stat in sub_stats]
    option_controls = _scale_controls(
        {stat: DEFAULT_TAPE_SUB_STAT_OPTIONS[stat] for stat in normalized_stats},
        screen_size,
        content_rect,
    )
    result: dict[str, Any] = {
        "sub_stat_options": option_controls,
        "sub_stat_count_four": scroll_controls["sub_stat_count_four"],
    }
    sequence: list[dict[str, Any]] = [
        {
            "name": "sub_stat_scroll_to_bottom",
            "from": scroll_controls["sub_stat_scroll_start"],
            "to": scroll_controls["sub_stat_scroll_end"],
            "duration_ms": duration_ms,
        }
    ]
    sequence.extend(
        {"name": "sub_stat_option", "sub_stat": stat, "position": option_controls[stat]} for stat in normalized_stats
    )
    sequence.append({"name": "sub_stat_count_four", "position": result["sub_stat_count_four"]})
    result["selection_sequence"] = sequence
    return result


def map_tape_equip_first_result(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 1200,
) -> dict[str, Any]:
    """Return actions for confirming the filter and equipping the first visible tape."""

    controls = _scale_controls(DEFAULT_TAPE_EQUIP_FIRST_RESULT, screen_size, content_rect)
    prompt = _scale_controls(DEFAULT_EQUIPMENT_REUSE_PROMPT, screen_size, content_rect)
    result: dict[str, Any] = {
        "confirm_filter": controls["confirm_filter"],
        "first_tape": controls["first_tape"],
        "tape_socket": controls["tape_socket"],
        "reuse_prompt_confirm": prompt["reuse_prompt_confirm"],
        "reuse_prompt_probe": prompt["reuse_prompt_probe"],
    }
    result["equip_sequence"] = [
        {"name": "confirm_filter", "position": result["confirm_filter"]},
        {
            "name": "drag_first_tape_to_socket",
            "from": result["first_tape"],
            "to": result["tape_socket"],
            "duration_ms": duration_ms,
        },
        {"name": "wait_for_equipment_reuse_prompt", "wait_seconds": 0.3},
        {
            "name": "confirm_equipment_reuse_prompt",
            "optional_confirm_position": result["reuse_prompt_confirm"],
            "modal_probe_position": result["reuse_prompt_probe"],
            "brightness_threshold": 150,
        },
    ]
    return result


def map_drive_block_installation(
    block: dict[str, Any],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 1200,
    cached_set_name: str | None = None,
    open_filter: bool = False,
) -> dict[str, Any]:
    """Return the filter and drag actions for installing one drive block."""

    drive = block.get("drive") if isinstance(block.get("drive"), dict) else {}
    drive_type = str(block.get("drive_type") or drive.get("shape_id") or "")
    quality = str(drive.get("quality") or "Gold")
    sub_stats = _drive_sub_stat_names(drive.get("sub_stats"))
    set_name = str(cached_set_name or block.get("set_name") or drive.get("set_name") or "").strip()
    reset = map_filter_reset(screen_size, content_rect)
    page_controls = map_drive_page_controls(screen_size, content_rect)
    set_selection = map_drive_set_selection(set_name, screen_size, content_rect) if set_name else None
    shape_selection = map_drive_shape_selection(drive_type, screen_size, content_rect)
    is_duplicate = bool(block.get("is_duplicate_drive") or block.get("is_duplicate_equipment"))
    refinement = map_drive_filter_refinement(
        [quality],
        sub_stats,
        screen_size,
        content_rect,
        include_status_filters=is_duplicate,
    )
    controls = _scale_controls(DEFAULT_DRIVE_EQUIP_FIRST_RESULT, screen_size, content_rect)
    prompt = _scale_controls(DEFAULT_EQUIPMENT_REUSE_PROMPT, screen_size, content_rect)
    target_position = _drive_target_position(block, screen_size, content_rect)
    result: dict[str, Any] = {
        "block_id": block.get("block_id"),
        "drive_type": shape_selection["drive_type"],
        "set_name": set_name,
        "shape_option": shape_selection["shape_option"],
        "first_drive": controls["first_drive"],
        "target_position": target_position,
        "confirm_filter": controls["confirm_filter"],
        "reuse_prompt_confirm": prompt["reuse_prompt_confirm"],
        "reuse_prompt_probe": prompt["reuse_prompt_probe"],
    }
    sequence: list[dict[str, Any]] = []
    if open_filter:
        sequence.append({"name": "filter_button", "position": page_controls["filter_button"]})
    sequence.extend(reset["reset_sequence"])
    sequence.extend(shape_selection["selection_sequence"])
    if set_selection:
        sequence.extend(set_selection["selection_sequence"])
    sequence.extend(refinement["refinement_sequence"])
    sequence.append({"name": "confirm_filter", "position": result["confirm_filter"]})
    sequence.append(
        {
            "name": "capture_drive_target_baseline",
            "block_id": block.get("block_id"),
            "target_position": result["target_position"],
            "sample_radius": 12,
        }
    )
    sequence.append(
        {
            "name": "force_drag_first_drive_to_block",
            "block_id": block.get("block_id"),
            "from": result["first_drive"],
            "to": result["target_position"],
            "duration_ms": duration_ms,
        }
    )
    sequence.append({"name": "wait_for_equipment_reuse_prompt", "wait_seconds": 0.3})
    sequence.append(
        {
            "name": "confirm_equipment_reuse_prompt",
            "block_id": block.get("block_id"),
            "optional_confirm_position": result["reuse_prompt_confirm"],
            "modal_probe_position": result["reuse_prompt_probe"],
            "brightness_threshold": 150,
        }
    )
    sequence.append({"name": "wait_after_drive_block_install", "wait_seconds": 1.0})
    sequence.append(
        {
            "name": "verify_drive_block_installed",
            "block_id": block.get("block_id"),
            "target_position": result["target_position"],
            "retry_from": result["first_drive"],
            "retry_to": result["target_position"],
            "retry_duration_ms": duration_ms,
            "sample_radius": 12,
            "change_threshold": 15.0,
            "brightness_threshold": 80.0,
            "optional_confirm_position": result["reuse_prompt_confirm"],
            "modal_probe_position": result["reuse_prompt_probe"],
            "retry_prompt_wait_seconds": 0.3,
            "retry_settle_seconds": 1.0,
        }
    )
    result["install_sequence"] = sequence
    return result


def map_drive_blocks_installation(
    blocks: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 1200,
) -> dict[str, Any]:
    """Return a per-block drive assembly plan.

    Each block is filtered and dragged independently so the first filtered
    result always corresponds to the current blueprint block being installed.
    """

    page_controls = map_drive_page_controls(screen_size, content_rect)
    cached_set_name = _drive_blocks_cached_set_name(blocks)
    install_plans = [
        map_drive_block_installation(
            block,
            screen_size,
            content_rect,
            duration_ms,
            cached_set_name=cached_set_name,
            open_filter=True,
        )
        for block in blocks
    ]
    result: dict[str, Any] = {
        "page_controls": page_controls,
        "install_plans": install_plans,
    }
    sequence: list[dict[str, Any]] = [{"name": "drive_tab", "position": page_controls["drive_tab"]}]
    sequence.extend(
        {
            "name": "install_drive_block",
            "block_id": install.get("block_id"),
            "sequence_index": index,
        }
        for index, install in enumerate(install_plans)
    )
    result["assembly_sequence"] = sequence
    return result


def _drive_blocks_cached_set_name(blocks: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> str:
    for block in blocks:
        drive = block.get("drive") if isinstance(block.get("drive"), dict) else {}
        set_name = str(block.get("set_name") or drive.get("set_name") or "").strip()
        if set_name:
            return set_name
    return ""


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
    return map_blocks_to_page([block], screen_size=screen_size, content_rect=content_rect)[0]["pixel_position"]


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
    base_w, base_h = base_size
    base_aspect = base_w / base_h
    target_aspect = target_width / target_height
    if target_aspect >= base_aspect:
        content_height = target_height
        content_width = round(content_height * base_aspect)
        left = round((target_width - content_width) / 2)
        top = 0
    else:
        content_width = target_width
        content_height = round(content_width / base_aspect)
        left = 0
        top = round((target_height - content_height) / 2)
    return left, top, max(1, content_width), max(1, content_height)


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
