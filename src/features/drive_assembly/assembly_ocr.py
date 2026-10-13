# 识别自动装配界面的文字目标、提示区域和亮度状态。
"""Execute drive assembly plans with a mouse backend."""

from __future__ import annotations

from typing import Any, Callable
from concurrent.futures import CancelledError

from src.integrations.vision.recognition.interaction_ocr import (
    prepare_ocr_image, best_ocr_line_match, crop_image_region,
)
from src.integrations.vision.recognition.model_resources import OcrModelResourceError
from src.integrations.vision.recognition.task_session import OcrTaskSession, OcrTaskStateError


from src.features.drive_assembly.input_backends import MouseBackend


def _click_ocr_target(action: dict[str, Any], backend: MouseBackend,
                      session: OcrTaskSession,
                      should_stop: Callable[[], bool] | None = None) -> bool:
    position = _find_ocr_target_position(action, backend, session)
    if position is None and action.get("fallback_position"):
        position = _point(action["fallback_position"])
    if position is None:
        return False
    session.check_current(should_stop=should_stop)
    backend.click(position)
    return True


def _find_ocr_target_position(action: dict[str, Any], backend: MouseBackend,
                              session: OcrTaskSession) -> tuple[int, int] | None:
    capture = getattr(backend, "screenshot", None)
    if capture is None:
        return None
    try:
        image = capture()
    except (CancelledError, OcrModelResourceError, OcrTaskStateError):
        session.invalidate()
        raise
    except Exception:
        return None
    region = action.get("ocr_search_region")
    if not region:
        return None
    cropped = crop_image_region(image, _region(region))
    if cropped is None:
        return None
    try:
        ocr_image, scale_x, scale_y = prepare_ocr_image(cropped)
        lines = session.extract_lines(ocr_image)
    except (CancelledError, OcrModelResourceError, OcrTaskStateError):
        session.invalidate()
        raise
    except Exception:
        return None
    target_text = str(action.get("ocr_target_text") or "")
    match = best_ocr_line_match(lines, target_text)
    if not match:
        return None
    x1, y1, x2, y2 = _region(region)
    bx1, by1, bx2, by2 = match["box"]
    crop_x = int((bx1 + bx2) / 2 / max(scale_x, 0.0001))
    crop_y = int((by1 + by2) / 2 / max(scale_y, 0.0001))
    return (x1 + crop_x, y1 + crop_y)


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
