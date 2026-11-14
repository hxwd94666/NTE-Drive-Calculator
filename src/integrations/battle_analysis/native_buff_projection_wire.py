# 校验并展开原生 Buff 投影的共享字符串与证据表，保留顺序和重复项。
from __future__ import annotations

from typing import Any


def expand_projection_tables(response: dict[str, Any]) -> list[dict[str, Any]]:
    """Return shared immutable-by-convention row dictionaries; no rule evaluation."""
    encoding = response.get("result_encoding")
    if encoding not in {"interned_v1", "interned_v2"}:
        raise ValueError("invalid projection encoding")
    evidence_fields = encoding == "interned_v2"
    def table(name: str) -> list:
        result = response.get(name)
        if not isinstance(result, list):
            raise ValueError("missing projection table")
        return result

    def ref(values: list, index: object):
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(values):
            raise ValueError("invalid projection table reference")
        return values[index]

    def refs(values: list, indices: object) -> list:
        if not isinstance(indices, list):
            raise ValueError("invalid projection reference list")
        return [ref(values, index) for index in indices]

    def rows(name: str, size: int):
        for row in table(name):
            if not isinstance(row, list) or len(row) != size:
                raise ValueError("invalid projection table row")
            yield row

    strings = table("strings")
    if any(not isinstance(value, str) for value in strings):
        raise ValueError("invalid projection string")
    modifiers = [{
        "property_id": ref(strings, row[0]), "additive_value": row[1],
        "interval_ids": refs(strings, row[2]), "buff_names": refs(strings, row[3]),
        "confidence": ref(strings, row[4]), "target_scope": ref(strings, row[5]),
    } for row in rows("modifiers", 6)]
    decisions = [{
        "interval_id": ref(strings, row[0]), "buff_name": ref(strings, row[1]),
        "status": ref(strings, row[2]), "applied_property_ids": refs(strings, row[3]),
        "reasons": refs(strings, row[4]),
        **({"observed_stacks": row[5], "state_confidence": ref(strings, row[6])}
           if evidence_fields else {}),
    } for row in rows("decisions", 7 if evidence_fields else 5)]
    projections = [{
        "event_id": ref(strings, row[0]), "modifiers": refs(modifiers, row[1]),
        "applied_interval_ids": refs(strings, row[2]), "excluded_interval_ids": refs(strings, row[3]),
        "exclusion_reasons": refs(strings, row[4]), "confidence": ref(strings, row[5]),
        "decisions": refs(decisions, row[6]),
    } for row in rows("projections", 7)]
    results = []
    for row in table("results"):
        if not isinstance(row, dict) or set(row) != {"job_id", "projection_index"}:
            raise ValueError("invalid projection job row")
        results.append({"job_id": row["job_id"], "projection": ref(projections, row["projection_index"])})
    return results
