# 识别自动装配界面的文字目标、提示区域和亮度状态。
"""Execute drive assembly plans with a mouse backend."""

from __future__ import annotations

import difflib
import re
from typing import Any, Callable

MAX_OCR_INPUT_WIDTH = 1200
MAX_OCR_INPUT_HEIGHT = 900

_OCR_ENGINE_INSTANCE: Any | None = None
_OCR_ENGINE_FACTORY: Callable[[], Any] | None = None


from src.features.drive_assembly.input_backends import MouseBackend


def _click_ocr_target(action: dict[str, Any], backend: MouseBackend) -> bool:
    position = _find_ocr_target_position(action, backend)
    if position is None and action.get("fallback_position"):
        position = _point(action["fallback_position"])
    if position is None:
        return False
    backend.click(position)
    return True


def _find_ocr_target_position(action: dict[str, Any], backend: MouseBackend) -> tuple[int, int] | None:
    capture = getattr(backend, "screenshot", None)
    if capture is None:
        return None
    try:
        image = capture()
    except Exception:
        return None
    region = action.get("ocr_search_region")
    if not region:
        return None
    cropped = _crop_image_region(image, _region(region))
    if cropped is None:
        return None
    try:
        ocr_image, scale_x, scale_y = _prepare_ocr_image(cropped)
        lines = _get_ocr_engine().extract_lines(ocr_image)
    except Exception:
        return None
    target_text = str(action.get("ocr_target_text") or "")
    match = _best_ocr_line_match(lines, target_text)
    if not match:
        return None
    x1, y1, x2, y2 = _region(region)
    bx1, by1, bx2, by2 = match["box"]
    crop_x = int((bx1 + bx2) / 2 / max(scale_x, 0.0001))
    crop_y = int((by1 + by2) / 2 / max(scale_y, 0.0001))
    return (x1 + crop_x, y1 + crop_y)


def _prepare_ocr_image(image: Any) -> tuple[Any, float, float]:
    import numpy as np

    array = np.asarray(image)
    if array.ndim < 2 or array.size == 0:
        return array, 1.0, 1.0
    if array.ndim == 3:
        array = array[..., :3]
    if array.dtype != np.uint8:
        array = np.clip(array, 0, 255).astype(np.uint8)
    height, width = array.shape[:2]
    if width <= 0 or height <= 0:
        return array, 1.0, 1.0
    scale = min(1.0, MAX_OCR_INPUT_WIDTH / width, MAX_OCR_INPUT_HEIGHT / height)
    if scale >= 1.0:
        return array, 1.0, 1.0
    new_width = max(1, int(width * scale))
    new_height = max(1, int(height * scale))
    try:
        import cv2

        resized = cv2.resize(array, (new_width, new_height), interpolation=cv2.INTER_AREA)
    except Exception:
        from PIL import Image

        resized = np.asarray(Image.fromarray(array).resize((new_width, new_height)))
    return resized, new_width / width, new_height / height


def _get_ocr_engine() -> Any:
    global _OCR_ENGINE_INSTANCE
    if _OCR_ENGINE_INSTANCE is None:
        if _OCR_ENGINE_FACTORY is not None:
            _OCR_ENGINE_INSTANCE = _OCR_ENGINE_FACTORY()
        else:
            from src.scanner.ocr_engine import OCREngine

            _OCR_ENGINE_INSTANCE = OCREngine()
    return _OCR_ENGINE_INSTANCE


def _best_ocr_line_match(lines: list[dict[str, Any]], target_text: str) -> dict[str, Any] | None:
    normalized_target = _normalize_ocr_match_text(target_text)
    if not normalized_target:
        return None
    best_line: dict[str, Any] | None = None
    best_score = 0.0
    for line in lines:
        text = _normalize_ocr_match_text(line.get("text"))
        if not text:
            continue
        if normalized_target in text or text in normalized_target:
            score = 1.0
        else:
            score = difflib.SequenceMatcher(None, normalized_target, text).ratio()
        if score > best_score:
            best_score = score
            best_line = line
    return best_line if best_score >= 0.55 else None


def _normalize_ocr_match_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("%", "百分比")
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def _crop_image_region(image: Any, region: tuple[int, int, int, int]) -> Any | None:
    x1, y1, x2, y2 = region
    if x1 >= x2 or y1 >= y2:
        return None
    try:
        if hasattr(image, "crop"):
            return image.crop((x1, y1, x2, y2))
    except Exception:
        return None
    try:
        import numpy as np

        array = np.asarray(image)
        if array.ndim < 2 or array.size == 0:
            return None
        height, width = array.shape[:2]
        x1 = max(0, min(width, x1))
        x2 = max(0, min(width, x2))
        y1 = max(0, min(height, y1))
        y2 = max(0, min(height, y2))
        if x1 >= x2 or y1 >= y2:
            return None
        return array[y1:y2, x1:x2]
    except Exception:
        return None


def _maybe_click_optional_confirm(action: dict[str, Any], backend: MouseBackend) -> bool:
    if not _optional_prompt_visible(action, backend):
        return False
    backend.click(_point(action["optional_confirm_position"]))
    return True


def _optional_prompt_visible(action: dict[str, Any], backend: MouseBackend) -> bool:
    capture = getattr(backend, "screenshot", None)
    if capture is None:
        return False
    try:
        image = capture()
    except Exception:
        return False
    probe = action.get("modal_probe_position")
    if not probe:
        return False
    threshold = float(action.get("brightness_threshold") or 150)
    return _region_brightness(image, _point(probe), radius=28) >= threshold


def _region_brightness(image: Any, center: tuple[int, int], radius: int = 20) -> float:
    x, y = center
    try:
        import numpy as np

        if hasattr(image, "__array__"):
            array = np.asarray(image)
            if array.ndim < 2 or array.size == 0:
                return 0.0
            height, width = array.shape[:2]
            x1, x2 = max(0, x - radius), min(width, x + radius + 1)
            y1, y2 = max(0, y - radius), min(height, y + radius + 1)
            patch = array[y1:y2, x1:x2]
            if patch.size == 0:
                return 0.0
            if patch.ndim == 3:
                patch = patch[..., :3]
            return float(np.mean(patch))
    except Exception:
        pass
    try:
        width, height = image.size
        x1, x2 = max(0, x - radius), min(width, x + radius + 1)
        y1, y2 = max(0, y - radius), min(height, y + radius + 1)
        values: list[float] = []
        for py in range(y1, y2):
            for px in range(x1, x2):
                pixel = image.getpixel((px, py))
                if isinstance(pixel, int):
                    values.append(float(pixel))
                else:
                    channels = pixel[:3]
                    values.append(sum(float(channel) for channel in channels) / len(channels))
        return sum(values) / len(values) if values else 0.0
    except Exception:
        return 0.0


def _point(value: Any) -> tuple[int, int]:
    x, y = value
    return int(x), int(y)


def _region(value: Any) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = value
    return int(x1), int(y1), int(x2), int(y2)
