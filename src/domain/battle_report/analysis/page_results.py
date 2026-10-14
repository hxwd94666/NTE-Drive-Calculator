# 按冻结事件身份查询原生共享详情表，并保存一次页面加载的只读结果。
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping
from src.domain.battle_report.analysis.snapshot import BattleAnalysisSnapshot
from src.domain.battle_report.buffs.models import (
    BattleBuffProjectionDecision, BattleHitBuffProjection, BattleProjectedBuffModifier,
)
from src.domain.battle_report.counterfactual.results import BattleMarginalBenefits, BattleMarginalPanelResult
from src.domain.battle_report.timeline.events import BattleAnalysisHit


@dataclass(frozen=True, slots=True)
class BattleHitDetailTables:
    strings: tuple[str, ...]
    modifiers: tuple[BattleProjectedBuffModifier, ...]
    decisions: tuple[BattleBuffProjectionDecision, ...]
    projections: tuple[tuple, ...]
    jobs: Mapping[str, int]

    def projection(self, job: str) -> BattleHitBuffProjection | None:
        index = self.jobs.get(job)
        if index is None:
            return None
        row = self.projections[index]
        strings = self.strings
        return BattleHitBuffProjection(
            strings[row[0]], tuple(self.modifiers[i] for i in row[1]),
            tuple(strings[i] for i in row[2]), tuple(strings[i] for i in row[3]),
            tuple(strings[i] for i in row[4]), strings[row[5]],
            tuple(self.decisions[i] for i in row[6]),
        )


class BattleHitDetailLookup:
    """绑定冻结分析与共享表的一次性页投影；事件 ID 和时间须同时匹配。"""
    def __init__(self, tables: BattleHitDetailTables, section: str,
                 analysis: BattleAnalysisSnapshot | None):
        self._tables, self._section = tables, section
        self._hits = {hit.event_id: hit for hit in (
            () if analysis is None else (*analysis.timeline_hits, *analysis.hits))}
        self._intervals = {row.interval_id: row for row in (
            () if analysis is None else (*analysis.timeline_buff_intervals, *analysis.buff_intervals))}

    def for_hit(self, hit: BattleAnalysisHit, *, formula: bool):
        frozen = self._hits.get(hit.event_id)
        if frozen is None or frozen.relative_time_us != hit.relative_time_us:
            return None, ()
        kind = "formula" if formula else "raw"
        projection = self._tables.projection(f"{self._section}:{kind}:{hit.event_id}")
        if projection is None:
            return None, ()
        intervals = tuple(self._intervals[row.interval_id] for row in projection.decisions
                          if row.interval_id in self._intervals)
        return projection, intervals


@dataclass(frozen=True, slots=True)
class BattlePageHitDetails:
    analysis: BattleHitDetailLookup
    candidate: BattleHitDetailLookup


@dataclass(frozen=True, slots=True)
class BattleReportAnalysisLoadResult:
    analysis: BattleAnalysisSnapshot | None
    target_catalog: dict[str, object] | None
    target_catalog_error: Exception | None = None
    marginal_benefits: BattleMarginalBenefits | None = None
    marginal_panel: BattleMarginalPanelResult | None = None
    candidate_display_analysis: BattleAnalysisSnapshot | None = None
    hit_details: BattlePageHitDetails | None = None
