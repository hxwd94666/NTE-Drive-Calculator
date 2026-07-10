# 管理角色识别、右侧角色列表遍历和逐角色装配计划。
"""Role recognition and traversal planning for drive assembly."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from src.features.drive_assembly.blocks import extract_drive_blocks_from_state, extract_tape_filters_from_state
from src.features.drive_assembly.page_mapping import map_blocks_to_page
from src.utils.image_io import imread_unicode
from src.utils.name_resolver import resolve_name


REFERENCE_SCREEN_SIZE = (2560, 1440)
DEFAULT_ROLE_NAVIGATION_CONTROLS = {
    "left_kongmu_tab": (176.0, 581.0),
    "assemble_button": (2160.0, 1322.0),
}
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
DEFAULT_ROLE_NAME_REGION = (1738.0, 252.0, 1900.0, 320.0)
DEFAULT_ROLE_TEMPLATE_REGION = (2300.0, 135.0, 2540.0, 1210.0)


@dataclass(frozen=True)
class RoleRecognition:
    """A normalized role recognition result."""

    role_name: str | None
    method: str
    confidence: float
    raw_text: str = ""


def map_role_navigation_controls(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Return controls for entering the assembly page from a role page."""

    controls = _scale_controls(DEFAULT_ROLE_NAVIGATION_CONTROLS, screen_size, content_rect)
    controls["entry_sequence"] = [
        {"name": "left_kongmu_tab", "position": controls["left_kongmu_tab"]},
        {"name": "assemble_button", "position": controls["assemble_button"]},
    ]
    return controls


def map_role_slots(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> list[tuple[int, int]]:
    """Return the five visible role avatar click positions."""

    return [
        _scale_point(point, screen_size, content_rect)
        for point in DEFAULT_ROLE_SLOT_POSITIONS
    ]


def map_role_slot_template_regions(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    half_width: int = 120,
    half_height: int = 120,
) -> list[tuple[int, int, int, int]]:
    """Return per-slot template matching regions around right-side avatars."""

    regions: list[tuple[int, int, int, int]] = []
    for x, y in map_role_slots(screen_size, content_rect):
        regions.append((x - half_width, y - half_height, x + half_width, y + half_height))
    return regions


def map_role_page_scroll(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    duration_ms: int = 700,
) -> dict[str, Any]:
    """Return the swipe action that advances the right-side role list by one page."""

    controls = _scale_controls(DEFAULT_ROLE_PAGE_SCROLL, screen_size, content_rect)
    result: dict[str, Any] = {
        "role_scroll_start": controls["role_scroll_start"],
        "role_scroll_end": controls["role_scroll_end"],
    }
    result["scroll_sequence"] = [
        {
            "name": "role_scroll_next_page",
            "from": result["role_scroll_start"],
            "to": result["role_scroll_end"],
            "duration_ms": duration_ms,
        }
    ]
    return result


def resolve_role_recognition(
    ocr_texts: list[str] | tuple[str, ...],
    expected_roles: list[str] | tuple[str, ...],
    template_scores: dict[str, float] | None = None,
    ocr_cutoff: float = 0.72,
    template_cutoff: float = 0.75,
) -> RoleRecognition:
    """Resolve a role name from OCR texts first, then template scores."""

    raw_text = "".join(str(text) for text in ocr_texts if str(text).strip())
    role_from_ocr = resolve_name(raw_text, expected_roles, cutoff=ocr_cutoff)
    if role_from_ocr:
        return RoleRecognition(role_from_ocr, "ocr", 1.0, raw_text)

    best_template = _best_template_score(template_scores or {}, expected_roles)
    if best_template and best_template[1] >= template_cutoff:
        return RoleRecognition(best_template[0], "template", round(float(best_template[1]), 4), raw_text)

    return RoleRecognition(None, "unrecognized", 0.0, raw_text)


def match_role_template(
    image: np.ndarray,
    template_dir: str | Path,
    expected_roles: list[str] | tuple[str, ...],
    region: tuple[int, int, int, int] | None = None,
) -> RoleRecognition:
    """Match a screenshot against role avatar templates."""

    if image is None or image.size == 0:
        return RoleRecognition(None, "template", 0.0)
    search = _crop(image, region) if region else image
    if search is None or search.size == 0:
        return RoleRecognition(None, "template", 0.0)
    gray_search = cv2.cvtColor(search, cv2.COLOR_BGR2GRAY) if len(search.shape) == 3 else search
    scores: dict[str, float] = {}
    for role_name in expected_roles:
        template = imread_unicode(Path(template_dir) / f"{role_name}.png", cv2.IMREAD_GRAYSCALE)
        if template is None or template.size == 0:
            continue
        score = _template_score(gray_search, template)
        if score is not None:
            scores[str(role_name)] = score
    return resolve_role_recognition([], expected_roles, scores)


def recognize_role_slots_from_image(
    image: np.ndarray,
    expected_roles: list[str] | tuple[str, ...],
    template_dir: str | Path,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> list[RoleRecognition]:
    """Recognize the five visible role slots from one screenshot."""

    regions = map_role_slot_template_regions(screen_size, content_rect)
    return [
        match_role_template(image, template_dir, expected_roles, region=region)
        for region in regions
    ]


def plan_role_assembly_from_observations(
    required_roles: list[str] | tuple[str, ...],
    observed_pages: list[list[RoleRecognition | str | None]],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """Build a de-duplicated per-role assembly plan from visible-page observations."""

    required = [str(role) for role in required_roles if str(role).strip()]
    required_set = set(required)
    slots = map_role_slots(screen_size, content_rect)
    entry = map_role_navigation_controls(screen_size, content_rect)
    scroll = map_role_page_scroll(screen_size, content_rect)
    seen: set[str] = set()
    plans: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    unrecognized: list[dict[str, Any]] = []

    for page_index, page in enumerate(observed_pages):
        for slot_index, observed in enumerate(page[: len(slots)]):
            recognition = _coerce_recognition(observed)
            slot_position = slots[slot_index]
            if not recognition.role_name:
                unrecognized.append({"page_index": page_index, "slot_index": slot_index, "position": slot_position})
                continue
            role_name = recognition.role_name
            if role_name in seen:
                duplicates.append({"role_name": role_name, "page_index": page_index, "slot_index": slot_index})
                continue
            seen.add(role_name)
            if role_name not in required_set:
                continue
            action_sequence = [
                {"name": "role_slot", "role_name": role_name, "position": slot_position},
                *entry["entry_sequence"],
                {"name": "run_drive_assembly_for_role", "role_name": role_name},
            ]
            plans.append(
                {
                    "role_name": role_name,
                    "page_index": page_index,
                    "slot_index": slot_index,
                    "recognition": {
                        "method": recognition.method,
                        "confidence": recognition.confidence,
                        "raw_text": recognition.raw_text,
                    },
                    "action_sequence": action_sequence,
                }
            )
        if page_index < len(observed_pages) - 1:
            plans.append(
                {
                    "role_name": None,
                    "page_index": page_index,
                    "action_sequence": scroll["scroll_sequence"],
                }
            )

    planned_roles = [plan["role_name"] for plan in plans if plan.get("role_name")]
    missing = [role for role in required if role not in set(planned_roles)]
    return {
        "required_roles": required,
        "planned_roles": planned_roles,
        "missing_roles": missing,
        "duplicates": duplicates,
        "unrecognized": unrecognized,
        "plans": plans,
        "complete": not missing and not unrecognized,
    }


def collect_role_observation_pages(
    required_roles: list[str] | tuple[str, ...],
    page_observer: Callable[[int], list[RoleRecognition]],
    scroll_next_page: Callable[[int], None] | None = None,
    max_pages: int | None = None,
) -> list[list[RoleRecognition]]:
    """Observe visible role pages until all required roles are seen or the page limit is reached."""

    required = {str(role) for role in required_roles if str(role).strip()}
    page_limit = max_pages if max_pages is not None else max(1, (len(required) + 4) // 5 + 1)
    observed_pages: list[list[RoleRecognition]] = []
    seen: set[str] = set()
    for page_index in range(max(1, page_limit)):
        page = page_observer(page_index)
        observed_pages.append(page)
        for recognition in page:
            if recognition.role_name:
                seen.add(recognition.role_name)
        if required and required.issubset(seen):
            break
        if page_index < page_limit - 1 and scroll_next_page is not None:
            scroll_next_page(page_index)
    return observed_pages


def build_role_assembly_payloads(
    equipped_state: dict[str, Any] | None,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return role-keyed assembly data for recognized roles."""

    drive_blocks = map_blocks_to_page(
        extract_drive_blocks_from_state(equipped_state),
        screen_size=screen_size,
        content_rect=content_rect,
    )
    tape_filters = extract_tape_filters_from_state(equipped_state)
    payloads: dict[str, dict[str, Any]] = {}
    for block in drive_blocks:
        role_name = str(block.get("blueprint_role_name") or block.get("role_name") or "")
        if not role_name:
            continue
        payloads.setdefault(role_name, {"drive_blocks": [], "tape_filter": None})["drive_blocks"].append(block)
    for tape_filter in tape_filters:
        role_name = str(tape_filter.get("blueprint_role_name") or tape_filter.get("role_name") or "")
        if not role_name:
            continue
        payloads.setdefault(role_name, {"drive_blocks": [], "tape_filter": None})["tape_filter"] = tape_filter
    return payloads


def required_roles_from_payloads(payloads: dict[str, dict[str, Any]]) -> list[str]:
    """Return roles that have at least one assembly item."""

    return [
        role_name
        for role_name, payload in payloads.items()
        if payload.get("drive_blocks") or payload.get("tape_filter")
    ]


def _coerce_recognition(value: RoleRecognition | str | None) -> RoleRecognition:
    if isinstance(value, RoleRecognition):
        return value
    if isinstance(value, str) and value.strip():
        return RoleRecognition(value.strip(), "provided", 1.0, value.strip())
    return RoleRecognition(None, "unrecognized", 0.0)


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
    base_w, base_h = REFERENCE_SCREEN_SIZE
    target_w, target_h = screen_size
    base_aspect = base_w / base_h
    target_aspect = target_w / target_h
    if target_aspect >= base_aspect:
        content_h = target_h
        content_w = round(content_h * base_aspect)
        left = round((target_w - content_w) / 2)
        top = 0
    else:
        content_w = target_w
        content_h = round(content_w / base_aspect)
        left = 0
        top = round((target_h - content_h) / 2)
    return left, top, max(1, content_w), max(1, content_h)


def _round_half_up(value: float) -> int:
    return int(value + 0.5001)
