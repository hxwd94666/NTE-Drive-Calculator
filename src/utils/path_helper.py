"""Path helpers that keep source and packaged builds aligned."""

import os
import sys
from pathlib import Path


def get_base_path():
    """返回项目根目录，兼容打包与开发环境"""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent.parent
