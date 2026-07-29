# 识别角色列表槽位和当前角色名称并处理重复匹配。
"""Role recognition and traversal planning for drive assembly."""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.utils.image_io import imread_unicode
from src.utils.name_resolver import normalize_name, resolve_name
from src.features.drive_assembly.role_contracts import RoleRecognition
from src.features.drive_assembly.role_flow_helpers import (
    _best_template_score,
    _crop,
    _normalize_role_alias,
    _template_score,
)




from src.features.drive_assembly.role_navigation_mapping import (
    map_current_role_name_region,
    map_role_slot_template_regions,
)

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


def _resolve_yi_ocr_fallback(
    text_parts: list[str],
    expected_roles: list[str] | tuple[str, ...],
) -> str | None:
    """Identify 翳 from an otherwise unmatched OCR fragment containing 医/醫.

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

    repeated_role = _resolve_repeated_role_name(sources, candidates)
    if repeated_role:
        return repeated_role, "ocr_repeated_name", 1.0

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


def _resolve_repeated_role_name(
    sources: list[str],
    candidates: list[str],
) -> str | None:
    """Resolve the unique role name repeated inside an OCR result.

    The primary and expanded name crops can overlap, and the game UI can add
    level or other text.  Thus ``海月海月S云`` and ``浔女浔`` both carry the
    same reliable signal: a unique canonical name appears at least twice.
    This is data-driven over the active role candidates, not a per-name OCR map.
    """

    matches: set[str] = set()
    for source in sources:
        source_key = normalize_name(source)
        for role_name in candidates:
            role_key = normalize_name(role_name)
            if role_key and source_key.count(role_key) >= 2:
                matches.add(role_name)
    return next(iter(matches)) if len(matches) == 1 else None


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
    # The expanded crop contains the primary crop.  Evaluate it on its own first
    # so a valid name is not doubled in diagnostics (for example 娜娜莉娜娜莉6).
    fallback_result = resolve_role_recognition(fallback_texts, expected_roles)
    if fallback_result.role_name:
        return _normalize_role_alias(RoleRecognition(
            fallback_result.role_name,
            "ocr_fallback",
            fallback_result.confidence,
            fallback_result.raw_text,
        ), role_aliases)

    # Retain a final combined attempt for split OCR fragments.  This is also
    # where repeated one-character names such as 浔女浔 are resolved.
    combined_texts = list(primary_texts or []) + list(fallback_texts or [])
    combined_result = resolve_role_recognition(combined_texts, expected_roles)
    return _normalize_role_alias(combined_result, role_aliases)
