# 识别驱动盘形状模板。
"""Template matching for drive shape and quality recognition."""

from collections import OrderedDict
from pathlib import Path
from types import MappingProxyType
from typing import Union
import cv2
import os
import numpy as np

from src.integrations.vision.recognition.image_resources import ShapeTemplateFile, immutable_template_pixels
from src.integrations.vision.recognition.resources import RecognitionResourceError
from src.services.allocation.inventory import AllocationInventoryProjectionError, legacy_shape_id
from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.utils.logger import logger
from src.utils.image_io import imread_unicode


class ShapeRecognizer:
    """
    基于 OpenCV 模板匹配的形状识别引擎
    """

    def __init__(self, template_dir: str = "config/templates", *,
                 valid_shape_ids: frozenset[str] | None = None):
        self.template_dir = template_dir
        self._templates = MappingProxyType({})
        self.template_files = ()
        self._resized = OrderedDict()
        self._resized_bytes = 0
        # 模板以正式形状映射过滤；模板完整性与每张图片匹配置信度是不同门禁。
        self.valid_shape_ids = (set(valid_shape_ids) if valid_shape_ids is not None
                                else self._load_valid_shape_ids())
        self._load_templates()

    def _load_valid_shape_ids(self) -> set[str]:
        try:
            with StaticGameDataDao() as static_dao:
                return {
                    legacy_shape_id(shape["shape_id"])
                    for shape in static_dao.list_shapes()
                }
        except Exception as exc:
            logger.warning(f"读取官方形状定义失败，将按文件名过滤模板: {exc}")
            return set()

    # 缓存只归此处理器所有；预算指当前缓存持有的像素，不是进程峰值。
    RESIZE_CACHE_BYTES = 4 * 1024 * 1024

    @property
    def templates(self):
        return self._templates

    @property
    def cached_pixel_bytes(self) -> int:
        return self._resized_bytes

    def _load_templates(self) -> None:
        candidate = {}
        evidence = []
        if not os.path.isdir(self.template_dir):
            raise RecognitionResourceError("形状模板目录缺失，请恢复模板后重新解析。")
        # 保留原文件遍历顺序与同分决策；别名冲突拒绝，而不是后读覆盖前读。
        for filename in os.listdir(self.template_dir):
            if not filename.endswith((".png", ".jpg")):
                continue
            shape_id = os.path.splitext(filename)[0]
            if self.valid_shape_ids:
                try:
                    shape_id = legacy_shape_id(shape_id)
                except AllocationInventoryProjectionError:
                    continue
                if shape_id not in self.valid_shape_ids:
                    continue
            elif shape_id.endswith(("_Gold", "_Purple", "_Blue")) or shape_id == "new_tag":
                continue
            if shape_id in candidate:
                raise RecognitionResourceError(f"形状模板正式身份重复：{shape_id}，请保留唯一模板。")
            file, payload = ShapeTemplateFile.read(shape_id, Path(self.template_dir) / filename)
            image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
            if image is None:
                raise RecognitionResourceError(f"形状模板解码失败：{shape_id}，请恢复模板。")
            candidate[shape_id] = immutable_template_pixels(image)
            evidence.append(file)
        # 已有池的显式重载还需保留必需集合，删除模板不成为成功的半套替换。
        missing = self.valid_shape_ids - candidate.keys()
        if self._templates and (missing or not candidate):
            raise RecognitionResourceError("形状模板重载不完整，缺少：" + "、".join(sorted(missing)))
        # 重载在候选全部成功后替换；失败保留旧池和旧缓存，杜绝半套模板。
        self._templates = MappingProxyType(candidate)
        self.template_files = tuple(evidence)
        self._resized.clear()
        self._resized_bytes = 0
        logger.info(f"形状识别器就绪，已加载 {len(candidate)} 个模板。")

    def resized_template(self, shape_id: str, size: tuple[int, int], *,
                         target: np.ndarray) -> np.ndarray:
        width, height = size
        # 尺寸必须来自当前真实搜索图，避免无效/超搜索范围的缩放申请。
        if (not isinstance(width, (int, np.integer)) or isinstance(width, bool)
                or not isinstance(height, (int, np.integer)) or isinstance(height, bool)
                or width <= 0 or height <= 0 or target.ndim != 2
                or width > target.shape[1] or height > target.shape[0]):
            raise ValueError("模板缩放尺寸应为当前搜索图范围内的正整数。")
        key = (shape_id, int(width), int(height))
        cached = self._resized.get(key)
        if cached is not None:
            self._resized.move_to_end(key)
            return cached
        resized = immutable_template_pixels(cv2.resize(self.templates[shape_id], (int(width), int(height))))
        if resized.nbytes <= self.RESIZE_CACHE_BYTES:
            while self._resized and self._resized_bytes + resized.nbytes > self.RESIZE_CACHE_BYTES:
                _key, removed = self._resized.popitem(last=False)
                self._resized_bytes -= removed.nbytes
            self._resized[key] = resized
            self._resized_bytes += resized.nbytes
        return resized

    def require_complete_templates(self) -> None:
        """确认当前解析所需的基础驱动形状模板均可用。

        模板来自仓库界面的截图，而不是游戏图鉴资源；它们的背景和裁切方式
        与识别区域一致。缺少模板时继续解析会把所有驱动误判为 ``Unknown``，
        因此应在创建解析管线时立即中止，而不是在每一张截图上产生失败记录。
        """
        loaded_shape_ids = set(self.templates)
        if self.valid_shape_ids:
            missing_shape_ids = sorted(self.valid_shape_ids - loaded_shape_ids)
            if not missing_shape_ids:
                return
            missing_text = "、".join(missing_shape_ids)
            raise RuntimeError(
                "驱动形状模板不完整，缺少："
                f"{missing_text}。请恢复 config/templates 中对应的基础模板图片后重试。"
            )

        if loaded_shape_ids:
            return
        raise RuntimeError(
            "未加载到任何驱动形状模板。请恢复 config/templates 中的基础模板图片后重试。"
        )

    def recognize(self, image_input: Union[str, np.ndarray]) -> dict:
        """识别形状：支持传入文件路径或灰度矩阵"""
        if isinstance(image_input, str):
            target_gray = imread_unicode(image_input, cv2.IMREAD_GRAYSCALE)
            if target_gray is None:
                raise ValueError(f"无法读取图片: {image_input}")
        else:
            target_gray = image_input

        best_match_shape = "Unknown"
        highest_confidence = -1.0

        for shape_id in self.templates:
            # 相同形状与裁切尺寸复用缩放结果；置信度仍由当前图片逐次匹配。
            target_h, target_w = target_gray.shape
            resized_template = self.resized_template(shape_id, (target_w, target_h), target=target_gray)

            res = cv2.matchTemplate(target_gray, resized_template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(res)

            if max_val > highest_confidence:
                highest_confidence = max_val
                best_match_shape = shape_id

        if highest_confidence < 0.7:
            best_match_shape = "Unknown"

        return {
            "shape_id": best_match_shape,
            "confidence": round(highest_confidence, 2)
        }


if __name__ == "__main__":
    recognizer = ShapeRecognizer(template_dir="../../config/templates")
    test_image = "debug_crops/shape_icon.png"
    if os.path.exists(test_image):
        result = recognizer.recognize(test_image)
        logger.info(f"识别结果: {result['shape_id']} | 置信度: {result['confidence'] * 100:.1f}%")
