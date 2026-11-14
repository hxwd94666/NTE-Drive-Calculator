# 只管理当前账号家具属性的读取和单库持久化。
from __future__ import annotations

from pathlib import Path

from src.domain.official_role.models import WORLD_BONUS_SETTING_KEY, WorldBonusSettings
from src.storage.sqlite.user_data_dao import UserDataDao


class WorldBonusSettingsService:
    """Persist world bonuses in the active account's setting-copy table."""

    def __init__(self, user_database_path: str | Path) -> None:
        self.user_database_path = Path(user_database_path).expanduser().resolve()

    def load(self) -> WorldBonusSettings:
        with UserDataDao(self.user_database_path) as dao:
            payload = dao.list_application_setting_copies().get(
                WORLD_BONUS_SETTING_KEY
            )
        return WorldBonusSettings.from_payload(payload)

    def save(self, settings: WorldBonusSettings) -> WorldBonusSettings:
        with UserDataDao(self.user_database_path) as dao:
            # 即使手动保存满值也记录来源，不再用删除记录表达默认值。
            dao.save_world_bonus_fields(settings.to_payload(), source="manual")
        return settings

    def apply_native(self, fields, *, check) -> bool:
        with UserDataDao(self.user_database_path) as dao:
            return dao.save_world_bonus_fields(fields, source="native", check=check)
