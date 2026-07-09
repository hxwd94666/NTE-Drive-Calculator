# 读取和保存全局热键配置。
"""Helpers for loading and saving user hotkey preferences."""

from __future__ import annotations

import json
from pathlib import Path

from src.utils.logger import logger


DEFAULT_HOTKEYS = {
    "capture": "F9",
    "finish": "F10",
    "stop": "F12",
}


def load_hotkey_config(config_dir: Path) -> dict[str, str]:
    path = config_dir / "hotkeys.json"
    hotkeys = dict(DEFAULT_HOTKEYS)
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                hotkeys["capture"] = str(data.get("capture", hotkeys["capture"]))
                hotkeys["finish"] = str(data.get("finish", hotkeys["finish"]))
                hotkeys["stop"] = str(data.get("stop", hotkeys["stop"]))
    except Exception as exc:
        logger.warning(f"读取热键配置失败，使用默认热键: {path} | {exc}")
    return hotkeys


def save_hotkey_config(config_dir: Path, capture: str, finish: str, stop: str) -> None:
    path = config_dir / "hotkeys.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "capture": capture,
                "finish": finish,
                "stop": stop,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
