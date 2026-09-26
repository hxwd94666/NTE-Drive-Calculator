# 校验并低频记录 DLL 快照分段计时，丢弃所有业务正文及未知诊断字段。
from collections import deque
from time import monotonic

from src.observability import OperationContext, log_event


STAGES = (
    "context", "clock", "feed_status", "readiness", "tick", "command_claim", "domain_keys", "domain_invalidation",
    "job_create", "job_step", "publish", "step_precheck", "reader_step", "step_postcheck",
    "character_init", "character_validate", "forks", "equipment_index", "character_rows",
    "character_base", "skills", "skill_query", "awakening", "awakening_definitions",
    "slots", "other_fields", "related_equipment", "row_finalize", "verify",
)
COUNTERS = ("calls", "total_us", "max_us", "over_8333_us", "over_20000_us")
SAMPLE_NUMBERS = ("sequence", "started_monotonic_us", "total_us", "job", "step")
DOMAINS = frozenset(("idle", "character", "inventory", "team", "environment", "equipment"))


def _integer(value):
    return type(value) is int and 0 <= value < 2**63


def _stages(value):
    if not isinstance(value, dict) or len(value) > 64:
        return None
    result = {}
    for name in STAGES:
        row = value.get(name)
        if not isinstance(row, dict) or not all(_integer(row.get(key)) for key in COUNTERS):
            continue
        result[name] = {key: row[key] for key in COUNTERS}
    return result


class SnapshotTimingLog:
    def __init__(self):
        self._last = float("-inf")
        self._previous = None
        self._seen = deque(maxlen=64)

    def observe(self, value):
        if (not isinstance(value, dict) or type(value.get("version")) is not int
                or value["version"] != 1 or monotonic() - self._last < 30):
            return
        stages = _stages(value.get("stages"))
        fields = ("slow_threshold_us", "slow_count", "dropped")
        if stages is None or not all(_integer(value.get(key)) for key in fields):
            return
        summary = {key: value[key] for key in fields}
        summary["stages"] = stages
        self._last = monotonic()
        if summary != self._previous:
            log_event("INFO", "native_sync.timing_summary", "DLL 快照分段累计耗时（含嵌套，非帧率）",
                      OperationContext.create("native_sync"), **summary)
            self._previous = summary
        samples = value.get("recent")
        if (value.get("recent_available") is not True or not isinstance(samples, list)
                or len(samples) > 9):
            return
        for row in samples:
            if not isinstance(row, dict) or not all(_integer(row.get(key)) for key in SAMPLE_NUMBERS):
                continue
            domain = row.get("domain")
            if type(domain) is not str or domain not in DOMAINS or type(row.get("all_items")) is not bool:
                continue
            parts = _stages(row.get("stages"))
            if parts is None or row["sequence"] == 0 or row["total_us"] <= value["slow_threshold_us"]:
                continue
            # Monotonic start distinguishes DLL restarts whose sequence returns to 1.
            key = (row["sequence"], row["started_monotonic_us"])
            if key in self._seen:
                continue
            safe = {name: row[name] for name in SAMPLE_NUMBERS}
            log_event("INFO", "native_sync.slow_pulse", "DLL 慢采集回调分段（有界样本，非完整逐帧记录）",
                      OperationContext.create("native_sync"), **safe, domain=domain,
                      all_items=row["all_items"], stages=parts)
            self._seen.append(key)
