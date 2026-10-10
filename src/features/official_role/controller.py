# 编排角色索引、详情、养成保存和重置并记录账号关联日志。
"""Controller boundary for the official-role vertical slice."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from collections import OrderedDict
from copy import deepcopy
from time import perf_counter
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal

from src.app.page_tasks import PageCommitLane, PageRequest, PageTaskLane
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
from src.services.world_bonus_settings_service import (
    WorldBonusSettings,
    WorldBonusSettingsService,
)
from src.services.official_role_inventory_contexts import load_role_replacement_detail
from src.services.official_role_replacement_service import replacement_candidates_for_official_role
from src.services.workshop_weight_template_service import configured_workshop_weight_template_file
from src.integrations.bundled_resources import bundled_config_dir


class OfficialRoleController(QObject):
    """Expose account-pinned role operations to the Qt page."""

    changed = Signal()

    def __init__(self, dependencies: OfficialRoleDependencies, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.dependencies = dependencies
        self._reads = PageTaskLane(self)
        self._writes = PageCommitLane(self)
        self._closed = False
        self._cache_key = None
        self._request_cache: dict[object, Any] = {}
        self._details: OrderedDict[int, dict] = OrderedDict()
        self._index_model = None
        self._epoch = 0
        self._profile_service = OfficialRoleProfileService(
            dependencies.user_database_path
        )
        self._world_bonus_service = WorldBonusSettingsService(
            dependencies.user_database_path
        )

    def is_writing(self) -> bool:
        return self._writes.is_running()

    def submit_change(self, work, committed, failed, application_failed) -> bool:
        if self._closed or self.is_writing():
            return False
        self._epoch += 1
        self._reads.cancel()

        def done(value):
            self._cache_key = None
            self.changed.emit()
            committed(value)

        return self._writes.submit(work, done, failed, application_failed)

    def when_commit_settled(self, callback) -> None:
        self._writes.settled.connect(callback, Qt.ConnectionType.SingleShotConnection)

    def cancel_reads(self) -> None:
        self._reads.cancel()

    def source_key(self) -> tuple:
        """Conservative file probes; scoped to the frozen account and resource identity."""
        def stamp(path):
            try:
                info = path.stat()
                return info.st_ino, info.st_size, info.st_mtime_ns
            except OSError:
                return None

        paths = (self.dependencies.user_database_path, self.dependencies.static_database_path,
                 self.dependencies.shared_database_path)
        template = configured_workshop_weight_template_file()
        return (self.dependencies, tuple((stamp(path), stamp(path.with_name(path.name + "-wal"))) for path in paths),
                stamp(self.dependencies.asset_root / "manifest.json") if self.dependencies.asset_root else None,
                stamp(template) if template is not None else None,
                stamp(bundled_config_dir() / "gameplay_effect_semantics.json"))

    def _prepare_cache(self) -> None:
        key = self.source_key()
        if key != self._cache_key:
            self._cache_key = key
            self._request_cache = {}
            self._details.clear()
            self._index_model = None

    def _stable_read(self, read):
        # File probing belongs on the worker too: stat() can block on slow drives.
        for _attempt in range(2):
            self._prepare_cache()
            before = self._cache_key
            result = read()
            if self.source_key() == before:
                return result, before
            self._cache_key = None
        raise ValueError("角色资料正在更新，请稍候重新进入角色页。")

    def request_index(self, apply, failed) -> None:
        if self._closed or self.is_writing():
            return
        def read():
            def model():
                if self._index_model is None:
                    self._index_model = self.load_index(), self.load_world_bonus()
                return self._index_model
            (roles, settings), key = self._stable_read(model)
            return deepcopy(roles), settings, key
        self._reads.submit(PageRequest(("index", self.dependencies, self._epoch), read, apply, failed))

    def request_detail(self, character_id: int, apply, failed, discarded=lambda: None) -> None:
        if self._closed or self.is_writing():
            discarded()
            return
        def read():
            def model():
                if character_id not in self._details:
                    self._details[character_id] = self.load_detail(character_id, include_replacement_candidates=False)
                self._details.move_to_end(character_id)
                while len(self._details) > 8:
                    self._details.popitem(last=False)
                return deepcopy(self._details[character_id])
            return self._stable_read(model)
        self._reads.submit(PageRequest(("detail", character_id, self.dependencies, self._epoch), read, apply, failed, discarded))

    def is_loading(self) -> bool:
        return self._reads.is_running()

    def request_replacement(self, detail, context_key, target, apply, failed) -> None:
        """Prepare a selected snapshot and rank it off the UI thread, without saving."""
        frozen_detail, frozen_target = deepcopy(detail), deepcopy(target)
        def read():
            source_key = self.source_key()
            if detail.get("_view_source_key", source_key) != source_key:
                raise ValueError("角色或配装资料已更新，请重新加载后再选择替换。")
            enriched = load_role_replacement_detail(
                self.dependencies.user_database_path, self.dependencies.static_database_path,
                self.dependencies.asset_root, frozen_detail, context_key,
            )
            candidates = replacement_candidates_for_official_role(enriched, context_key, frozen_target)
            if self.source_key() != source_key:
                raise ValueError("角色或配装资料正在更新，请重新加载后再选择替换。")
            return enriched, candidates

        key = ("replacement", int(detail["character"]["character_id"]), context_key,
               target.get("uid_serial"), target.get("uid_slot"), self._epoch)
        self._reads.submit(PageRequest(key, read, apply, failed))

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._reads.close()
        if self._reads.is_running():
            self._reads.idle.connect(self._release_cache, Qt.ConnectionType.SingleShotConnection)
        else:
            self._release_cache()

    def _release_cache(self) -> None:
        self._request_cache.clear()
        self._details.clear()
        self._index_model = None

    def when_reads_idle(self, callback) -> None:
        self._reads.idle.connect(callback, Qt.ConnectionType.SingleShotConnection)

    def _operation(self, job_id: int | str | None = None) -> OperationContext:
        return OperationContext.create(
            "official_role",
            account_id=self.dependencies.account_id,
            context_generation=self.dependencies.generation,
            job_id=job_id,
        )

    def load_index(self) -> list[dict]:
        started = perf_counter()
        with operation_scope(
            self._operation(),
            started_event="role.index_load_started",
            succeeded_event="role.index_load_succeeded",
            failed_event="role.index_load_failed",
            message="加载角色索引",
        ) as span:
            # 单独记录进入业务前的日志输出等待，不将控制台阻塞误判为查库慢。
            start_log_ms = round((perf_counter() - started) * 1000, 3)
            stages: dict[str, float] = {}
            roles = load_official_role_index(
                self.dependencies.user_database_path, stage_duration_ms=stages,
                static_database_path=self.dependencies.static_database_path,
                asset_root=self.dependencies.asset_root,
            )
            span.annotate(
                role_count=len(roles), start_log_ms=start_log_ms,
                stage_duration_ms=stages,
            )
            return roles

    def load_detail(self, character_id: int, *, include_replacement_candidates: bool = True) -> dict:
        self._prepare_cache()
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
                static_database_path=self.dependencies.static_database_path,
                asset_root=self.dependencies.asset_root,
                shared_database_path=self.dependencies.shared_database_path,
                request_cache=self._request_cache,
                include_replacement_candidates=include_replacement_candidates,
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

    def load_world_bonus(self) -> WorldBonusSettings:
        return self._world_bonus_service.load()

    def save_world_bonus(
        self, settings: WorldBonusSettings,
    ) -> WorldBonusSettings:
        with operation_scope(
            self._operation(),
            started_event="role.world_bonus_save_started",
            succeeded_event="role.world_bonus_save_succeeded",
            failed_event="role.world_bonus_save_failed",
            message="保存家具加成",
        ):
            return self._world_bonus_service.save(settings)

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
        context_key: str = "saved",
        replacement_score: float,
        current_assignment_scores: Mapping[str, float],
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
                context_key=context_key,
                replacement_score=float(replacement_score),
                current_assignment_scores=current_assignment_scores,
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
