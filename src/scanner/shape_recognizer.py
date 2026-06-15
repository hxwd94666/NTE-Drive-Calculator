# 识别驱动盘形状模板。
"""Template matching for drive shape and quality recognition."""

from typing import Union
import cv2
import os
import json
import numpy as np

from src.utils.logger import logger
from src.utils.image_io import imread_unicode


class ShapeRecognizer:
    """
    基于 OpenCV 模板匹配的形状识别引擎
    """

    def __init__(self, template_dir: str = "config/templates"):
        self.template_dir = template_dir
        self.templates = {}
        self.valid_shape_ids = self._load_valid_shape_ids()
        self._load_templates()

    def _load_valid_shape_ids(self) -> set[str]:
        shapes_path = os.path.join(os.path.dirname(self.template_dir), "shapes.json")
        if not os.path.exists(shapes_path):
            return set()
        try:
            with open(shapes_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {
                item.get("shape_id")
                for item in data.get("shapes", [])
                if item.get("shape_id") and item.get("shape_id") != "TAPE_15"
            }
        except Exception as exc:
            logger.warning(f"读取形状定义失败，将按文件名过滤模板: {exc}")
            return set()

    def _load_templates(self):
        """将 12 种标准形状的图片加载进内存"""
        if not os.path.exists(self.template_dir):
            os.makedirs(self.template_dir)
            logger.warning(f"模板文件夹 {self.template_dir} 不存在，已自动创建。请放入形状模板图片。")
            return

        for filename in os.listdir(self.template_dir):
            if filename.endswith((".png", ".jpg")):
                shape_id = os.path.splitext(filename)[0]
                if self.valid_shape_ids:
                    if shape_id not in self.valid_shape_ids:
                        continue
                elif shape_id.endswith(("_Gold", "_Purple", "_Blue")) or shape_id == "new_tag":
                    continue
                filepath = os.path.join(self.template_dir, filename)

                template_img = imread_unicode(filepath, cv2.IMREAD_GRAYSCALE)
                if template_img is not None:
                    self.templates[shape_id] = template_img

        logger.info(f"形状识别器就绪，已加载 {len(self.templates)} 个模板。")

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

        for shape_id, template in self.templates.items():
            # 将模板动态缩放到与目标图片一致的尺寸
            target_h, target_w = target_gray.shape
            resized_template = cv2.resize(template, (target_w, target_h))

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
        print(f"识别结果: {result['shape_id']} | 置信度: {result['confidence'] * 100:.1f}%")
