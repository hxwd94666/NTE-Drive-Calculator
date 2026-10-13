# 持有单次 OCR 实例，统一灰度输入、文本投影与原生返回后的取消检查。
from __future__ import annotations

from concurrent.futures import CancelledError

import cv2
import numpy as np

from src.integrations.vision.recognition.backend_runtime import (
    CancelCheck,
    check_cancel,
    create_ocr_engine,
)
from src.integrations.vision.recognition.model_resources import OcrModelResourceError
from src.utils.exceptions import OCRParseError
from src.utils.logger import logger


class OCREngine:
    """实例由调用者持有，本入口不创建全局缓存；执行后端不等同于输入语言。"""

    def __init__(self, backend_preference: str | None = None, *,
                 cancel_check: CancelCheck = None):
        self.cancel_check = cancel_check
        logger.info('正在初始化 OCR 引擎...')
        self.ocr, engine_type, self.runtime_resources = create_ocr_engine(
            backend_preference, cancel_check=cancel_check)
        check_cancel(cancel_check)
        logger.success(f'OCR 引擎就绪 [后端: {engine_type}]')

    def _extract(self, image_input: np.ndarray, *, include_boxes: bool) -> list:
        cancel_check = getattr(self, 'cancel_check', None)
        check_cancel(cancel_check)
        try:
            final_input = cv2.cvtColor(image_input, cv2.COLOR_GRAY2BGR) if len(image_input.shape) == 2 else image_input
            check_cancel(cancel_check)
            self.runtime_resources.verify_decoder(self.ocr)
            try:
                results, _ = self.ocr(final_input)
            finally:
                # 底层推理不可抢占；即使推理抛错，也先阻止已取消结果进入后续解析。
                check_cancel(cancel_check)
                self.runtime_resources.verify_decoder(self.ocr)
            output = []
            for line in results or []:
                if not include_boxes:
                    output.append(str(line[1]).strip())
                    continue
                if len(line) < 2:
                    continue
                text = str(line[1]).strip()
                if not text:
                    continue
                try:
                    xs = [float(point[0]) for point in line[0]]
                    ys = [float(point[1]) for point in line[0]]
                    rect = (int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys)))
                except Exception:
                    rect = (0, 0, final_input.shape[1], final_input.shape[0])
                output.append({'text': text, 'box': rect})
            check_cancel(cancel_check)
            return output
        except (CancelledError, OcrModelResourceError):
            raise
        except Exception as exc:
            check_cancel(cancel_check)
            logger.error(f'OCR 解析失败: {exc}')
            raise OCRParseError(f'OCR 引擎异常: {exc}') from exc

    def extract_text(self, image_input: np.ndarray) -> list:
        return self._extract(image_input, include_boxes=False)

    def extract_lines(self, image_input: np.ndarray) -> list[dict]:
        return self._extract(image_input, include_boxes=True)

    def identify_item_type(self, identity_image_input: np.ndarray) -> str:
        full_text = ''.join(self.extract_text(identity_image_input))
        return 'drive' if any(char in full_text for char in ('驱', '动', '型', 'I')) else 'tape'
