# 提供角色识别与遍历规划共用的归一化、相似度和坐标缩放函数。
"""Role recognition and traversal planning for drive assembly."""

from __future__ import annotations


import cv2
import numpy as np

from src.scanner.window_capture import game_content_rect
from src.features.drive_assembly.role_contracts import RoleRecognition


REFERENCE_SCREEN_SIZE = (2560, 1440)
DEFAULT_ROLE_NAVIGATION_CONTROLS = {
    "left_kongmu_tab": (88.0, 581.0),
    "assemble_button": (2160.0, 1322.0),
}
ROLE_KONGMU_TAB_SETTLE_SECONDS = 1.0
ROLE_ASSEMBLE_PAGE_SETTLE_SECONDS = 1.2
ROLE_LIST_STICK_MOVE_PAUSE_SECONDS = 0.25
DEFAULT_ROLE_SLOT_POSITIONS = [
    (2410.0, 242.0),
    (2410.0, 470.0),
    (2410.0, 697.0),
    (2410.0, 925.0),
    (2410.0, 1152.0),
]
DEFAULT_ROLE_PAGE_SCROLL = {
    "role_scroll_start": (2388.0, 1152.0),
    "role_scroll_end": (2388.0, 242.0),
}
DEFAULT_ROLE_PAGE_RESET_SCROLLS = 6
DEFAULT_ROLE_ROSTER_MAX_PAGES = 20
DEFAULT_ROLE_NAME_REGION = (1738.0, 252.0, 2180.0, 320.0)
DEFAULT_ROLE_NAME_FALLBACK_REGION = (1688.0, 228.0, 2248.0, 342.0)
DEFAULT_ROLE_TEMPLATE_REGION = (2300.0, 135.0, 2540.0, 1210.0)
DEFAULT_DPAD_RESET_UP_COUNT = 5
DEFAULT_DPAD_BOTTOM_REPEAT_LIMIT = 3
DEFAULT_DPAD_ROLE_LIMIT = 80
ROLE_LIST_GRID_COLUMNS = 3
ROLE_LIST_GRID_ROWS = 4
ROLE_LIST_FIRST_PAGE_SIZE = ROLE_LIST_GRID_COLUMNS * ROLE_LIST_GRID_ROWS
ROLE_LIST_INITIAL_LEFT_RESET_COUNT = 4



def _coerce_recognition(value: RoleRecognition | str | None) -> RoleRecognition:
    if isinstance(value, RoleRecognition):
        return value
    if isinstance(value, str) and value.strip():
        return RoleRecognition(value.strip(), "provided", 1.0, value.strip())
    return RoleRecognition(None, "unrecognized", 0.0)


def _recognition_stability_key(recognition: RoleRecognition) -> str:
    if recognition.role_name:
        return recognition.role_name
    return str(recognition.raw_text or "").strip()


def _normalize_role_alias(
    recognition: RoleRecognition,
    role_aliases: dict[str, str] | None,
) -> RoleRecognition:
    if not recognition.role_name or not role_aliases:
        return recognition
    recognized = str(recognition.role_name).strip()
    for canonical, alias in role_aliases.items():
        canonical_name = str(canonical).strip()
        alias_name = str(alias).strip()
        if alias_name and recognized == alias_name:
            return RoleRecognition(canonical_name, recognition.method, recognition.confidence, recognition.raw_text)
    return recognition


def _best_template_score(
    template_scores: dict[str, float],
    expected_roles: list[str] | tuple[str, ...],
) -> tuple[str, float] | None:
    valid = [(role, float(template_scores.get(role, -1.0))) for role in expected_roles]
    valid = [(role, score) for role, score in valid if score >= 0]
    if not valid:
        return None
    return max(valid, key=lambda item: item[1])


def _template_score(search: np.ndarray, template: np.ndarray) -> float | None:
    search_h, search_w = search.shape[:2]
    th, tw = template.shape[:2]
    best: float | None = None
    for scale in (0.5, 0.65, 0.8, 1.0, 1.2):
        rw, rh = int(tw * scale), int(th * scale)
        if rw < 16 or rh < 16 or rw > search_w or rh > search_h:
            continue
        resized = cv2.resize(template, (rw, rh), interpolation=cv2.INTER_AREA)
        res = cv2.matchTemplate(search, resized, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        best = max(float(max_val), best if best is not None else -1.0)
    return best


def _crop(image: np.ndarray, region: tuple[int, int, int, int]) -> np.ndarray:
    height, width = image.shape[:2]
    x1, y1, x2, y2 = region
    x1 = max(0, min(width, int(x1)))
    x2 = max(0, min(width, int(x2)))
    y1 = max(0, min(height, int(y1)))
    y2 = max(0, min(height, int(y2)))
    return image[y1:y2, x1:x2]


def _scale_controls(
    controls: dict[str, tuple[float, float]],
    screen_size: tuple[int, int] | None,
    content_rect: tuple[int, int, int, int] | None,
) -> dict[str, tuple[int, int]]:
    return {name: _scale_point(point, screen_size, content_rect) for name, point in controls.items()}


def _scale_point(
    point: tuple[float, float],
    screen_size: tuple[int, int] | None,
    content_rect: tuple[int, int, int, int] | None,
) -> tuple[int, int]:
    left, top, content_width, content_height = _content_rect_for(screen_size, content_rect)
    scale_x = content_width / REFERENCE_SCREEN_SIZE[0]
    scale_y = content_height / REFERENCE_SCREEN_SIZE[1]
    return (_round_half_up(left + point[0] * scale_x), _round_half_up(top + point[1] * scale_y))


def _content_rect_for(
    screen_size: tuple[int, int] | None,
    content_rect: tuple[int, int, int, int] | None,
) -> tuple[int, int, int, int]:
    if content_rect is not None:
        return content_rect
    if screen_size is None:
        return 0, 0, REFERENCE_SCREEN_SIZE[0], REFERENCE_SCREEN_SIZE[1]
    return game_content_rect(screen_size[0], screen_size[1], REFERENCE_SCREEN_SIZE)


def _round_half_up(value: float) -> int:
    return int(value + 0.5001)
