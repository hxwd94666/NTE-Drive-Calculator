# 编排角色索引、详情、养成保存和重置并记录账号关联日志。
"""Controller boundary for the official-role vertical slice."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.features.official_role.dependencies import OfficialRoleDependencies
from src.observability.context import OperationContext
from src.observability.operation import log_event, operation_scope
from src.services.official_role_page_service import (
    load_official_role_detail,
    load_official_role_index,
    save_official_role_replacement,
    save_official_role_tab_order,
)
from src.services.official_role_profile_service import (
    OfficialRoleProfileService,
    OfficialRoleProfileUpdate,
)


class OfficialRoleController:
    """Expose account-pinned role operations to the Qt page."""

    def __init__(self, dependencies: OfficialRoleDependencies) -> None:
        self.dependencies = dependencies
        self._profile_service = OfficialRoleProfileService(
            dependencies.user_database_path
        )

    def _operation(self, job_id: int | str | None = None) -> OperationContext:
        return OperationContext.create(
            "official_role",
            account_id=self.dependencies.account_id,
            context_generation=self.dependencies.generation,
            job_id=job_id,
        )

    def load_index(self) -> list[dict]:
        with operation_scope(
            self._operation(),
            started_event="role.index_load_started",
            succeeded_event="role.index_load_succeeded",
            failed_event="role.index_load_failed",
            message="加载角色索引",
        ) as span:
            roles = load_official_role_index(
                self.dependencies.user_database_path
            )
            span.annotate(role_count=len(roles))
            return roles

    def load_detail(self, character_id: int) -> dict:
        with operation_scope(
            self._operation(character_id),
            started_event="role.detail_load_started",
            succeeded_event="role.detail_load_succeeded",
            failed_event="role.detail_load_failed",
            message="加载角色详情",
            character_id=int(character_id),
        ):
            return load_official_role_detail(
                self.dependencies.user_database_path,
                int(character_id),
            )

    def save_profiles(
        self, updates: Sequence[OfficialRoleProfileUpdate]
    ) -> int:
        with operation_scope(
            self._operation(),
            started_event="role.profile_save_started",
            succeeded_event="role.profile_save_succeeded",
            failed_event="role.profile_save_failed",
            message="保存角色养成配置",
            character_count=len(updates),
        ) as span:
            saved_count = self._profile_service.save_profiles(updates)
            span.annotate(saved_count=saved_count)
            return saved_count

    def reset_profile(self, character_id: int) -> None:
        with operation_scope(
            self._operation(character_id),
            started_event="role.profile_reset_started",
            succeeded_event="role.profile_reset_succeeded",
            failed_event="role.profile_reset_failed",
            message="重置当前角色养成配置",
            character_id=int(character_id),
        ):
            self._profile_service.reset_profile(character_id)

    def reset_all_profiles(self) -> int:
        with operation_scope(
            self._operation(),
            started_event="role.profiles_reset_started",
            succeeded_event="role.profiles_reset_succeeded",
            failed_event="role.profiles_reset_failed",
            message="重置全部角色养成配置",
        ) as span:
            reset_count = self._profile_service.reset_all_profiles()
            span.annotate(reset_count=reset_count)
            return reset_count

    def save_tab_order(self, character_ids: Sequence[int]) -> list[int]:
        result = save_official_role_tab_order(
            self.dependencies.user_database_path,
            character_ids,
        )
        log_event(
            "DEBUG",
            "role.tab_order_saved",
            "角色页签顺序已保存",
            self._operation(),
            character_count=len(result),
        )
        return result

    def save_replacement(
        self,
        detail: Mapping[str, Any],
        target: Mapping[str, Any],
        replacement: Mapping[str, Any],
        *,
        replacement_score: float,
        current_score: float,
    ) -> None:
        character_id = int((detail.get("character") or {})["character_id"])
        with operation_scope(
            self._operation(character_id),
            started_event="role.replacement_save_started",
            succeeded_event="role.replacement_save_succeeded",
            failed_event="role.replacement_save_failed",
            message="保存角色替换优化结果",
            character_id=character_id,
        ):
            save_official_role_replacement(
                self.dependencies.user_database_path,
                detail,
                target,
                replacement,
                replacement_score=float(replacement_score),
                current_score=float(current_score),
            )

    def log_dirty_exit(self, action: str, dirty_count: int) -> None:
        log_event(
            "INFO",
            "role.dirty_exit_decided",
            "处理角色页面未保存修改",
            self._operation(),
            action=action,
            dirty_character_count=int(dirty_count),
        )
