# 持久化当前账号的角色养成指针并提供明确的重置边界。
"""Account-scoped official-role profile write service."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from src.storage.sqlite.user_data_dao import UserDataDao


@dataclass(frozen=True, slots=True)
class OfficialRoleProfileUpdate:
    character_id: int
    character_level: int
    breakthrough_stage: int
    awakening_level: int
    fork_id: str | None
    fork_level: int | None
    fork_refinement_level: int | None
    selected_skill_id: str | None
    skill_levels: dict[str, int]
    ordinal: int


class OfficialRoleProfileService:
    """Own role-profile transactions without depending on Qt widgets."""

    def __init__(
        self,
        user_database_path: str | Path,
        *,
        dao_factory: type[UserDataDao] = UserDataDao,
    ) -> None:
        self._user_database_path = Path(user_database_path)
        self._dao_factory = dao_factory

    def save_profiles(
        self, updates: Sequence[OfficialRoleProfileUpdate]
    ) -> int:
        with self._dao_factory(self._user_database_path) as dao:
            for update in updates:
                dao.save_character_profile(
                    character_id=update.character_id,
                    character_level=update.character_level,
                    breakthrough_stage=update.breakthrough_stage,
                    awakening_level=update.awakening_level,
                    fork_id=update.fork_id,
                    fork_level=update.fork_level,
                    fork_refinement_level=update.fork_refinement_level,
                    selected_skill_id=update.selected_skill_id,
                    skill_levels=dict(update.skill_levels),
                    ordinal=update.ordinal,
                )
        return len(updates)

    def reset_profile(self, character_id: int) -> None:
        with self._dao_factory(self._user_database_path) as dao:
            dao.reset_character_profile(int(character_id))

    def reset_all_profiles(self) -> int:
        with self._dao_factory(self._user_database_path) as dao:
            return int(dao.reset_all_character_profiles())
