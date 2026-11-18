# 以正式四人集合匹配战报范围；出伤摘要只作相关提示，不补造零伤害队友。
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from src.domain.localization.catalog import localized_text
from src.domain.battle_report.history.records import BattleReportHistoryEntry
from src.domain.teams.models import Team


class MatchStatus(StrEnum):
    PENDING = "pending"
    MATCHED = "matched"
    DIFFERENT = "different"
    INSUFFICIENT = "insufficient"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


STATUS_LABELS = {
    MatchStatus.PENDING: "待核验", MatchStatus.MATCHED: "阵容匹配 · 增强输入待检查",
    MatchStatus.DIFFERENT: "阵容不同", MatchStatus.INSUFFICIENT: "证据不足",
    MatchStatus.CONFLICT: "范围冲突", MatchStatus.UNAVAILABLE: "核验组件未就绪", MatchStatus.ERROR: "核验故障",
}


@dataclass(frozen=True, slots=True)
class ReportRange:
    record_id: int
    scope: str = "all"
    start_us: int | None = None
    end_us: int | None = None

    def __post_init__(self):
        if type(self.record_id) is not int or self.record_id <= 0 or self.scope not in {"all", "first", "second", "range"}:
            raise ValueError("战报范围身份无效")
        if self.scope == "range":
            if type(self.start_us) is not int or type(self.end_us) is not int or not 0 <= self.start_us < self.end_us:
                raise ValueError("请输入有效的原轴开始和结束时刻")
        elif self.start_us is not None or self.end_us is not None:
            raise ValueError("非指定时段不能携带另一份时间范围")

    @property
    def label(self):
        if self.scope == "range":
            return localized_text(None, 'auto.domain.teams.report_matching.label.f66d5c297', '指定时段 {value}–{value_2} 秒', value=f'{self.start_us / 1_000_000:g}', value_2=f'{self.end_us / 1_000_000:g}')
        return {"all": "整场", "first": "上半场", "second": "下半场"}[self.scope]

    def selector_fields(self):
        return dict(battle_record_id=self.record_id, start_us=self.start_us, end_us=self.end_us,
                    detail_scope=self.scope if self.scope in {"first", "second"} else None)


@dataclass(frozen=True, slots=True)
class ReportCandidate:
    entry: BattleReportHistoryEntry
    range: ReportRange


@dataclass(frozen=True, slots=True)
class TeamReportMatch:
    team: Team
    candidate: ReportCandidate
    status: MatchStatus
    reason: str = ""
    report_character_ids: tuple[int, ...] = ()
    source_input_digest: str | None = None
    input_identity: tuple = ()


@dataclass(frozen=True, slots=True)
class ReportDiscovery:
    teams: tuple[Team, ...]
    candidates: tuple[ReportCandidate, ...]
    next_offset: int | None = None


@dataclass(frozen=True, slots=True)
class MatchPage:
    results: tuple[TeamReportMatch, ...]
    next_offset: int
    total: int


def discover_candidates(entries):
    def order(entry):
        try:
            saved = datetime.fromisoformat(entry.saved_at_utc.replace("Z", "+00:00")).timestamp()
        except (ValueError, OSError):
            saved = 0
        return saved, entry.battle_record_id
    result = []
    for entry in sorted(entries, key=order, reverse=True):
        scopes = [key for key, exists in (("first", entry.has_first_half), ("second", entry.has_second_half)) if exists]
        for scope in scopes or ["all"]:
            result.append(ReportCandidate(entry, ReportRange(entry.battle_record_id, scope)))
    return tuple(result)


def classify_evidence(team, candidate, evidence, *, input_identity=()):
    ids = tuple(evidence["team_character_ids"])
    reason = evidence.get("reason")
    if len(team.character_ids) != 4:
        status, message = MatchStatus.INSUFFICIENT, "请先补齐队伍的四名正式成员"
    elif len(ids) == 4:
        status = MatchStatus.MATCHED if evidence["ready"] and set(ids) == set(team.character_ids) else MatchStatus.DIFFERENT
        message = "" if status == MatchStatus.MATCHED else "正式冻结阵容与所选队伍不同，请检查差异成员"
    elif reason in {"axis_team_changed", "axis_team_scope_conflict"}:
        status, message = MatchStatus.CONFLICT, "范围内队伍变化或半场冲突，请选择半场或缩小范围"
    else:
        status, message = MatchStatus.INSUFFICIENT, "缺少该范围的正式完整四人队伍证据"
    return TeamReportMatch(team, candidate, status, message, ids, evidence.get("source_input_digest"), input_identity)
