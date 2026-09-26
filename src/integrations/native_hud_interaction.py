# 白名单记录 HUD 与角色读取的有界交互诊断，不接受对象、账号或技能正文。
from collections import deque
from time import monotonic

from src.observability import OperationContext, log_event


STAGES = frozenset(("control", "character", "skill_query", "hud"))
EVENTS = frozenset(("awakening_changed", "hud_options", "begin", "end", "fault",
                    "control_busy", "snapshot_cancel", "skill_selection"))
SELECTION_REASONS = (
    "unavailable", "sdk_incompatible", "ui_call_unreadable", "ui_call_mismatch",
    "selector_unreadable", "selector_mismatch", "weight_unreadable", "weight_mismatch", "invalid_ability",
    "invalid_instances", "invalid_instance", "weighted_selection_unavailable", "return_buffer", "native_no_handle",
    "duplicate_handle", "native_current_spec", "handle_not_in_actor_input", "ui_binding", "unique_spec",
    "ambiguous_ui_binding", "no_candidate", "configured_no_handle",
)
NUMBERS = ("sequence", "at_monotonic_us", "duration_us", "detail", "active_mask", "hud_options")


def _number(value):
    return type(value) is int and 0 <= value < 2**63


class HudInteractionLog:
    def __init__(self):
        self._last = float("-inf")
        self._summary_at = float("-inf")
        self._seen = deque(maxlen=128)
        self._state = None

    def observe(self, value):
        if (not isinstance(value, dict) or type(value.get("version")) is not int
                or value["version"] != 1 or monotonic() - self._last < 1):
            return
        fields = ("observed_monotonic_us", "hud_options", "active_mask", "dropped", "latest_sequence")
        if (not all(_number(value.get(key)) for key in fields)
                or value["hud_options"] > 31 or value["active_mask"] > 15):
            return
        self._last = monotonic()
        stages = value.get("stages")
        safe_stages = {}
        if isinstance(stages, dict):
            for name in sorted(STAGES):
                row = stages.get(name)
                if isinstance(row, dict) and all(_number(row.get(key)) for key in ("calls", "active", "faults")):
                    safe_stages[name] = {key: row[key] for key in ("calls", "active", "faults")}
        # A state/fault change is immediate on observation; cumulative call counts
        # alone are periodic. This adds no status polling or RPCs of its own.
        state = (value["hud_options"], value["active_mask"], value["dropped"],
                 tuple((name, row["faults"]) for name, row in safe_stages.items()))
        if state != self._state or self._last - self._summary_at >= 30:
            log_event("INFO", "native_sync.hud_interaction", "HUD 与角色采集交互状态（重叠不等于竞争）",
                      OperationContext.create("native_sync"),
                      **{key: value[key] for key in fields}, stages=safe_stages)
            self._state, self._summary_at = state, self._last
        recent = value.get("recent")
        if value.get("recent_available") is not True or not isinstance(recent, list) or len(recent) > 32:
            return
        for row in recent:
            if not isinstance(row, dict) or not all(_number(row.get(key)) for key in NUMBERS):
                continue
            event, stage = row.get("event"), row.get("stage")
            if (type(event) is not str or event not in EVENTS or type(stage) is not str or stage not in STAGES
                    or not row["sequence"] or row["hud_options"] > 31 or row["active_mask"] > 15
                    or row["detail"] > 0xFFFFFFFF or row["at_monotonic_us"] > value["observed_monotonic_us"]):
                continue
            key = row["sequence"], row["at_monotonic_us"]
            if key in self._seen:
                continue
            selection = {}
            if event == "skill_selection":
                if (not _number(row.get("input")) or row["input"] > 1
                        or not _number(row.get("candidates")) or row["candidates"] > 2048
                        or row["detail"] >= len(SELECTION_REASONS)):
                    continue
                selection = dict(input="Q" if row["input"] else "E", candidates=row["candidates"],
                                 reason=SELECTION_REASONS[row["detail"]])
            log_event("WARNING" if event == "fault" else "INFO", "native_sync.hud_interaction_event",
                      "HUD 与角色采集交互事件（有界采样）", OperationContext.create("native_sync"),
                      interaction_event=event, stage=stage, **selection, **{name: row[name] for name in NUMBERS})
            self._seen.append(key)
