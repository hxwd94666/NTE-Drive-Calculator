# 在账号事务内保存家具值及来源，原生观测永不覆盖手动保存或旧版显式配置。
from collections.abc import Callable, Mapping
import math

from src.domain.official_role.models import WORLD_BONUS_SETTING_KEY, WorldBonusSettings
from src.storage.sqlite.user_data_support import UserDataValidationError


class WorldBonusDaoMixin:
    def save_world_bonus_fields(self, fields: Mapping[str, float], *, source: str,
                               check: Callable[[], None] = lambda: None) -> bool:
        if source not in {'manual', 'native'}:
            raise UserDataValidationError('家具设置来源无效')
        defaults = WorldBonusSettings().to_payload()
        if any(key not in defaults or type(value) not in (int, float)
               or not math.isfinite(value) or not 0 <= value <= defaults[key]
               for key, value in fields.items()):
            raise UserDataValidationError('家具设置字段或数值无效')
        if not fields:
            return False
        check()
        with self.write_transaction():
            current = self.list_application_setting_copies().get(WORLD_BONUS_SETTING_KEY)
            # 旧版本只保存过显式手动非默认值；缺来源的旧记录必须按手动保护。
            if source == 'native' and current is not None and current.get('source') != 'native':
                check()
                return False
            values = WorldBonusSettings.from_payload(current).to_payload()
            values.update(fields)
            payload = {**values, 'version': 2, 'source': source}
            changed = payload != current
            if changed:
                self._write_application_setting(WORLD_BONUS_SETTING_KEY, payload)
            # 包含无变化与手动保护路径的最终账号/取消核对，不提前提交。
            check()
        return changed
