# 游戏资料库正式术语与本地化的 Qt 无关数据合同。
"""Immutable contracts for localized static-catalog terminology."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal


TermStatus = Literal["complete", "name_missing"]
TermSourceKind = Literal[
    "formal_localization",
    "reviewed_annotation",
    "ui_state",
    "name_missing",
]

_TERM_SOURCE_LABELS: Mapping[str, Mapping[TermSourceKind, str]] = {
    "zh-CN": {
        "formal_localization": "游戏内正式文本",
        "reviewed_annotation": "审阅注解",
        "ui_state": "界面状态",
        "name_missing": "名称缺失",
    },
    "en-US": {
        "formal_localization": "Official in-game text",
        "reviewed_annotation": "Reviewed annotation",
        "ui_state": "Interface state",
        "name_missing": "Name unavailable",
    },
}


def term_source_label(
    source_kind: TermSourceKind,
    *,
    locale: str = "zh-CN",
) -> str | None:
    """集中投影可读来源标签；未知语言不冒用原始来源 token 作为正式名称。"""

    return _TERM_SOURCE_LABELS.get(locale, {}).get(source_kind)


@dataclass(frozen=True, slots=True)
class LocalizedTermRecord:
    """One canonical term returned by a data-backed terminology source."""

    entity_kind: str
    canonical_id: str
    names: Mapping[str, str]
    text_table: str | None = None
    text_key: str | None = None
    source_kind: TermSourceKind = "formal_localization"


@dataclass(frozen=True, slots=True)
class LocalizedTerm:
    """名称与正式身份分离；缺名称保持缺失状态，不以内部 ID 补成可见正式名。"""

    entity_kind: str
    requested_id: str
    canonical_id: str | None
    requested_locale: str
    resolved_locale: str | None
    display_name: str | None
    status: TermStatus
    text_table: str | None = None
    text_key: str | None = None
    source_kind: TermSourceKind = "name_missing"

    @property
    def name_available(self) -> bool:
        return self.status == "complete"

    @property
    def source_label(self) -> str:
        return (
            term_source_label(self.source_kind, locale=self.requested_locale)
            or term_source_label(self.source_kind, locale="zh-CN")
            or "来源名称缺失"
        )


@dataclass(frozen=True, slots=True)
class ForkCampaignRecord:
    """One formally ordered limited fork campaign."""

    pool_id: str
    featured_fork_id: str
    release_ordinal: int
    title: LocalizedTermRecord


@dataclass(frozen=True, slots=True)
class LocalizedForkCampaign:
    """Player-facing fork campaign with formal release order."""

    pool_id: str
    featured_fork_id: str
    release_ordinal: int
    title: LocalizedTerm
