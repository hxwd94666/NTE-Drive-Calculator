# 账号级分配候选过滤设置及其纯过滤规则。
"""Allocation candidate filtering applied before per-role preferences."""

from __future__ import annotations

from src.i18n import display_term, tr

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.storage.sqlite.user_data_dao import UserDataDao


ALLOCATION_QUALITIES = frozenset({"Blue", "Purple", "Gold"})
ALLOCATION_ITEM_TYPES = frozenset({"tape", "drive"})
_SETTING_KEY = "allocation_filter"


class AllocationFilterValidationError(ValueError):
    """The account filter cannot form an unambiguous calculation request."""


@dataclass(frozen=True)
class AllocationFilterSettings:
    """Immutable quality filter and the equipment types it applies to.

    Types not present in ``item_types`` bypass the quality filter and continue
    through the established per-role candidate rules unchanged.
    """

    qualities: frozenset[str] = frozenset()
    item_types: frozenset[str] = frozenset()

    def validate(self) -> None:
        unknown_qualities = self.qualities.difference(ALLOCATION_QUALITIES)
        if unknown_qualities:
            raise AllocationFilterValidationError(
                tr("未知分配品质：{values}",
                   values=tr("、").join(display_term(v) for v in sorted(unknown_qualities)))
            )
        unknown_types = self.item_types.difference(ALLOCATION_ITEM_TYPES)
        if unknown_types:
            raise AllocationFilterValidationError(
                tr("未知分配类型：{values}",
                   values=tr("、").join(display_term(v) for v in sorted(unknown_types)))
            )
        if self.item_types and not self.qualities:
            raise AllocationFilterValidationError(
                tr("选择分配类型后，必须至少选择一种分配品质。")
            )

    def to_payload(self) -> dict[str, list[str]]:
        self.validate()
        return {
            "qualities": sorted(self.qualities),
            "item_types": sorted(self.item_types),
        }

    @classmethod
    def from_payload(cls, value: Mapping[str, Any] | None) -> "AllocationFilterSettings":
        raw = value or {}
        qualities = raw.get("qualities", ())
        item_types = raw.get("item_types", ())
        if not isinstance(qualities, (list, tuple, set, frozenset)):
            raise AllocationFilterValidationError(tr("分配品质设置必须是列表。"))
        if not isinstance(item_types, (list, tuple, set, frozenset)):
            raise AllocationFilterValidationError(tr("分配类型设置必须是列表。"))
        settings = cls(
            qualities=frozenset(str(value).strip() for value in qualities),
            item_types=frozenset(str(value).strip() for value in item_types),
        )
        settings.validate()
        return settings


def filter_allocation_candidates(
    candidates: Sequence[Mapping[str, Any]],
    settings: AllocationFilterSettings,
) -> tuple[Mapping[str, Any], ...]:
    """Apply the global quality rule once, before any role-specific rules."""

    settings.validate()
    if not settings.item_types:
        return tuple(candidates)
    return tuple(
        candidate
        for candidate in candidates
        if str(candidate.get("item_type") or "") not in settings.item_types
        or str(candidate.get("quality") or "") in settings.qualities
    )


class AllocationFilterSettingsService:
    """Persist the active account's calculation preference without UI state."""

    def __init__(self, user_database_path: str | Path) -> None:
        self.user_database_path = Path(user_database_path).expanduser().resolve()

    def load(self) -> AllocationFilterSettings:
        with UserDataDao(self.user_database_path) as dao:
            value = dao.list_application_setting_copies().get(_SETTING_KEY)
        return AllocationFilterSettings.from_payload(value)

    def save(self, settings: AllocationFilterSettings) -> AllocationFilterSettings:
        payload = settings.to_payload()
        with UserDataDao(self.user_database_path) as dao:
            if settings == AllocationFilterSettings():
                dao.delete_application_setting_copy(_SETTING_KEY)
            else:
                dao.replace_application_setting_copy(_SETTING_KEY, payload)
        return settings
