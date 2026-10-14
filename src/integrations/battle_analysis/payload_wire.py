# 无损还原原生逐击文本分片，并限制展开后的总数据量。
from __future__ import annotations

from src.integrations.nte_analysis_core import NativeAnalysisError

MAX_EXPANDED_PAYLOAD_BYTES = 128 * 1024 * 1024


def restore_payloads(page: dict) -> dict:
    table = page.get("native_payload_fragments")
    if table is None:
        return page
    if not isinstance(table, list) or any(not isinstance(part, str) for part in table):
        raise NativeAnalysisError("分析核心原生证据分片表无效")
    # 预算按 UTF-8 字节累计两份快照的每次引用，不只统计分片去重后的体积。
    sizes = [len(part.encode("utf-8")) for part in table]
    used = 0
    restored = dict(page)
    for section in ("analysis", "candidate_display_analysis"):
        snapshot = page.get(section)
        if not isinstance(snapshot, dict):
            continue
        snapshot = dict(snapshot)
        for field in ("hits", "timeline_hits"):
            rows = []
            for hit in snapshot.get(field, ()):
                evidence = hit.get("native_evidence") if isinstance(hit, dict) else None
                if isinstance(evidence, dict) and "payload_fragments" in evidence:
                    indices = evidence["payload_fragments"]
                    if ("payload_json" in evidence or "reference_event_id" in evidence
                            or not isinstance(indices, list)
                            or any(type(i) is not int or not 0 <= i < len(table) for i in indices)):
                        raise NativeAnalysisError("分析核心原生证据分片引用无效")
                    used += sum(sizes[i] for i in indices)
                    if used > MAX_EXPANDED_PAYLOAD_BYTES:
                        raise NativeAnalysisError("分析核心原生证据展开超过大小限制")
                    evidence = dict(evidence)
                    del evidence["payload_fragments"]
                    evidence["payload_json"] = "".join(table[i] for i in indices)
                    hit = {**hit, "native_evidence": evidence}
                rows.append(hit)
            snapshot[field] = rows
        restored[section] = snapshot
    return restored
