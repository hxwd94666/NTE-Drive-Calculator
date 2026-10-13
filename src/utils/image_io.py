# 提供支持中文路径的图像读取工具。
"""Unicode-safe image loading helpers for Windows paths."""

from pathlib import Path

import cv2
import numpy as np


def imread_unicode(path: str | Path, flags=cv2.IMREAD_COLOR):
    """以字节读取支持中文路径；空文件返回 None，解码结果仍由调用方核对。"""
    data = np.fromfile(str(path), dtype=np.uint8)
    if data.size == 0:
        return None
    return cv2.imdecode(data, flags)
