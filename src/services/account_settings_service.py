# 合并只读静态默认设置与账号数据库中的用户设置副本。
"""Account-scoped application settings backed by the two SQLite databases."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.storage.sqlite.static_game_data_dao import StaticGameDataDao
from src.storage.sqlite.user_data_dao import UserDataDao, UserDataValidationError
from src.utils.logger import logger


SETTING_GROUPS = frozenset({"sync", "hotkeys", "update", "ui"})

# These are account preferences rather than game data.  Keep them here so an
# older bundled static database can still supply the remaining UI defaults.
_UI_RUNTIME_DEFAULTS = {
    "protagonist_game_name": "",
    "skip_protagonist_name_prompt": False,
    "equipment_plugin_game_executable": "",
    "equipment_plugin_dll_source": "",
    "equipment_plugin_backup_path": "",
    "equipment_plugin_deployed_sha256": "",
}


class AccountSettingsService:
    """Read official defaults and persist only account-specific group copies."""

    def __init__(
        self,
        user_database_path: str | Path,
        *,
        static_database_path: str | Path | None = None,
        legacy_config_dir: str | Path | None = None,
    ) -> None:
        self.user_database_path = Path(user_database_path).expanduser().resolve()
        self.static_database_path = static_database_path
        self.legacy_config_dir = (
            Path(legacy_config_dir).expanduser().resolve()
            if legacy_config_dir is not None
            else None
        )

    def _defaults(self) -> dict[str, dict[str, Any]]:
        with StaticGameDataDao(self.static_database_path) as dao:
            defaults = dao.application_setting_defaults()
        missing = SETTING_GROUPS.difference(defaults)
        if missing:
            raise RuntimeError(
                "静态数据库缺少设置默认值：" + "、".join(sorted(missing))
            )
        return defaults

    def load(self, setting_key: str) -> dict[str, Any]:
        key = self._setting_key(setting_key)
        defaults = self._defaults()[key]
        with UserDataDao(self.user_database_path) as dao:
            account_copy = dao.list_application_setting_copies().get(key, {})
        return self._normalize(key, {**defaults, **account_copy}, defaults)

    def save(
        self, setting_key: str, value: Mapping[str, Any]
    ) -> dict[str, Any]:
        key = self._setting_key(setting_key)
        defaults = self._defaults()[key]
        normalized = self._normalize(key, {**defaults, **dict(value)}, defaults)
        with UserDataDao(self.user_database_path) as dao:
            if normalized == self._normalize(key, defaults, defaults):
                dao.delete_application_setting_copy(key)
            else:
                dao.replace_application_setting_copy(key, normalized)
        return normalized

    def migrate_legacy_settings(self) -> None:
        """Import pre-v8 JSON/typed settings once, without retaining default copies."""

        defaults = self._defaults()
        with UserDataDao(self.user_database_path) as dao:
            if dao.legacy_application_settings_imported():
                return
            existing = dao.list_application_setting_copies()
            candidates: dict[str, Mapping[str, Any]] = {}

            if "sync" not in existing:
                legacy_sync = dao.get_sync_settings()
                legacy_sync.pop("updated_at_utc", None)
                candidates["sync"] = legacy_sync

            if self.legacy_config_dir is not None:
                legacy_files = {
                    "hotkeys": "hotkeys.json",
                    "update": "update_config.json",
                    "ui": "ui_preferences.json",
                }
                for key, filename in legacy_files.items():
                    if key in existing:
                        continue
                    value = self._read_legacy_json(
                        self.legacy_config_dir / filename
                    )
                    if value is not None:
                        candidates[key] = value

            for key, value in candidates.items():
                normalized = self._normalize(
                    key, {**defaults[key], **dict(value)}, defaults[key]
                )
                if normalized != self._normalize(key, defaults[key], defaults[key]):
                    dao.replace_application_setting_copy(key, normalized)
            dao.mark_legacy_application_settings_imported()

    @staticmethod
    def _read_legacy_json(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"迁移旧设置文件失败，已跳过: {path} | {exc}")
            return None
        if not isinstance(value, dict):
            logger.warning(f"迁移旧设置文件失败，内容不是对象: {path}")
            return None
        return value

    @staticmethod
    def _setting_key(setting_key: str) -> str:
        key = str(setting_key).strip()
        if key not in SETTING_GROUPS:
            raise UserDataValidationError(f"未知设置分组：{key or '<空>'}")
        return key

    @classmethod
    def _normalize(
        cls,
        key: str,
        value: Mapping[str, Any],
        defaults: Mapping[str, Any],
    ) -> dict[str, Any]:
        effective_defaults = dict(defaults)
        if key == "ui":
            effective_defaults.update(_UI_RUNTIME_DEFAULTS)
        normalized = {
            name: value.get(name, default)
            for name, default in effective_defaults.items()
        }
        if key == "sync":
            for name in ("inventory_sync_method", "equipment_apply_method"):
                method = str(normalized[name]).strip()
                if method not in {"nte_core", "gamepad"}:
                    raise UserDataValidationError(f"{name} 必须是 nte_core 或 gamepad")
                normalized[name] = method
            device = normalized.get("capture_device_id")
            normalized["capture_device_id"] = (
                str(device).strip() or None if device is not None else None
            )
            normalized["raw_capture_enabled"] = bool(
                normalized["raw_capture_enabled"]
            )
            normalized["auto_start_inventory_sync"] = bool(
                normalized["auto_start_inventory_sync"]
            )
            try:
                settle = float(normalized["inventory_settle_seconds"])
            except (TypeError, ValueError) as exc:
                raise UserDataValidationError(
                    "inventory_settle_seconds 必须是数值"
                ) from exc
            if not math.isfinite(settle) or settle <= 0:
                raise UserDataValidationError(
                    "inventory_settle_seconds 必须大于 0"
                )
            normalized["inventory_settle_seconds"] = settle
            retention = normalized["inventory_snapshot_retention_count"]
            if isinstance(retention, bool):
                raise UserDataValidationError(
                    "inventory_snapshot_retention_count 必须是正整数"
                )
            try:
                retention = int(retention)
            except (TypeError, ValueError) as exc:
                raise UserDataValidationError(
                    "inventory_snapshot_retention_count 必须是正整数"
                ) from exc
            if retention < 1:
                raise UserDataValidationError(
                    "inventory_snapshot_retention_count 必须是正整数"
                )
            normalized["inventory_snapshot_retention_count"] = retention
        elif key == "hotkeys":
            for name in ("capture", "finish", "stop"):
                hotkey = str(normalized[name]).strip()
                if not hotkey:
                    raise UserDataValidationError(f"{name} 快捷键不能为空")
                normalized[name] = hotkey
        elif key == "update":
            normalized["never_remind"] = bool(normalized["never_remind"])
            normalized["ignored_version"] = str(
                normalized["ignored_version"] or ""
            ).strip()
        elif key == "ui":
            for name in (
                "log_enabled",
                "skip_unsaved_allocation_prompt",
                "skip_automatic_assembly_duplicate_warning",
                "full_scan_dual_thread_processing",
                "full_scan_amd_compatibility",
            ):
                normalized[name] = bool(normalized[name])
            theme = str(normalized["theme"]).strip()
            if theme not in {"dark", "black", "light"}:
                raise UserDataValidationError("theme 必须是 dark、black 或 light")
            normalized["theme"] = theme
            if normalized["full_scan_amd_compatibility"]:
                normalized["full_scan_dual_thread_processing"] = False
            normalized["protagonist_game_name"] = str(
                normalized.get("protagonist_game_name") or ""
            ).strip()
            normalized["skip_protagonist_name_prompt"] = bool(
                normalized.get("skip_protagonist_name_prompt", False)
            )
            for name in (
                "equipment_plugin_game_executable",
                "equipment_plugin_dll_source",
                "equipment_plugin_backup_path",
                "equipment_plugin_deployed_sha256",
            ):
                normalized[name] = str(normalized.get(name) or "").strip()
        return normalized
