# 定义应用版本的单一事实源。
"""应用版本信息。"""

import re

__version__ = "2.4.0"
# Python 包元数据遵守 PEP 440；应用与安装器仍显示用户约定的日期测试版。
__package_version__ = re.sub(r"\.((?:\d{8}|\d{12}))T$", r".dev\1", __version__).replace(".T", ".dev")


def version_order_key(value: str) -> tuple[int, int, int, int, int]:
    """同基线下日期测试版先按日期排序，正式版高于该基线测试版。"""
    text = str(value).strip()
    parts = [int(item) for item in re.findall(r"\d+", text)]
    major, minor, patch = (parts + [0, 0, 0])[:3]
    test = re.fullmatch(r"v?\d+\.\d+\.\d+\.(?:T(\d{6})|((?:\d{8}|\d{12}))T)", text, re.IGNORECASE)
    return major, minor, patch, 0 if test else 1, int(test[1] or test[2]) if test else 0


def windows_numeric_version(value: str) -> str:
    """安装器数字资源版本不包含测试标签；显示版本保留完整字符串。"""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:\.(?:T\d{6}|(?:\d{8}|\d{12})T))?", value)
    if not match or any(int(part) > 65535 for part in match.groups()):
        raise ValueError(f"不支持的 Windows 安装器版本：{value}")  # ui-text: internal
    return ".".join(str(int(part)) for part in match.groups()) + ".0"
