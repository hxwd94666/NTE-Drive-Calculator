# 调度 DOT 与残虹蓄焰状态计算并恢复原有中文证据。
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.domain.battle_report.analysis.backend_contracts import BattleComputeBackend
from src.services.battle_report.mechanics.dot_evidence import (
    BattleDotStackState, DOT_KIND_NAMES, dot_basis, dot_final_basis,
)


def compute_hit_state(
    kind: str, inputs: dict[str, Any], *, backend: BattleComputeBackend,
    checkpoint: Callable[[], None] | None,
) -> list[dict[str, Any]]:
    if checkpoint:
        checkpoint()
    results = backend.compute_batch("hit_state_v1", ({**inputs, "kind": kind},),
                                    checkpoint=checkpoint)
    if len(results) != 1 or not isinstance(results[0].get("states"), list):
        raise ValueError("invalid_hit_state_result")
    rows = results[0]["states"]
    if not all(isinstance(row, dict) and isinstance(row.get("event_id"), str) for row in rows):
        raise ValueError("invalid_hit_state_result")
    return rows


def restore_dot_states(rows: list[dict[str, Any]]) -> dict[str, Any]:
    results = {}
    for row in rows:
        kind = row.get("kind")
        if (
            kind not in DOT_KIND_NAMES or type(row.get("coefficient")) is not int
            or not 0 <= row["coefficient"] <= 10
            or type(row.get("active_dot_kind_count")) is not int
            or not 0 <= row["active_dot_kind_count"] <= 4
            or row.get("confidence") not in ("未解析", "中", "低")
            or not all(type(row.get(flag)) is bool for flag in ("lower", "early", "final_enabled", "final_active"))
            or type(row.get("dot_final_multiplier")) not in (int, float)
            or row["dot_final_multiplier"] not in (1.0, 1.25, 1.5, 1.75, 2.0)
            or not isinstance(row.get("effect"), str)
            or not all(isinstance(row.get(field), list) and all(value in DOT_KIND_NAMES for value in row[field])
                       for field in ("active_kinds", "recent_only"))
        ):
            raise ValueError("invalid_hit_state_result")
        label = (
            "三觉：噩梦提前结算" if row["early"] else
            f"{DOT_KIND_NAMES[kind]}观测下限" if row["lower"] else
            "浊燃结算前层数" if kind == "scorch" else
            f"{DOT_KIND_NAMES[kind]}当前状态" if kind in ("cang_field", "adler_skill") else
            f"{DOT_KIND_NAMES[kind]}当前层数"
        )
        results[row["event_id"]] = BattleDotStackState(
            event_id=row["event_id"], coefficient=row["coefficient"], label=label,
            confidence=row["confidence"], evidence_basis=dot_basis(row),
            active_dot_kind_count=row["active_dot_kind_count"],
            dot_final_multiplier=row["dot_final_multiplier"], dot_final_multiplier_basis=dot_final_basis(row),
        )
    return results


def restore_q_final_states(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from src.services.battle_report.characters.zankou import ZankouQFinalDamageEvidence
    results = {}
    for row in rows:
        if row.get("branch") not in ("magic", "force") or row.get("multiplier") != 2.5:
            raise ValueError("invalid_hit_state_result")
        branch = row["branch"]
        name = "血宴入梦时" if branch == "magic" else "焚天烬灭舞"
        results[row["event_id"]] = ZankouQFinalDamageEvidence(
            event_id=row["event_id"], multiplier=row["multiplier"],
            evidence_basis=(
                "觉醒二「渊底之吻」蓄焰：覆纹/浊燃触发后，"
                f"本次{name}独立最终伤害 +150%，即 ×2.5"
                + ("；觉醒四「梦魇生花」使血宴分支独立持有并消费资格" if branch == "magic" else "")
            ),
        )
    return results
