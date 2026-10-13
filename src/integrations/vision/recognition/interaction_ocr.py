# 处理执行界面的 OCR 缩放、裁剪和文字匹配，不拥有游戏输入或角色状态。
from __future__ import annotations

import difflib
import re
from typing import Any

MAX_OCR_INPUT_WIDTH = 1200
MAX_OCR_INPUT_HEIGHT = 900


def prepare_ocr_image(image: Any) -> tuple[Any, float, float]:
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


def best_ocr_line_match(lines: list[dict[str, Any]], target_text: str) -> dict[str, Any] | None:
    normalized_target = normalize_ocr_match_text(target_text)
    if not normalized_target:
        return None
    best_line: dict[str, Any] | None = None
    best_score = 0.0
    for line in lines:
        text = normalize_ocr_match_text(line.get("text"))
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


def normalize_ocr_match_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("%", "百分比")
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", text)


def crop_image_region(image: Any, region: tuple[int, int, int, int]) -> Any | None:
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
