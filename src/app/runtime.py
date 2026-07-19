# 保存运行时路径和当前账号状态。
"""Mutable application runtime paths shared across feature modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any

ROOT: Path
APP_DIR: Path
BUNDLED_CONFIG_DIR: Path
ASSET_DIR: Path
APP_ICON_PATH: Path
DATA_ROOT: Path
CONFIG_DIR: Path
ACCOUNTS_DIR: Path
ACCOUNTS_INDEX_FILE: Path
ACCOUNT_DATA_ROOT: Path
USER_DATABASE_PATH: Path
USER_CONFIG_DIR: Path
TEMPLATE_DIR: Path
OUTPUT_FILE: Path
SCREENSHOT_DIR: Path
LOG_DIR: Path
ACTIVE_ACCOUNT_ID = "default"
ACTIVE_ACCOUNT_NAME = "默认账号"


def configure(
    *,
    root: Path,
    app_dir: Path,
    data_root: Path,
    bundled_config_dir: Path,
    asset_dir: Path,
    app_icon_path: Path,
) -> None:
    global ROOT, APP_DIR, BUNDLED_CONFIG_DIR, ASSET_DIR, APP_ICON_PATH
    global DATA_ROOT, CONFIG_DIR, ACCOUNTS_DIR, ACCOUNTS_INDEX_FILE
    global ACCOUNT_DATA_ROOT, USER_DATABASE_PATH, USER_CONFIG_DIR, TEMPLATE_DIR, OUTPUT_FILE
    global SCREENSHOT_DIR, LOG_DIR

    ROOT = root
    APP_DIR = app_dir
    DATA_ROOT = data_root
    BUNDLED_CONFIG_DIR = bundled_config_dir
    ASSET_DIR = asset_dir
    APP_ICON_PATH = app_icon_path
    CONFIG_DIR = DATA_ROOT / "config"
    ACCOUNTS_DIR = DATA_ROOT / "accounts"
    ACCOUNTS_INDEX_FILE = ACCOUNTS_DIR / "accounts.json"
    ACCOUNT_DATA_ROOT = ACCOUNTS_DIR / ACTIVE_ACCOUNT_ID
    USER_DATABASE_PATH = ACCOUNT_DATA_ROOT / "user_data.sqlite3"
    USER_CONFIG_DIR = ACCOUNT_DATA_ROOT / "config"
    TEMPLATE_DIR = CONFIG_DIR / "templates"
    OUTPUT_FILE = USER_CONFIG_DIR / "real_inventory.json"
    SCREENSHOT_DIR = ACCOUNT_DATA_ROOT / "scanned_images"
    LOG_DIR = ACCOUNT_DATA_ROOT / "logs"


def apply_account_state(state: Any) -> None:
    global ACTIVE_ACCOUNT_ID, ACTIVE_ACCOUNT_NAME, ACCOUNT_DATA_ROOT, USER_DATABASE_PATH
    global USER_CONFIG_DIR, OUTPUT_FILE, SCREENSHOT_DIR, LOG_DIR

    ACTIVE_ACCOUNT_ID = state.active_account_id
    ACTIVE_ACCOUNT_NAME = state.active_account_name
    ACCOUNT_DATA_ROOT = state.account_data_root
    USER_DATABASE_PATH = state.user_database_path
    USER_CONFIG_DIR = state.user_config_dir
    OUTPUT_FILE = state.output_file
    SCREENSHOT_DIR = state.screenshot_dir
    LOG_DIR = state.log_dir
