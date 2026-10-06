# 在同一只读事务内冻结本账号练度导出所需的角色记录。
from __future__ import annotations

from typing import Any

from .protocols import UserDataDaoMixinHost


class PracticeShareDaoMixin(UserDataDaoMixinHost):
    def read_practice_share_context(self, character_ids: tuple[int, ...], *,
                                    include_unequipped: bool = False) -> dict[str, Any]:
        connection = self._db()
        if connection.in_transaction:
            raise RuntimeError('练度读取需独立的只读事务')
        connection.execute('BEGIN')
        try:
            if include_unequipped:
                return {'profiles': self.list_character_profiles(include_inactive=True),
                        'observations': self.list_native_character_profile_observations(),
                        'observed_ids': self.list_observed_character_ids()}
            return {
                'profiles': [profile for identity in character_ids
                             if (profile := self.get_character_profile(identity)) is not None],
                'observations': [record for identity in character_ids
                                 if (record := self.get_native_character_profile_observation(identity)) is not None],
            }
        finally:
            connection.rollback()
