# 持久化当前账号的角色养成指针并提供明确的重置边界。
"""Account-scoped official-role profile write service."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from src.storage.sqlite.user_data_dao import UserDataDao
from src.storage.sqlite.native_character_profile_dao import normalize_native_profile_patches
from src.services.native_role_profile_projection import load_template_growth_defaults


@dataclass(frozen=True, slots=True)
class OfficialRoleProfileUpdate:
    character_id: int
    character_level: int
    breakthrough_stage: int
    awakening_level: int
    selected_awaken_effect_ids: tuple[str, ...]
    likeability_level_10_enabled: bool
    fork_id: str | None
    fork_level: int | None
    fork_breakthrough_stage: int | None
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
        static_database_path: str | Path | None = None,
        growth_defaults_loader: Callable[[tuple[int, ...]], Mapping[int, Mapping[str, int]]] | None = None,
    ) -> None:
        self._user_database_path = Path(user_database_path)
        self._dao_factory = dao_factory
        self._growth_defaults_loader = growth_defaults_loader
        self._static_database_path = Path(static_database_path).resolve() if static_database_path is not None else None

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
                    selected_awaken_effect_ids=update.selected_awaken_effect_ids,
                    awakening_selection_initialized=True,
                    likeability_level_10_enabled=(
                        update.likeability_level_10_enabled
                    ),
                    fork_id=update.fork_id,
                    fork_level=update.fork_level,
                    fork_breakthrough_stage=update.fork_breakthrough_stage,
                    fork_refinement_level=update.fork_refinement_level,
                    selected_skill_id=update.selected_skill_id,
                    skill_levels=dict(update.skill_levels),
                    ordinal=update.ordinal,
                )
        return len(updates)

    def patch_native_profiles(
        self, profiles: Sequence[Mapping[str, object]], *, check: Callable[[], None],
    ) -> int:
        """Apply observed cultivation with a final in-transaction context guard."""
        check()
        patches = normalize_native_profile_patches(profiles)
        character_ids = tuple(patch["character_id"] for patch in patches)
        if self._growth_defaults_loader is not None:
            defaults = self._growth_defaults_loader(character_ids)
        elif self._static_database_path is not None:
            defaults = load_template_growth_defaults(character_ids, static_database_path=self._static_database_path)
        else:
            raise ValueError("角色状态同步缺少本次冻结的静态数据库路径")
        check()
        if self._static_database_path is not None:
            from src.services.native_role_profile_projection import validate_native_cultivation
            validate_native_cultivation(patches, static_database_path=self._static_database_path)
        with self._dao_factory(self._user_database_path) as dao:
            return dao.patch_native_character_profiles(patches, growth_defaults=defaults, check=check)


    def reset_profile(self, character_id: int) -> None:
        with self._dao_factory(self._user_database_path) as dao:
            dao.reset_character_profile(int(character_id))

    def reset_all_profiles(self) -> int:
        with self._dao_factory(self._user_database_path) as dao:
            return int(dao.reset_all_character_profiles())
