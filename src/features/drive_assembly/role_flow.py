# 管理角色识别、右侧角色列表遍历和逐角色装配计划。
"""Role recognition and traversal planning for drive assembly."""

from __future__ import annotations

from dataclasses import dataclass
import difflib
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from src.features.drive_assembly.blocks import extract_drive_blocks_from_state, extract_tape_filters_from_state
from src.features.drive_assembly.page_mapping import map_blocks_to_page
from src.utils.image_io import imread_unicode
from src.utils.name_resolver import normalize_name, resolve_name


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
# Known OCR misreads observed in the in-game role-name region.  These are
# applied only when the canonical role is present in the active blueprint.
ROLE_OCR_CORRECTIONS = {
    "医殿B朴": "翳",
    # The single-character name 翳 is commonly read as the longer fragment
    # “医设醫” in the in-game role-name region.
    "医设": "翳",
}


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
    controls["assemble_sequence"] = [
        {"name": "assemble_button", "position": controls["assemble_button"]},
        {"name": "wait_after_assemble_button", "wait_seconds": ROLE_ASSEMBLE_PAGE_SETTLE_SECONDS},
    ]
    controls["entry_sequence"] = [
        {"name": "left_kongmu_tab", "position": controls["left_kongmu_tab"]},
        {"name": "wait_after_left_kongmu_tab", "wait_seconds": ROLE_KONGMU_TAB_SETTLE_SECONDS},
        *controls["assemble_sequence"],
    ]
    controls["exit_sequence"] = [
        {
            "name": "assembly_back_to_role_page",
            "gamepad_button": "b",
            "post_action_pause_seconds": 1.5,
        },
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


def map_role_page_reset(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    repeat_count: int = DEFAULT_ROLE_PAGE_RESET_SCROLLS,
    duration_ms: int = 700,
) -> dict[str, Any]:
    """Return swipes that move the right-side role list back toward the first page."""

    controls = _scale_controls(DEFAULT_ROLE_PAGE_SCROLL, screen_size, content_rect)
    result: dict[str, Any] = {
        "role_scroll_start": controls["role_scroll_end"],
        "role_scroll_end": controls["role_scroll_start"],
        "repeat_count": max(0, int(repeat_count)),
    }
    result["reset_sequence"] = [
        {
            "name": "role_scroll_reset_to_first_page",
            "from": result["role_scroll_start"],
            "to": result["role_scroll_end"],
            "duration_ms": duration_ms,
        }
        for _index in range(result["repeat_count"])
    ]
    return result


def map_current_role_name_region(
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    expanded: bool = False,
) -> tuple[int, int, int, int]:
    """Return the top-right current role name OCR region."""

    region = DEFAULT_ROLE_NAME_FALLBACK_REGION if expanded else DEFAULT_ROLE_NAME_REGION
    x1, y1 = _scale_point((region[0], region[1]), screen_size, content_rect)
    x2, y2 = _scale_point((region[2], region[3]), screen_size, content_rect)
    return x1, y1, x2, y2


def map_dpad_role_reset_sequence(repeat_count: int = DEFAULT_DPAD_RESET_UP_COUNT) -> list[dict[str, Any]]:
    """Return gamepad actions that move the role cursor to the first role."""

    return [
        {"name": "role_dpad_reset_to_first", "gamepad_button": "dpad_up"}
        for _index in range(max(0, int(repeat_count)))
    ]


def map_dpad_role_down_sequence(repeat_count: int) -> list[dict[str, Any]]:
    """Return gamepad actions that move down by a role count."""

    return [
        {"name": "role_dpad_next", "gamepad_button": "dpad_down"}
        for _index in range(max(0, int(repeat_count)))
    ]


def map_dpad_role_move_sequence(current_index: int, target_index: int) -> list[dict[str, Any]]:
    """Return D-pad actions that move from one recognized roster index to another."""

    delta = int(target_index) - int(current_index)
    if delta > 0:
        return map_dpad_role_down_sequence(delta)
    if delta < 0:
        return [
            {"name": "role_dpad_previous", "gamepad_button": "dpad_up"}
            for _index in range(abs(delta))
        ]
    return []


def map_role_list_grid_move_sequence(
    current_index: int,
    target_index: int,
    columns: int = ROLE_LIST_GRID_COLUMNS,
) -> list[dict[str, Any]]:
    """Move between roles in the three-column RS character-list grid.

    All directional inputs in the list use the left stick. Adjacent roster
    entries cross a row boundary through left/right exactly as the game does.
    Longer moves use vertical movement first, then stay within the target row
    for horizontal correction.
    """

    width = max(1, int(columns))
    current = max(0, int(current_index))
    target = max(0, int(target_index))
    if target == current:
        return []
    if target == current + 1:
        return [
            {
                "name": "role_list_next",
                "gamepad_stick": "left_right",
                "post_action_pause_seconds": ROLE_LIST_STICK_MOVE_PAUSE_SECONDS,
            }
        ]
    if target == current - 1:
        return [
            {
                "name": "role_list_previous",
                "gamepad_stick": "left_left",
                "post_action_pause_seconds": ROLE_LIST_STICK_MOVE_PAUSE_SECONDS,
            }
        ]

    current_row, current_col = divmod(current, width)
    target_row, target_col = divmod(target, width)
    sequence: list[dict[str, Any]] = []
    vertical_stick = "left_down" if target_row > current_row else "left_up"
    vertical_name = "role_list_down" if target_row > current_row else "role_list_up"
    sequence.extend(
        {
            "name": vertical_name,
            "gamepad_stick": vertical_stick,
            "post_action_pause_seconds": ROLE_LIST_STICK_MOVE_PAUSE_SECONDS,
        }
        for _index in range(abs(target_row - current_row))
    )
    horizontal_input = "left_right" if target_col > current_col else "left_left"
    horizontal_name = "role_list_next" if target_col > current_col else "role_list_previous"
    sequence.extend(
        {
            "name": horizontal_name,
            "gamepad_stick": horizontal_input,
            "post_action_pause_seconds": ROLE_LIST_STICK_MOVE_PAUSE_SECONDS,
        }
        for _index in range(abs(target_col - current_col))
    )
    return sequence


def resolve_role_recognition(
    ocr_texts: list[str] | tuple[str, ...],
    expected_roles: list[str] | tuple[str, ...],
    template_scores: dict[str, float] | None = None,
    ocr_cutoff: float = 0.72,
    template_cutoff: float = 0.75,
) -> RoleRecognition:
    """Resolve a role name from OCR texts first, then template scores."""

    text_parts = [str(text).strip() for text in ocr_texts if str(text).strip()]
    raw_text = "".join(text_parts)
    corrected_role = _resolve_known_role_ocr_correction(text_parts, expected_roles)
    if corrected_role:
        return RoleRecognition(corrected_role, "ocr_correction", 1.0, raw_text)
    role_from_ocr = _resolve_role_name_from_ocr(text_parts, expected_roles, ocr_cutoff)
    if role_from_ocr:
        role_name, method, confidence = role_from_ocr
        return RoleRecognition(role_name, method, confidence, raw_text)
    yi_fallback = _resolve_yi_ocr_fallback(text_parts, expected_roles)
    if yi_fallback:
        return RoleRecognition(yi_fallback, "ocr_yi_fallback", 0.6, raw_text)

    best_template = _best_template_score(template_scores or {}, expected_roles)
    if best_template and best_template[1] >= template_cutoff:
        return RoleRecognition(best_template[0], "template", round(float(best_template[1]), 4), raw_text)

    return RoleRecognition(None, "unrecognized", 0.0, raw_text)


def _resolve_known_role_ocr_correction(
    text_parts: list[str],
    expected_roles: list[str] | tuple[str, ...],
) -> str | None:
    """Return a canonical role for a known, otherwise ambiguous OCR error."""

    candidates = {str(role).strip() for role in expected_roles if str(role).strip()}
    if not candidates:
        return None
    sources = [normalize_name(text) for text in [*text_parts, "".join(text_parts)]]
    for mistaken_text, canonical_role in ROLE_OCR_CORRECTIONS.items():
        if canonical_role not in candidates:
            continue
        mistaken_key = normalize_name(mistaken_text)
        if mistaken_key and any(mistaken_key in source for source in sources):
            return canonical_role
    return None


def _resolve_yi_ocr_fallback(
    text_parts: list[str],
    expected_roles: list[str] | tuple[str, ...],
) -> str | None:
    """Identify 翳 from a residual OCR fragment containing 医/醫.

    This is intentionally evaluated only after normal OCR matching failed,
    and only if 翳 is an active candidate.  It therefore cannot override a
    valid recognition of another role.
    """

    candidates = {str(role).strip() for role in expected_roles if str(role).strip()}
    if "翳" not in candidates:
        return None
    sources = [normalize_name(text) for text in [*text_parts, "".join(text_parts)]]
    if any("医" in source or "醫" in source for source in sources):
        return "翳"
    return None


def _resolve_role_name_from_ocr(
    text_parts: list[str],
    expected_roles: list[str] | tuple[str, ...],
    cutoff: float,
) -> tuple[str, str, float] | None:
    """Match OCR fragments against role names, tolerating surrounding UI text and one-character errors."""

    candidates = [str(role).strip() for role in expected_roles if str(role).strip()]
    if not text_parts or not candidates:
        return None

    sources = [*text_parts, "".join(text_parts)]
    for source in sources:
        resolved = resolve_name(source, candidates, cutoff=cutoff)
        if resolved:
            return resolved, "ocr", 1.0

    scored: list[tuple[float, str]] = []
    for role_name in candidates:
        role_key = normalize_name(role_name)
        if len(role_key) < 2:
            continue
        score = max((_role_ocr_similarity(source, role_key) for source in sources), default=0.0)
        scored.append((score, role_name))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    best_score, best_role = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0

    # A two-character Chinese name with one OCR error scores about 0.5.
    # Keep a margin so similar candidates cannot be silently confused.
    if best_score < 0.5 or best_score - second_score < 0.15:
        return None
    return best_role, "ocr_fuzzy", round(best_score, 4)


def _role_ocr_similarity(source: str, role_key: str) -> float:
    source_key = normalize_name(source)
    if not source_key:
        return 0.0
    if role_key in source_key:
        return 1.0
    score = difflib.SequenceMatcher(None, source_key, role_key).ratio()
    target_length = len(role_key)
    for width in range(max(2, target_length - 1), target_length + 2):
        if width > len(source_key):
            continue
        for start in range(0, len(source_key) - width + 1):
            score = max(score, difflib.SequenceMatcher(None, source_key[start:start + width], role_key).ratio())
    return score


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


def recognize_current_role_from_image(
    image: np.ndarray,
    expected_roles: list[str] | tuple[str, ...],
    ocr_engine: Any,
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    role_aliases: dict[str, str] | None = None,
) -> RoleRecognition:
    """Recognize the currently selected role from the top-right name text."""

    if image is None or image.size == 0:
        return RoleRecognition(None, "unrecognized", 0.0)
    if ocr_engine is None:
        return RoleRecognition(None, "unrecognized", 0.0)

    primary_region = map_current_role_name_region(screen_size, content_rect)
    primary_crop = _crop(image, primary_region)
    primary_texts = ocr_engine.extract_text(primary_crop)
    primary_result = resolve_role_recognition(primary_texts, expected_roles)
    if primary_result.role_name:
        return _normalize_role_alias(primary_result, role_aliases)

    fallback_region = map_current_role_name_region(screen_size, content_rect, expanded=True)
    fallback_crop = _crop(image, fallback_region)
    fallback_texts = ocr_engine.extract_text(fallback_crop)
    combined_texts = list(primary_texts or []) + list(fallback_texts or [])
    fallback_result = resolve_role_recognition(combined_texts, expected_roles)
    if fallback_result.role_name:
        return _normalize_role_alias(RoleRecognition(
            fallback_result.role_name,
            "ocr_fallback",
            fallback_result.confidence,
            fallback_result.raw_text,
        ), role_aliases)
    return _normalize_role_alias(fallback_result, role_aliases)


def plan_role_assembly_from_observations(
    required_roles: list[str] | tuple[str, ...],
    observed_pages: list[list[RoleRecognition | str | None]],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    reset_to_first_page: bool = False,
    reset_scroll_count: int = DEFAULT_ROLE_PAGE_RESET_SCROLLS,
) -> dict[str, Any]:
    """Build a de-duplicated per-role assembly plan from visible-page observations."""

    required = [str(role) for role in required_roles if str(role).strip()]
    required_set = set(required)
    slots = map_role_slots(screen_size, content_rect)
    entry = map_role_navigation_controls(screen_size, content_rect)
    scroll = map_role_page_scroll(screen_size, content_rect)
    reset = map_role_page_reset(screen_size, content_rect, repeat_count=reset_scroll_count)
    seen: set[str] = set()
    plans: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    unrecognized: list[dict[str, Any]] = []

    if reset_to_first_page and observed_pages:
        plans.append(
            {
                "role_name": None,
                "page_index": -1,
                "action_sequence": reset["reset_sequence"],
            }
        )

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
                {"name": "assemble_current_role_from_blueprint", "role_name": role_name},
            ]
            plans.append(
                {
                    "role_name": role_name,
                    "page_index": page_index,
                    "slot_index": slot_index,
                    "flow": "find_role_then_assemble_blueprint",
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


def collect_role_roster_until_repeat(
    expected_roles: list[str] | tuple[str, ...],
    page_observer: Callable[[int], list[RoleRecognition]],
    scroll_next_page: Callable[[int], None] | None = None,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Scan the role sidebar until a post-scroll page repeats a known role."""

    page_limit = max_pages if max_pages is not None else DEFAULT_ROLE_ROSTER_MAX_PAGES
    observed_pages: list[list[RoleRecognition]] = []
    role_order: list[str] = []
    seen: set[str] = set()
    duplicates: list[dict[str, Any]] = []
    unrecognized: list[dict[str, Any]] = []
    reached_bottom = False
    bottom_page_index = 0

    for page_index in range(max(1, page_limit)):
        page = page_observer(page_index)
        observed_pages.append(page)
        page_repeated = False
        for slot_index, observed in enumerate(page[:5]):
            recognition = _coerce_recognition(observed)
            if not recognition.role_name:
                unrecognized.append({"page_index": page_index, "slot_index": slot_index})
                continue
            role_name = recognition.role_name
            if role_name in seen:
                duplicates.append({"role_name": role_name, "page_index": page_index, "slot_index": slot_index})
                page_repeated = True
                continue
            seen.add(role_name)
            role_order.append(role_name)
        bottom_page_index = page_index
        if page_index > 0 and page_repeated:
            reached_bottom = True
            break
        if page_index < page_limit - 1 and scroll_next_page is not None:
            scroll_next_page(page_index)

    expected = [str(role) for role in expected_roles if str(role).strip()]
    return {
        "roles": role_order,
        "observed_pages": observed_pages,
        "duplicates": duplicates,
        "unrecognized": unrecognized,
        "missing_expected_roles": [role for role in expected if role not in seen],
        "bottom_page_index": bottom_page_index,
        "reached_bottom": reached_bottom,
    }


def collect_role_roster_with_dpad(
    expected_roles: list[str] | tuple[str, ...],
    current_observer: Callable[[int], RoleRecognition],
    press_up: Callable[[], None],
    press_down: Callable[[], None],
    reset_up_count: int = DEFAULT_DPAD_RESET_UP_COUNT,
    bottom_repeat_limit: int = DEFAULT_DPAD_BOTTOM_REPEAT_LIMIT,
    max_roles: int = DEFAULT_DPAD_ROLE_LIMIT,
) -> dict[str, Any]:
    """Scan roles by D-pad navigation until repeated down presses no longer change the role."""

    for _index in range(max(0, int(reset_up_count))):
        press_up()

    roles: list[str] = []
    observations: list[RoleRecognition] = []
    seen: set[str] = set()
    duplicates: list[dict[str, Any]] = []
    unrecognized: list[dict[str, Any]] = []
    unchanged_count = 0
    previous_key: str | None = None
    reached_bottom = False
    cursor_index = 0
    role_positions: dict[str, int] = {}

    for index in range(max(1, int(max_roles))):
        recognition = _coerce_recognition(current_observer(index))
        key = _recognition_stability_key(recognition)
        is_unchanged = bool(previous_key and key and key == previous_key)
        if index > 0 and is_unchanged:
            unchanged_count += 1
            if unchanged_count >= max(1, int(bottom_repeat_limit)):
                reached_bottom = True
                break
        else:
            if index > 0:
                cursor_index += 1
            unchanged_count = 0
            if not recognition.role_name:
                unrecognized.append({"roster_index": cursor_index, "raw_text": recognition.raw_text})
            elif recognition.role_name in seen:
                duplicates.append({"role_name": recognition.role_name, "roster_index": cursor_index})
            else:
                seen.add(recognition.role_name)
                role_positions[recognition.role_name] = cursor_index
                roles.append(recognition.role_name)
                observations.append(recognition)
        previous_key = key or previous_key
        if index < max_roles - 1:
            press_down()

    expected = [str(role) for role in expected_roles if str(role).strip()]
    return {
        "roles": roles,
        "role_positions": role_positions,
        "current_index": cursor_index,
        "observations": observations,
        "duplicates": duplicates,
        "unrecognized": unrecognized,
        "missing_expected_roles": [role for role in expected if role not in seen],
        "reached_bottom": reached_bottom,
        "navigation": "dpad_current_role",
        "reset_up_count": max(0, int(reset_up_count)),
        "bottom_repeat_limit": max(1, int(bottom_repeat_limit)),
    }


def collect_role_roster_from_role_list(
    expected_roles: list[str] | tuple[str, ...],
    current_observer: Callable[[int], RoleRecognition],
    press_up: Callable[[], None],
    open_role_list: Callable[[], None],
    confirm_selection: Callable[[], None],
    move_right: Callable[[], None],
    reset_up_count: int = DEFAULT_DPAD_RESET_UP_COUNT,
    bottom_repeat_limit: int = DEFAULT_DPAD_BOTTOM_REPEAT_LIMIT,
    max_roles: int = DEFAULT_DPAD_ROLE_LIMIT,
) -> dict[str, Any]:
    """Scan the RS three-column role list and stop as soon as targets are found.

    ``A`` refreshes the current character while leaving the list open, so OCR
    observes each grid position without relying on the unrelated sidebar order.
    """

    for _index in range(max(0, int(reset_up_count))):
        press_up()
    open_role_list()

    expected_order = [str(role).strip() for role in expected_roles if str(role).strip()]
    expected = set(expected_order)
    roles: list[str] = []
    role_positions: dict[str, int] = {}
    observations: list[RoleRecognition] = []
    seen: set[str] = set()
    duplicates: list[dict[str, Any]] = []
    unrecognized: list[dict[str, Any]] = []
    previous_key = ""
    unchanged_count = 0
    current_index = 0
    stop_reason = "max_roles_reached"

    for list_index in range(max(1, int(max_roles))):
        confirm_selection()
        recognition = _coerce_recognition(current_observer(list_index))
        key = _recognition_stability_key(recognition) or "<unrecognized>"
        if list_index > 0 and key == previous_key:
            unchanged_count += 1
            if unchanged_count >= max(1, int(bottom_repeat_limit)):
                stop_reason = "role_list_end_reached"
                break
        else:
            unchanged_count = 0
            current_index = list_index
            if not recognition.role_name:
                unrecognized.append({"roster_index": list_index, "raw_text": recognition.raw_text})
            elif recognition.role_name in seen:
                duplicates.append({"role_name": recognition.role_name, "roster_index": list_index})
            else:
                seen.add(recognition.role_name)
                role_positions[recognition.role_name] = list_index
                roles.append(recognition.role_name)
                observations.append(recognition)

            if expected and expected.issubset(seen):
                stop_reason = "all_required_roles_found"
                break
        previous_key = key
        if list_index < max(1, int(max_roles)) - 1:
            move_right()

    return {
        "roles": roles,
        "role_positions": role_positions,
        "current_index": current_index,
        "observations": observations,
        "duplicates": duplicates,
        "unrecognized": unrecognized,
        "missing_expected_roles": [role for role in expected_order if role not in seen],
        "reached_bottom": stop_reason == "role_list_end_reached",
        "stop_reason": stop_reason,
        "list_open": True,
        "navigation": "rs_role_list_scan",
        "reset_up_count": max(0, int(reset_up_count)),
    }


def plan_role_assembly_from_roster(
    required_roles: list[str] | tuple[str, ...],
    role_roster: dict[str, Any],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    reset_scroll_count: int = DEFAULT_ROLE_PAGE_RESET_SCROLLS,
) -> dict[str, Any]:
    """Build role assembly navigation from a cached full sidebar roster."""

    required = [str(role) for role in required_roles if str(role).strip()]
    slots = map_role_slots(screen_size, content_rect)
    entry = map_role_navigation_controls(screen_size, content_rect)
    scroll = map_role_page_scroll(screen_size, content_rect)["scroll_sequence"]
    reset = map_role_page_reset(screen_size, content_rect, repeat_count=reset_scroll_count)["reset_sequence"]
    roster = [str(role) for role in role_roster.get("roles", []) if str(role).strip()]
    role_indexes = {role: index for index, role in enumerate(roster)}
    bottom_scroll_count = int(role_roster.get("bottom_page_index", max(0, (len(roster) - 1) // 5)) or 0)
    tail_count = len(roster) % len(slots) if slots else 0
    tail_start = len(roster) - tail_count if tail_count else len(roster)
    plans: list[dict[str, Any]] = []

    for role_name in required:
        index = role_indexes.get(role_name)
        if index is None:
            continue
        use_bottom_anchor = bool(tail_count and index >= tail_start)
        if use_bottom_anchor:
            from_bottom = len(roster) - index
            slot_index = max(0, min(len(slots) - 1, len(slots) - from_bottom))
            page_index = "bottom"
            scroll_count = bottom_scroll_count
        else:
            slot_index = index % len(slots)
            page_index = index // len(slots)
            scroll_count = int(page_index)
        action_sequence = [
            *reset,
            *(scroll * scroll_count),
            {"name": "role_slot", "role_name": role_name, "position": slots[slot_index]},
            *entry["entry_sequence"],
            {"name": "assemble_current_role_from_blueprint", "role_name": role_name},
        ]
        plans.append(
            {
                "role_name": role_name,
                "page_index": page_index,
                "slot_index": slot_index,
                "roster_index": index,
                "positioning": "bottom_tail" if use_bottom_anchor else "page_slot",
                "flow": "find_role_then_assemble_blueprint",
                "action_sequence": action_sequence,
            }
        )

    planned_roles = [plan["role_name"] for plan in plans]
    missing = [role for role in required if role not in set(planned_roles)]
    return {
        "required_roles": required,
        "planned_roles": planned_roles,
        "missing_roles": missing,
        "duplicates": list(role_roster.get("duplicates", []) or []),
        "unrecognized": list(role_roster.get("unrecognized", []) or []),
        "role_roster": roster,
        "plans": plans,
        "complete": not missing and not role_roster.get("unrecognized"),
    }


def plan_role_assembly_from_dpad_roster(
    required_roles: list[str] | tuple[str, ...],
    role_roster: dict[str, Any],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    reset_up_count: int = DEFAULT_DPAD_RESET_UP_COUNT,
    current_index: int | None = None,
) -> dict[str, Any]:
    """Build assembly navigation from the D-pad current-role roster."""

    required = [str(role) for role in required_roles if str(role).strip()]
    entry = map_role_navigation_controls(screen_size, content_rect)
    roster = [str(role) for role in role_roster.get("roles", []) if str(role).strip()]
    role_positions = {
        str(role): int(index)
        for role, index in (role_roster.get("role_positions", {}) or {}).items()
        if str(role).strip()
    }
    role_indexes = role_positions or {role: index for index, role in enumerate(roster)}
    ordered_required = sorted(
        (role for role in required if role in role_indexes),
        key=lambda role: role_indexes[role],
    )
    cursor_index = (
        int(current_index)
        if current_index is not None
        else int(role_roster.get("current_index", max(0, len(roster) - 1)) or 0)
    )
    plans: list[dict[str, Any]] = []

    for plan_index, role_name in enumerate(ordered_required):
        index = role_indexes[role_name]
        if plan_index == 0:
            move_sequence = map_dpad_role_move_sequence(cursor_index, index)
            action_sequence = [
                *move_sequence,
                *entry["entry_sequence"],
                {"name": "assemble_current_role_from_blueprint", "role_name": role_name},
                *entry["exit_sequence"],
            ]
            navigation = "sidebar_dpad"
        else:
            move_sequence = map_role_list_grid_move_sequence(cursor_index, index)
            action_sequence = [
                {"name": "open_role_list", "gamepad_button": "rs"},
                *move_sequence,
                {"name": "confirm_role_list_selection", "gamepad_button": "a"},
                {"name": "close_role_list_after_confirmation", "gamepad_button": "b"},
                *entry["entry_sequence"],
                {"name": "assemble_current_role_from_blueprint", "role_name": role_name},
                *entry["exit_sequence"],
            ]
            navigation = "rs_role_list_grid"
        plans.append(
            {
                "role_name": role_name,
                "roster_index": index,
                "start_roster_index": cursor_index,
                "navigation": navigation,
                "flow": "find_role_then_assemble_blueprint",
                "action_sequence": action_sequence,
            }
        )
        cursor_index = index

    planned_roles = [plan["role_name"] for plan in plans]
    missing = [role for role in required if role not in set(planned_roles)]
    return {
        "required_roles": required,
        "planned_roles": planned_roles,
        "missing_roles": missing,
        "duplicates": list(role_roster.get("duplicates", []) or []),
        "unrecognized": list(role_roster.get("unrecognized", []) or []),
        "role_roster": roster,
        "plans": plans,
        "complete": not missing and not role_roster.get("unrecognized"),
        "navigation": "sidebar_then_rs_role_list_grid",
    }


def plan_role_assembly_from_role_list_roster(
    required_roles: list[str] | tuple[str, ...],
    role_roster: dict[str, Any],
    screen_size: tuple[int, int] | None = None,
    content_rect: tuple[int, int, int, int] | None = None,
    current_index: int | None = None,
) -> dict[str, Any]:
    """Plan assembly entirely through the RS three-column character list.

    The initial roster scan leaves the list open. The first target therefore
    only needs grid movement, ``A`` confirmation and ``B`` to close the list;
    every later target reopens the list with ``RS`` before following the same
    route. Targets are visited in actual list order to avoid backtracking.
    """

    required = [str(role) for role in required_roles if str(role).strip()]
    entry = map_role_navigation_controls(screen_size, content_rect)
    roster = [str(role) for role in role_roster.get("roles", []) if str(role).strip()]
    role_positions = {
        str(role): int(index)
        for role, index in (role_roster.get("role_positions", {}) or {}).items()
        if str(role).strip()
    }
    role_indexes = role_positions or {role: index for index, role in enumerate(roster)}
    ordered_required = sorted(
        (role for role in required if role in role_indexes),
        key=lambda role: role_indexes[role],
    )
    cursor_index = (
        int(current_index)
        if current_index is not None
        else int(role_roster.get("current_index", max(0, len(roster) - 1)) or 0)
    )
    list_is_open = bool(role_roster.get("list_open", True))
    plans: list[dict[str, Any]] = []

    for plan_index, role_name in enumerate(ordered_required):
        index = role_indexes[role_name]
        move_sequence = map_role_list_grid_move_sequence(cursor_index, index)
        starts_in_open_list = plan_index == 0 and list_is_open
        action_sequence: list[dict[str, Any]] = []
        if not starts_in_open_list:
            action_sequence.append({"name": "open_role_list", "gamepad_button": "rs"})
        action_sequence.extend(
            [
                *move_sequence,
                {"name": "confirm_role_list_selection", "gamepad_button": "a"},
                {"name": "close_role_list_after_confirmation", "gamepad_button": "b"},
                *entry["entry_sequence"],
                {"name": "assemble_current_role_from_blueprint", "role_name": role_name},
                *entry["exit_sequence"],
            ]
        )
        plans.append(
            {
                "role_name": role_name,
                "roster_index": index,
                "start_roster_index": cursor_index,
                "navigation": "role_list_grid_from_open" if starts_in_open_list else "rs_role_list_grid",
                "flow": "find_role_then_assemble_blueprint",
                "action_sequence": action_sequence,
            }
        )
        cursor_index = index

    planned_roles = [plan["role_name"] for plan in plans]
    missing = [role for role in required if role not in set(planned_roles)]
    return {
        "required_roles": required,
        "planned_roles": planned_roles,
        "missing_roles": missing,
        "duplicates": list(role_roster.get("duplicates", []) or []),
        "unrecognized": list(role_roster.get("unrecognized", []) or []),
        "role_roster": roster,
        "plans": plans,
        "complete": not missing and not role_roster.get("unrecognized"),
        "navigation": "rs_role_list_scan_then_grid",
        "scan_stop_reason": role_roster.get("stop_reason", ""),
    }


def collect_role_observation_pages(
    required_roles: list[str] | tuple[str, ...],
    page_observer: Callable[[int], list[RoleRecognition]],
    scroll_next_page: Callable[[int], None] | None = None,
    max_pages: int | None = None,
    stop_when_all_seen: bool = True,
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
        if stop_when_all_seen and required and required.issubset(seen):
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
