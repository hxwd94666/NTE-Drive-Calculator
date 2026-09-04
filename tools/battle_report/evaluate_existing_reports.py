# 在数据库一致性副本上评估历史战报逐击拟合与逐角色边际证据。
"""Evaluate replay fit and per-character marginal evidence on saved reports."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import sys
import tempfile
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.services.battle_buff_attribute_projection_service import (
    BattleBuffAttributeProjectionService,
)
from src.services.battle_marginal_calculation_service import (
    BattleMarginalCalculationService,
)
from src.services.battle_report_history_service import BattleReportHistoryService
from src.services.battle_report_persistence_service import (
    BattleReportPersistenceDependencies,
)


_CONFIDENCE_ORDER = {"": 0, "低": 1, "中": 2, "高": 3}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="在临时数据库副本上重放历史战报并导出拟合/边际审计 JSON。",
    )
    parser.add_argument("--user-database", type=Path, required=True)
    parser.add_argument("--static-database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--record-ids",
        default="",
        help="逗号分隔的战报 ID；留空时评估全部带构筑与逐击轴的战报。",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        help="可选的旧评估 JSON；提供后输出聚合变化和逐角色边际变化。",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="并行战报批次数；每批使用独立数据库副本，默认 4。",
    )
    return parser


def _readonly_connection(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)


def _clone_database(source_path: Path, destination_path: Path) -> None:
    with _readonly_connection(source_path) as source:
        with sqlite3.connect(destination_path) as destination:
            source.backup(destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _account_id(database_path: Path) -> str:
    with _readonly_connection(database_path) as connection:
        row = connection.execute(
            "SELECT account_id FROM database_profile LIMIT 1"
        ).fetchone()
    if row is None or not str(row[0]).strip():
        raise RuntimeError("账号数据库缺少 database_profile.account_id")
    return str(row[0])


def _available_record_ids(database_path: Path) -> tuple[int, ...]:
    with _readonly_connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT record.battle_record_id
            FROM battle_record AS record
            INNER JOIN battle_build_snapshot AS build
                ON build.battle_record_id = record.battle_record_id
            WHERE record.axis_stored_hits > 0
            ORDER BY record.battle_record_id
            """
        ).fetchall()
    return tuple(int(row[0]) for row in rows)


def _requested_record_ids(raw: str, available: Sequence[int]) -> tuple[int, ...]:
    if not raw.strip():
        return tuple(available)
    requested = tuple(
        dict.fromkeys(int(token.strip()) for token in raw.split(",") if token.strip())
    )
    missing = sorted(set(requested) - set(available))
    if missing:
        raise ValueError(f"战报不存在、缺少构筑或没有逐击轴：{missing}")
    return requested


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return float(ordered[index])


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(float(value), 9)


def _replay_metrics(analysis: Any) -> dict[str, Any]:
    observed_total = 0.0
    comparable_observed = 0.0
    comparable_predicted = 0.0
    absolute_error = 0.0
    errors: list[float] = []
    confidence_counts: Counter[str] = Counter()
    formula_counts: Counter[str] = Counter()
    missing_counts: Counter[str] = Counter()
    comparable_hits = 0
    for replay in analysis.hit_replays:
        observed = max(0.0, float(replay.observed_damage))
        observed_total += observed
        confidence_counts[str(replay.confidence or "未标注")] += 1
        formula_counts[str(replay.formula_type or "未分类")] += 1
        for reason in replay.missing_evidence:
            missing_counts[str(reason)] += 1
        predicted = replay.selected_damage
        if predicted is None or observed <= 0.0:
            continue
        predicted_value = max(0.0, float(predicted))
        comparable_hits += 1
        comparable_observed += observed
        comparable_predicted += predicted_value
        absolute_error += abs(predicted_value - observed)
        errors.append(abs(predicted_value - observed) / observed * 100.0)
    return {
        "replay_hit_count": len(analysis.hit_replays),
        "comparable_hit_count": comparable_hits,
        "observed_damage": _rounded(observed_total),
        "comparable_observed_damage": _rounded(comparable_observed),
        "comparable_predicted_damage": _rounded(comparable_predicted),
        "damage_coverage_percent": _rounded(
            comparable_observed / observed_total * 100.0
            if observed_total > 0.0
            else None
        ),
        "weighted_absolute_error_percent": _rounded(
            absolute_error / comparable_observed * 100.0
            if comparable_observed > 0.0
            else None
        ),
        "signed_bias_percent": _rounded(
            (comparable_predicted - comparable_observed)
            / comparable_observed
            * 100.0
            if comparable_observed > 0.0
            else None
        ),
        "median_hit_error_percent": _rounded(
            statistics.median(errors) if errors else None
        ),
        "p90_hit_error_percent": _rounded(_percentile(errors, 0.9)),
        "within_2_percent_hits": sum(value <= 2.0 for value in errors),
        "within_5_percent_hits": sum(value <= 5.0 for value in errors),
        "within_10_percent_hits": sum(value <= 10.0 for value in errors),
        "within_20_percent_hits": sum(value <= 20.0 for value in errors),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "formula_counts": dict(sorted(formula_counts.items())),
        "missing_evidence_counts": dict(missing_counts.most_common()),
    }


def _quantification_payload(quantification: Any) -> dict[str, Any]:
    return {
        "status": quantification.status,
        "basis_damage": _rounded(quantification.basis_damage),
        "fully_quantified_damage": _rounded(
            quantification.fully_quantified_damage
        ),
        "partially_quantified_damage": _rounded(
            quantification.partially_quantified_damage
        ),
        "unavailable_damage": _rounded(quantification.unavailable_damage),
        "proven_unchanged_damage": _rounded(
            quantification.proven_unchanged_damage
        ),
        "quantified_increment": _rounded(quantification.quantified_increment),
        "gap_codes": [row.code for row in quantification.gaps],
    }


def _projection_confidence_by_character_property(analysis: Any) -> dict[
    tuple[int, str], str
]:
    result: dict[tuple[int, str], str] = {}
    for hit in analysis.hits:
        if hit.character_id is None or hit.direction != "outgoing":
            continue
        projection = BattleBuffAttributeProjectionService.project_hit(
            hit,
            analysis.buff_intervals,
        )
        for modifier in projection.modifiers:
            key = (int(hit.character_id), str(modifier.property_id))
            current = result.get(key, "高")
            if _CONFIDENCE_ORDER.get(modifier.confidence, 0) < _CONFIDENCE_ORDER.get(
                current,
                0,
            ):
                result[key] = modifier.confidence
    return result


def _marginal_payload(analysis: Any) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    replay_by_event = {row.event_id: row for row in analysis.hit_replays}
    projection_confidence = _projection_confidence_by_character_property(analysis)
    for baseline in analysis.baselines:
        units = BattleMarginalCalculationService.default_units(
            baseline,
            hits=analysis.hits,
            replays=replay_by_event,
        )
        margins = BattleMarginalCalculationService.calculate(
            analysis=analysis,
            character_id=baseline.character_id,
            edited_values={},
            units=units,
        )
        for margin in margins:
            quantification = _quantification_payload(margin.quantification)
            state_confidence = projection_confidence.get(
                (baseline.character_id, margin.property_id),
                "",
            )
            key = (
                f"record={analysis.battle_record_id}/character="
                f"{baseline.character_id}/property={margin.property_id}"
            )
            if margin.quantification.status == "complete" and state_confidence in {
                "低",
                "中",
            }:
                issues.append(
                    f"{key}: complete 使用 {state_confidence}置信 Buff 状态投影"
                )
            if (
                margin.quantification.status == "complete"
                and margin.role_denominator_status != "complete"
            ):
                issues.append(
                    f"{key}: complete 但角色分母为 {margin.role_denominator_status}"
                )
            rows.append({
                "character_id": baseline.character_id,
                "character_name": baseline.character_name,
                "property_id": margin.property_id,
                "label": margin.label,
                "unit": _rounded(margin.unit),
                "panel_value": _rounded(margin.panel_value),
                "weighted_effective_value": _rounded(
                    margin.weighted_effective_value
                ),
                "baseline_damage": _rounded(margin.baseline_damage),
                "known_projection_damage": _rounded(
                    margin.known_projection_damage
                ),
                "quantified_role_gain_percent": _rounded(
                    margin.quantified_role_gain_percent
                ),
                "full_role_gain_percent": _rounded(margin.full_role_gain_percent),
                "quantified_team_gain_percent": _rounded(
                    margin.quantified_team_gain_percent
                ),
                "full_team_gain_percent": _rounded(margin.full_team_gain_percent),
                "related_damage": _rounded(margin.related_damage),
                "role_denominator_status": margin.role_denominator_status,
                "team_denominator_status": margin.team_denominator_status,
                "buff_state_confidence": state_confidence,
                "quantification": quantification,
                "assumption": margin.assumption,
            })
    return rows, issues


def _buff_payload(analysis: Any) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    issues: list[str] = []
    for result in (*analysis.buff_counterfactuals, *analysis.passive_counterfactuals):
        if result.quantification.status == "complete" and result.confidence != "高":
            issues.append(
                f"record={analysis.battle_record_id}/buff={result.buff_key}: "
                f"complete 但机制置信度为 {result.confidence or '未标注'}"
            )
        rows.append({
            "buff_key": result.buff_key,
            "source_character_id": result.source_character_id,
            "source_character_name": result.source_character_name,
            "buff_name": result.buff_name,
            "target_scope": result.target_scope,
            "confidence": result.confidence,
            "interval_count": result.interval_count,
            "affected_hits": result.affected_hits,
            "quantified_hits": result.quantified_hits,
            "gain_percent": _rounded(result.gain_percent),
            "quantified_gain_percent": _rounded(result.quantified_gain_percent),
            "quantification": _quantification_payload(result.quantification),
            "beneficiaries": [
                {
                    "character_id": row.character_id,
                    "character_name": row.character_name,
                    "affected_hits": row.affected_hits,
                    "quantified_hits": row.quantified_hits,
                    "gain_percent": _rounded(row.recipient_gain_percent),
                    "quantified_gain_percent": _rounded(
                        row.quantified_recipient_gain_percent
                    ),
                    "quantification": _quantification_payload(row.quantification),
                }
                for row in result.beneficiaries
            ],
        })
    return rows, issues


def _evaluate_report(history: BattleReportHistoryService, record_id: int) -> dict[str, Any]:
    analysis = history.load_analysis(
        record_id,
        use_build_edit=False,
        include_buff_inference=True,
        include_hit_replays=True,
        include_buff_counterfactuals=True,
    )
    if analysis is None:
        raise RuntimeError(f"无法读取战报 {record_id}")
    evaluation_failures: list[str] = []
    try:
        marginals, marginal_issues = _marginal_payload(analysis)
    except Exception as error:  # noqa: BLE001 - 批量审计必须保留单场业务失败并继续。
        marginals = []
        marginal_issues = []
        evaluation_failures.append(
            f"逐角色边际审计失败：{type(error).__name__}: {error}"
        )
    try:
        buffs, buff_issues = _buff_payload(analysis)
    except Exception as error:  # noqa: BLE001 - 批量审计必须保留单场业务失败并继续。
        buffs = []
        buff_issues = []
        evaluation_failures.append(
            f"Buff 反事实审计失败：{type(error).__name__}: {error}"
        )
    return {
        "battle_record_id": record_id,
        "axis_complete": analysis.axis_complete,
        "formula_model_version": analysis.formula_model_version,
        "hit_replay_model_version": analysis.hit_replay_model_version,
        "buff_inference_version": analysis.buff_inference_version,
        "duration_seconds": _rounded(analysis.duration_seconds),
        "effective_damage": _rounded(analysis.effective_damage),
        "characters": [
            {
                "character_id": row.character_id,
                "character_name": row.character_name,
                "damage": _rounded(row.damage),
            }
            for row in analysis.roles
        ],
        "replay": _replay_metrics(analysis),
        "marginals": marginals,
        "buff_counterfactuals": buffs,
        "evidence_issues": [*marginal_issues, *buff_issues],
        "evaluation_failures": evaluation_failures,
    }


def _evaluate_batch(
    record_ids: Sequence[int],
    copied_database: Path,
    static_database: Path,
    account_id: str,
) -> list[dict[str, Any]]:
    dependencies = BattleReportPersistenceDependencies(
        account_id=account_id,
        user_database_path=copied_database,
        generation=0,
        static_database_path=static_database,
    )
    history = BattleReportHistoryService(
        dependencies=dependencies,
        context_is_current=lambda _dependencies: True,
    )
    reports = []
    for record_id in record_ids:
        reports.append(_evaluate_report(history, record_id))
        print(f"已评估战报 {record_id}", file=sys.stderr, flush=True)
    return reports


def _aggregate(reports: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    observed = sum(
        float(row["replay"]["comparable_observed_damage"] or 0.0)
        for row in reports
    )
    predicted = sum(
        float(row["replay"]["comparable_predicted_damage"] or 0.0)
        for row in reports
    )
    absolute_error = sum(
        float(row["replay"]["weighted_absolute_error_percent"] or 0.0)
        * float(row["replay"]["comparable_observed_damage"] or 0.0)
        / 100.0
        for row in reports
    )
    all_observed = sum(float(row["replay"]["observed_damage"] or 0.0) for row in reports)
    margin_statuses: Counter[str] = Counter()
    buff_statuses: Counter[str] = Counter()
    for report in reports:
        margin_statuses.update(
            row["quantification"]["status"] for row in report["marginals"]
        )
        buff_statuses.update(
            row["quantification"]["status"]
            for row in report["buff_counterfactuals"]
        )
    return {
        "report_count": len(reports),
        "axis_complete_report_count": sum(bool(row["axis_complete"]) for row in reports),
        "replay_hit_count": sum(row["replay"]["replay_hit_count"] for row in reports),
        "comparable_hit_count": sum(
            row["replay"]["comparable_hit_count"] for row in reports
        ),
        "comparable_observed_damage": _rounded(observed),
        "comparable_predicted_damage": _rounded(predicted),
        "damage_coverage_percent": _rounded(
            observed / all_observed * 100.0 if all_observed > 0.0 else None
        ),
        "weighted_absolute_error_percent": _rounded(
            absolute_error / observed * 100.0 if observed > 0.0 else None
        ),
        "signed_bias_percent": _rounded(
            (predicted - observed) / observed * 100.0 if observed > 0.0 else None
        ),
        "marginal_status_counts": dict(sorted(margin_statuses.items())),
        "buff_status_counts": dict(sorted(buff_statuses.items())),
        "evidence_issue_count": sum(len(row["evidence_issues"]) for row in reports),
        "evaluation_failure_count": sum(
            len(row["evaluation_failures"]) for row in reports
        ),
    }


def _marginal_index(payload: Mapping[str, Any]) -> dict[tuple[int, int, str], Any]:
    return {
        (
            int(report["battle_record_id"]),
            int(row["character_id"]),
            str(row["property_id"]),
        ): row
        for report in payload.get("reports", [])
        for row in report.get("marginals", [])
    }


def _comparison(current: Mapping[str, Any], baseline: Mapping[str, Any]) -> dict[str, Any]:
    aggregate = current["aggregate"]
    old_aggregate = baseline["aggregate"]
    current_index = _marginal_index(current)
    old_index = _marginal_index(baseline)
    changes = []
    for key in sorted(set(current_index) | set(old_index)):
        old = old_index.get(key)
        new = current_index.get(key)
        if old == new:
            continue
        changes.append({
            "battle_record_id": key[0],
            "character_id": key[1],
            "property_id": key[2],
            "before_status": None if old is None else old["quantification"]["status"],
            "after_status": None if new is None else new["quantification"]["status"],
            "before_full_role_gain_percent": (
                None if old is None else old["full_role_gain_percent"]
            ),
            "after_full_role_gain_percent": (
                None if new is None else new["full_role_gain_percent"]
            ),
            "before_quantified_role_gain_percent": (
                None if old is None else old["quantified_role_gain_percent"]
            ),
            "after_quantified_role_gain_percent": (
                None if new is None else new["quantified_role_gain_percent"]
            ),
            "before_buff_state_confidence": (
                None if old is None else old["buff_state_confidence"]
            ),
            "after_buff_state_confidence": (
                None if new is None else new["buff_state_confidence"]
            ),
        })
    return {
        "weighted_absolute_error_percent_delta": _rounded(
            float(aggregate["weighted_absolute_error_percent"] or 0.0)
            - float(old_aggregate["weighted_absolute_error_percent"] or 0.0)
        ),
        "damage_coverage_percent_delta": _rounded(
            float(aggregate["damage_coverage_percent"] or 0.0)
            - float(old_aggregate["damage_coverage_percent"] or 0.0)
        ),
        "signed_bias_percent_delta": _rounded(
            float(aggregate["signed_bias_percent"] or 0.0)
            - float(old_aggregate["signed_bias_percent"] or 0.0)
        ),
        "evidence_issue_count_delta": (
            int(aggregate["evidence_issue_count"])
            - int(old_aggregate["evidence_issue_count"])
        ),
        "marginal_changes": changes,
    }


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    user_database = args.user_database.resolve()
    static_database = args.static_database.resolve()
    if not user_database.is_file():
        raise FileNotFoundError(user_database)
    if not static_database.is_file():
        raise FileNotFoundError(static_database)
    account_id = _account_id(user_database)
    available = _available_record_ids(user_database)
    record_ids = _requested_record_ids(args.record_ids, available)
    if args.workers < 1:
        raise ValueError("--workers 必须至少为 1")
    worker_count = min(args.workers, max(1, len(record_ids)))
    with tempfile.TemporaryDirectory(prefix="nte-battle-evaluation-") as temporary:
        temporary_root = Path(temporary)
        batches = [list(record_ids[index::worker_count]) for index in range(worker_count)]
        batch_databases = []
        for index in range(worker_count):
            copied_database = temporary_root / f"user_data_{index}.sqlite3"
            _clone_database(user_database, copied_database)
            batch_databases.append(copied_database)
        reports = []
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    _evaluate_batch,
                    batch,
                    batch_databases[index],
                    static_database,
                    account_id,
                ): index
                for index, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                reports.extend(future.result())
        reports.sort(key=lambda row: int(row["battle_record_id"]))
    payload: dict[str, Any] = {
        "evaluation_schema_version": 1,
        "source": {
            "user_database_filename": user_database.name,
            "user_database_sha256": _sha256(user_database),
            "static_database_filename": static_database.name,
            "static_database_sha256": _sha256(static_database),
            "record_ids": list(record_ids),
        },
        "aggregate": _aggregate(reports),
        "reports": reports,
    }
    if args.baseline is not None:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        payload["comparison"] = _comparison(payload, baseline)
    return payload


def main() -> int:
    args = _parser().parse_args()
    payload = evaluate(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload["aggregate"], ensure_ascii=False, indent=2))
    if "comparison" in payload:
        print(json.dumps(payload["comparison"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
